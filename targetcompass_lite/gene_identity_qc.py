import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gene_mapping import ensure_hgnc_mapping, ensure_hgnc_symbols


MAX_SCAN_ROWS = 5000


def assess_expression_gene_identity(project_dir: Path, expression_matrix: Path, dataset_id: str = "") -> dict[str, Any]:
    cached = _load_current_manifest(expression_matrix)
    if cached:
        return cached
    rows = _read_gene_ids(expression_matrix)
    total = len(rows)
    hgnc_symbols = ensure_hgnc_symbols(project_dir)
    ensembl_map = ensure_hgnc_mapping(project_dir)
    sample = rows[:MAX_SCAN_ROWS]
    hgnc_hits = sum(1 for gene in sample if _normalize(gene) in hgnc_symbols)
    ensembl_hits = sum(1 for gene in sample if _normalize_ensembl(gene) in ensembl_map)
    probe_like = sum(1 for gene in sample if _looks_like_probe_id(gene))
    unknown = len(sample) - hgnc_hits - ensembl_hits - probe_like
    denominator = max(1, len(sample))
    hgnc_rate = hgnc_hits / denominator
    ensembl_rate = ensembl_hits / denominator
    probe_rate = probe_like / denominator
    unknown_rate = max(0.0, unknown / denominator)
    if hgnc_rate >= 0.70:
        status = "PASS"
        identity_type = "hgnc_symbol"
    elif ensembl_rate >= 0.50:
        status = "REVIEW"
        identity_type = "ensembl_gene_id_requires_mapping"
    elif probe_rate >= 0.50:
        status = "REVIEW"
        identity_type = "platform_probe_id_requires_annotation"
    elif hgnc_rate >= 0.35:
        status = "REVIEW"
        identity_type = "mixed_or_partial_gene_symbols"
    else:
        status = "FAIL"
        identity_type = "unresolved_gene_identity"
    manifest = {
        "schema_version": "v4.gene_identity_qc/0.1",
        "dataset_id": dataset_id or expression_matrix.parent.name,
        "expression_matrix": str(expression_matrix.relative_to(project_dir)).replace("\\", "/") if expression_matrix.is_relative_to(project_dir) else str(expression_matrix),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "identity_type": identity_type,
        "scanned_rows": len(sample),
        "total_rows_observed": total,
        "hgnc_symbol_hits": hgnc_hits,
        "ensembl_gene_hits": ensembl_hits,
        "probe_like_hits": probe_like,
        "unknown_hits": max(0, unknown),
        "hgnc_symbol_rate": round(hgnc_rate, 4),
        "ensembl_gene_rate": round(ensembl_rate, 4),
        "probe_like_rate": round(probe_rate, 4),
        "unknown_rate": round(unknown_rate, 4),
        "examples": {
            "first_rows": rows[:20],
            "unknown_examples": [gene for gene in sample if _normalize(gene) not in hgnc_symbols and _normalize_ensembl(gene) not in ensembl_map and not _looks_like_probe_id(gene)][:20],
        },
        "recovery": _recovery(status, identity_type),
    }
    out = expression_matrix.with_name("gene_identity_qc.json")
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [out],
            producer="gene_identity_qc",
            artifact_type="gene_identity_qc_output",
            task_id=f"gene_identity_qc_{manifest['dataset_id']}",
            qc_status="pass" if status == "PASS" else "review",
        )
    except Exception:
        pass
    return manifest


def _load_current_manifest(expression_matrix: Path) -> dict[str, Any]:
    manifest_path = expression_matrix.with_name("gene_identity_qc.json")
    if not manifest_path.exists() or not expression_matrix.exists():
        return {}
    try:
        if manifest_path.stat().st_mtime < expression_matrix.stat().st_mtime:
            return {}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if manifest.get("schema_version") != "v4.gene_identity_qc/0.1":
        return {}
    if not manifest.get("status") or not manifest.get("identity_type"):
        return {}
    return manifest


def _read_gene_ids(path: Path) -> list[str]:
    genes = []
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, [])
        if not header:
            return []
        for row in reader:
            if row and row[0].strip():
                genes.append(row[0].strip())
    return genes


def _normalize(value: str) -> str:
    return str(value or "").strip().strip('"').upper()


def _normalize_ensembl(value: str) -> str:
    value = _normalize(value)
    if "|" in value:
        value = value.split("|")[-1]
    return re.sub(r"\.\d+$", "", value)


def _looks_like_probe_id(value: str) -> bool:
    value = _normalize(value)
    if value.startswith(("ILMN_", "AFFX", "PROBE", "TC", "A_", "GE_", "HT_")):
        return True
    if re.match(r"^\d+_AT$", value):
        return True
    if re.match(r"^\d{5,}$", value):
        return True
    return False


def _recovery(status: str, identity_type: str) -> list[str]:
    if status == "PASS":
        return []
    if identity_type == "ensembl_gene_id_requires_mapping":
        return ["Map Ensembl gene IDs to HGNC symbols before DEG/evidence import.", "Use the HGNC complete-set mapping and write gene_id_mapping_manifest.json."]
    if identity_type == "platform_probe_id_requires_annotation":
        return ["Download the GEO platform annotation file, e.g. GPLxxxx.annot.gz.", "Re-import with --platform-annotation and --symbol-column so probe IDs collapse to HGNC gene symbols."]
    return ["Inspect expression matrix first column.", "Confirm whether row IDs are HGNC symbols, Ensembl IDs, or platform probe IDs.", "Do not treat unresolved row IDs as gene symbols in scoring."]
