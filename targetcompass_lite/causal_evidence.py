import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


GENETIC_EVIDENCE_TYPES = {
    "gwas_association": "association",
    "qtl_colocalization": "coloc",
    "eqtl_colocalization": "coloc",
    "pqtl_colocalization": "coloc",
    "mendelian_randomization": "mr",
    "opentargets_association": "association",
}


def grade_causal_evidence(project_dir: Path) -> Path:
    db = project_dir / "evidence.sqlite"
    if not db.exists():
        raise ValueError("evidence.sqlite is required before causal grading")
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in GENETIC_EVIDENCE_TYPES)
    rows = [
        dict(row)
        for row in con.execute(
            f"SELECT * FROM evidence_item WHERE evidence_type IN ({placeholders}) ORDER BY entity_symbol, evidence_type",
            tuple(GENETIC_EVIDENCE_TYPES),
        ).fetchall()
    ]
    con.close()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("evidence_type") in GENETIC_EVIDENCE_TYPES:
            grouped[row["entity_symbol"]].append(row)
    out_dir = project_dir / "results" / "causal_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "causal_evidence_grades.tsv"
    grades = []
    for gene, evidences in grouped.items():
        evidence_types = {row["evidence_type"] for row in evidences}
        methods = {GENETIC_EVIDENCE_TYPES[row["evidence_type"]] for row in evidences}
        best_p = min([row["p_value"] for row in evidences if row.get("p_value") is not None] or [None], key=lambda value: value is None or value)
        grade, rationale = _grade(methods, evidence_types, best_p)
        grades.append(
            {
                "gene_symbol": gene,
                "causal_grade": grade,
                "methods": ";".join(sorted(methods)),
                "evidence_types": ";".join(sorted(evidence_types)),
                "evidence_count": len(evidences),
                "best_p_value": "" if best_p is None else f"{best_p:.6g}",
                "rationale": rationale,
                "limitation": "Automated causal grade is a triage label; locus mapping, LD, pleiotropy, and ancestry matching require human/statistical review.",
            }
        )
    grades.sort(key=lambda row: (row["causal_grade"], row["gene_symbol"]))
    fields = ["gene_symbol", "causal_grade", "methods", "evidence_types", "evidence_count", "best_p_value", "rationale", "limitation"]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(grades)
    manifest = {
        "schema_version": "v4.causal_evidence_manifest/0.1",
        "module_id": "causal_evidence_grading_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "graded_genes": len(grades),
        "output": str(out.relative_to(project_dir)),
        "grade_policy": {
            "A": "MR plus coloc evidence",
            "B": "coloc evidence or strong GWAS/database genetic association",
            "C": "association only",
            "D": "insufficient genetic evidence",
        },
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _grade(methods: set[str], evidence_types: set[str], best_p: float | None) -> tuple[str, str]:
    if "mr" in methods and "coloc" in methods:
        return "A", "MR and colocalization evidence are both present."
    if "coloc" in methods:
        return "B", "Colocalization evidence is present; review locus and QTL context."
    if "association" in methods and (best_p is not None and best_p < 5e-8):
        return "B", "Genome-wide significant association-level evidence is present."
    if evidence_types:
        return "C", "Only association-level or database genetic evidence is available."
    return "D", "No recognized genetic causal evidence."
