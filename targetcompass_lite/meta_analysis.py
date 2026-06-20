import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def run_meta_analysis(project_dir: Path) -> Path:
    rows_by_gene: dict[str, list[dict[str, str]]] = defaultdict(list)
    for deg_path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv")):
        dataset_id = deg_path.parent.name.replace("bulk_deg_", "")
        with deg_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                row["dataset_id"] = dataset_id
                rows_by_gene[row.get("gene_symbol", "")].append(row)
    out_dir = project_dir / "results" / "meta_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "deg_meta_analysis.tsv"
    results = []
    for gene, rows in rows_by_gene.items():
        effects = [_float(row.get("logFC")) for row in rows if _float(row.get("logFC")) is not None]
        p_values = [_float(row.get("adj_p_value")) for row in rows if _float(row.get("adj_p_value")) is not None]
        if not effects:
            continue
        directions = ["up" if value > 0 else "down" if value < 0 else "flat" for value in effects]
        dominant = max(set(directions), key=directions.count)
        direction_consistency = directions.count(dominant) / len(directions)
        fisher_stat = -2 * sum(math.log(max(value, 1e-300)) for value in p_values) if p_values else 0.0
        combined_score = fisher_stat / max(1, 2 * len(p_values))
        results.append(
            {
                "gene_symbol": gene,
                "dataset_count": len(rows),
                "mean_logFC": f"{sum(effects) / len(effects):.6g}",
                "max_abs_logFC": f"{max(abs(value) for value in effects):.6g}",
                "dominant_direction": dominant,
                "direction_consistency": f"{direction_consistency:.3f}",
                "combined_p_score": f"{combined_score:.6g}",
                "source_datasets": ";".join(sorted({row["dataset_id"] for row in rows})),
                "limitation": "Lightweight fixed summary; use formal random-effects meta-analysis for publication-grade inference.",
            }
        )
    results.sort(key=lambda row: (-float(row["dataset_count"]), -float(row["combined_p_score"]), row["gene_symbol"]))
    fields = [
        "gene_symbol",
        "dataset_count",
        "mean_logFC",
        "max_abs_logFC",
        "dominant_direction",
        "direction_consistency",
        "combined_p_score",
        "source_datasets",
        "limitation",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(results)
    manifest = {
        "schema_version": "v4.meta_analysis_manifest/0.1",
        "module_id": "deg_meta_analysis_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "genes": len(results),
        "input_deg_count": len(list((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))),
        "output": str(out.relative_to(project_dir)),
        "status": "pass" if results else "warning",
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None
