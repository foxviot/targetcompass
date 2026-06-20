import csv
from pathlib import Path

from .paths import KB


def _read_tsv(path: Path, key: str):
    with path.open(encoding="utf-8") as f:
        return {row[key]: row for row in csv.DictReader(f, delimiter="\t")}


def _merge_custom_tables(project_dir: Path, base: dict, kind: str) -> dict:
    for path in sorted((project_dir / "knowledge_imports" / "normalized").glob(f"*_{kind}.tsv")):
        rows = _read_tsv(path, "gene_symbol")
        base.update(rows)
    return base


def annotate_project(project_dir: Path) -> tuple[Path, Path, Path]:
    access = _merge_custom_tables(project_dir, _read_tsv(KB / "annotation_tables" / "accessibility.tsv", "gene_symbol"), "accessibility")
    safety = _merge_custom_tables(project_dir, _read_tsv(KB / "annotation_tables" / "safety.tsv", "gene_symbol"), "safety")
    genes = []
    for deg_path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv")):
        with deg_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                genes.append(row["gene_symbol"])
    genes = sorted(set(genes))
    out_dir = project_dir / "results" / "annotation"
    out_dir.mkdir(parents=True, exist_ok=True)
    access_path = out_dir / "accessibility_annotation.tsv"
    safety_path = out_dir / "safety_flags.tsv"
    review_path = out_dir / "unknown_review.tsv"
    access_rows = {}
    safety_rows = {}
    with access_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["gene_symbol", "route", "accessibility_status", "source"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for gene in genes:
            row = access.get(gene, {"gene_symbol": gene, "route": "unknown", "accessibility_status": "UNKNOWN", "source": "local_default"})
            access_rows[gene] = {k: row.get(k, "") for k in fields}
            writer.writerow(access_rows[gene])
    with safety_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["gene_symbol", "safety_gate", "critical_tissue_flag", "note"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for gene in genes:
            row = safety.get(gene, {"gene_symbol": gene, "safety_gate": "UNKNOWN", "critical_tissue_flag": "UNKNOWN", "note": "not in local safety table"})
            safety_rows[gene] = {k: row.get(k, "") for k in fields}
            writer.writerow(safety_rows[gene])
    with review_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["gene_symbol", "missing_fields", "route", "safety_gate", "recommended_action"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for gene in genes:
            missing = []
            route = access_rows.get(gene, {}).get("route", "unknown")
            safety_gate = safety_rows.get(gene, {}).get("safety_gate", "UNKNOWN")
            if route == "unknown" or access_rows.get(gene, {}).get("accessibility_status") == "UNKNOWN":
                missing.append("accessibility")
            if safety_gate == "UNKNOWN":
                missing.append("safety")
            if missing:
                writer.writerow(
                    {
                        "gene_symbol": gene,
                        "missing_fields": ",".join(missing),
                        "route": route,
                        "safety_gate": safety_gate,
                        "recommended_action": "manual curation before interpreting candidate rank",
                    }
                )
    return access_path, safety_path, review_path
