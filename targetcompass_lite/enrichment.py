import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .paths import KB
from .v4 import file_hash


GENE_SETS = KB / "enrichment_gene_sets.tsv"


def _read_gene_sets(path: Path = GENE_SETS) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f, delimiter="\t"):
            genes = {gene.strip() for gene in row["genes"].split(",") if gene.strip()}
            rows.append({**row, "gene_set": genes})
        return rows


def _project_gene_sets(project_dir: Path) -> list[dict]:
    rows = _read_gene_sets()
    for path in sorted((project_dir / "knowledge_imports" / "normalized").glob("*_gene_sets.tsv")):
        rows.extend(_read_gene_sets(path))
    return rows


def _read_deg(path: Path) -> tuple[set[str], set[str]]:
    background = set()
    selected = set()
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            gene = row["gene_symbol"]
            background.add(gene)
            try:
                adj_p = float(row["adj_p_value"])
                log_fc = float(row["logFC"])
            except (TypeError, ValueError):
                continue
            if adj_p < 0.05 and log_fc > 0:
                selected.add(gene)
    return background, selected


def _hypergeom_sf(overlap: int, selected_n: int, term_n: int, universe_n: int) -> float:
    if overlap <= 0 or selected_n <= 0 or term_n <= 0 or universe_n <= 0:
        return 1.0
    denominator = math.comb(universe_n, selected_n)
    max_k = min(selected_n, term_n)
    total = 0
    for k in range(overlap, max_k + 1):
        if selected_n - k <= universe_n - term_n:
            total += math.comb(term_n, k) * math.comb(universe_n - term_n, selected_n - k)
    return min(1.0, total / denominator)


def run_enrichment(project_dir: Path) -> Path:
    out_dir = project_dir / "results" / "enrichment"
    out_dir.mkdir(parents=True, exist_ok=True)
    gene_sets = _project_gene_sets(project_dir)
    all_rows = []
    for deg_path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv")):
        dataset_id = deg_path.parent.name.replace("bulk_deg_", "")
        background, selected = _read_deg(deg_path)
        universe_n = len(background)
        selected_n = len(selected)
        for term in gene_sets:
            term_genes = term["gene_set"] & background
            overlap_genes = sorted(term_genes & selected)
            p_value = _hypergeom_sf(len(overlap_genes), selected_n, len(term_genes), universe_n)
            all_rows.append(
                {
                    "dataset_id": dataset_id,
                    "term_id": term["term_id"],
                    "term_name": term["term_name"],
                    "overlap_n": len(overlap_genes),
                    "selected_n": selected_n,
                    "term_n": len(term_genes),
                    "universe_n": universe_n,
                    "p_value": f"{p_value:.6g}",
                    "overlap_genes": ",".join(overlap_genes),
                    "source": term["source"],
                }
            )
    all_rows.sort(key=lambda row: (float(row["p_value"]), row["dataset_id"], row["term_id"]))
    for rank, row in enumerate(all_rows, 1):
        row["adj_p_value"] = f"{min(1.0, float(row['p_value']) * len(all_rows) / rank):.6g}"
    out = out_dir / "enrichment_results.tsv"
    fields = [
        "dataset_id",
        "term_id",
        "term_name",
        "overlap_n",
        "selected_n",
        "term_n",
        "universe_n",
        "p_value",
        "adj_p_value",
        "overlap_genes",
        "source",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(all_rows)
    manifest = {
        "schema_version": "v4.enrichment_manifest/0.2",
        "module_id": "enrichment_v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_deg_files": [str(path.relative_to(project_dir)) for path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))],
        "gene_set_count": len(gene_sets),
        "tested_rows": len(all_rows),
        "significant_rows": sum(1 for row in all_rows if float(row["adj_p_value"]) < 0.05),
        "output": str(out.relative_to(project_dir)),
        "output_hash": file_hash(out),
        "qc": {
            "status": "pass" if all_rows else "warning",
            "message": "enrichment completed" if all_rows else "no DEG inputs or gene sets produced testable rows",
        },
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "qc_summary.json").write_text(json.dumps(manifest["qc"], indent=2, ensure_ascii=False), encoding="utf-8")
    return out
