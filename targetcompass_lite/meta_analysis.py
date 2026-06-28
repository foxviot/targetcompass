import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .v4 import file_hash


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
    forest_dir = out_dir / "forest_plots"
    forest_dir.mkdir(exist_ok=True)
    results = []
    forest_index = []
    for gene, rows in rows_by_gene.items():
        effects = [_float(row.get("logFC")) for row in rows if _float(row.get("logFC")) is not None]
        p_values = [_float(row.get("adj_p_value")) for row in rows if _float(row.get("adj_p_value")) is not None]
        if not effects:
            continue
        study_rows = [_study_row(row) for row in rows if _study_row(row) is not None]
        fixed = _fixed_effect(study_rows)
        random = _random_effect(study_rows, fixed)
        directions = ["up" if value > 0 else "down" if value < 0 else "flat" for value in effects]
        dominant = max(set(directions), key=directions.count)
        direction_consistency = directions.count(dominant) / len(directions)
        fisher_stat = -2 * sum(math.log(max(value, 1e-300)) for value in p_values) if p_values else 0.0
        combined_score = fisher_stat / max(1, 2 * len(p_values))
        qc_flags = _qc_flags(rows, direction_consistency, random)
        forest_path = _write_forest_svg(project_dir, forest_dir, gene, study_rows, fixed, random)
        if forest_path:
            forest_index.append({"gene_symbol": gene, "forest_plot": str(forest_path.relative_to(project_dir))})
        results.append(
            {
                "gene_symbol": gene,
                "dataset_count": len(rows),
                "mean_logFC": f"{sum(effects) / len(effects):.6g}",
                "max_abs_logFC": f"{max(abs(value) for value in effects):.6g}",
                "fixed_effect_logFC": f"{fixed['effect']:.6g}" if fixed else "",
                "fixed_effect_se": f"{fixed['se']:.6g}" if fixed else "",
                "fixed_effect_z": f"{fixed['z']:.6g}" if fixed else "",
                "random_effect_logFC": f"{random['effect']:.6g}" if random else "",
                "random_effect_se": f"{random['se']:.6g}" if random else "",
                "random_effect_tau2": f"{random['tau2']:.6g}" if random else "",
                "heterogeneity_q": f"{random['q']:.6g}" if random else "",
                "heterogeneity_i2": f"{random['i2']:.3f}" if random else "",
                "dominant_direction": dominant,
                "direction_consistency": f"{direction_consistency:.3f}",
                "combined_p_score": f"{combined_score:.6g}",
                "qc_status": "review" if qc_flags else "pass",
                "qc_flags": ";".join(qc_flags) or "none",
                "forest_plot": str(forest_path.relative_to(project_dir)) if forest_path else "",
                "source_datasets": ";".join(sorted({row["dataset_id"] for row in rows})),
                "limitation": "Local fixed/random effects meta-analysis uses approximate SE from DEG p-values when explicit SE is absent; publication-grade use requires method review.",
            }
        )
    results.sort(key=lambda row: (-float(row["dataset_count"]), -abs(float(row.get("random_effect_logFC") or row.get("mean_logFC") or 0)), row["gene_symbol"]))
    fields = [
        "gene_symbol",
        "dataset_count",
        "mean_logFC",
        "max_abs_logFC",
        "fixed_effect_logFC",
        "fixed_effect_se",
        "fixed_effect_z",
        "random_effect_logFC",
        "random_effect_se",
        "random_effect_tau2",
        "heterogeneity_q",
        "heterogeneity_i2",
        "dominant_direction",
        "direction_consistency",
        "combined_p_score",
        "qc_status",
        "qc_flags",
        "forest_plot",
        "source_datasets",
        "limitation",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(results)
    forest_index_path = out_dir / "forest_plot_index.tsv"
    with forest_index_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["gene_symbol", "forest_plot"], delimiter="\t")
        writer.writeheader()
        writer.writerows(forest_index)
    qc = {
        "schema_version": "v4.meta_analysis_qc/0.1",
        "status": "pass" if results else "warning",
        "genes": len(results),
        "review_gene_count": sum(1 for row in results if row.get("qc_status") == "review"),
        "high_heterogeneity_count": sum(1 for row in results if "high_heterogeneity" in row.get("qc_flags", "")),
        "direction_conflict_count": sum(1 for row in results if "direction_conflict" in row.get("qc_flags", "")),
    }
    qc_summary = out_dir / "qc_summary.json"
    qc_summary.write_text(json.dumps(qc, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema_version": "v4.meta_analysis_manifest/0.2",
        "module_id": "deg_meta_analysis_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "methods": {
            "fixed_effect": "Inverse-variance weighted fixed effect model.",
            "random_effect": "DerSimonian-Laird random effects model with approximate SE fallback.",
            "heterogeneity": "Cochran Q and I2.",
        },
        "genes": len(results),
        "input_deg_count": len(list((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))),
        "output": str(out.relative_to(project_dir)),
        "output_hash": file_hash(out),
        "forest_plot_index": str(forest_index_path.relative_to(project_dir)),
        "forest_plot_count": len(forest_index),
        "qc_summary": str((out_dir / "qc_summary.json").relative_to(project_dir)),
        "qc": qc,
        "status": qc["status"],
    }
    run_manifest = out_dir / "run_manifest.json"
    run_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [out, forest_index_path, qc_summary, run_manifest],
            producer="meta_analysis",
            artifact_type="meta_analysis_output",
            task_id="meta_analysis",
            qc_status="pass" if qc["status"] == "pass" else "pending",
        )
    except Exception:
        pass
    return out


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _study_row(row: dict[str, str]) -> dict[str, float | str] | None:
    effect = _float(row.get("logFC"))
    if effect is None:
        return None
    se = _float(row.get("se")) or _float(row.get("standard_error"))
    p_value = _float(row.get("p_value")) or _float(row.get("adj_p_value"))
    if se is None:
        se = _approx_se(effect, p_value)
    if se is None or se <= 0:
        se = max(0.25, abs(effect) / 2.0)
    return {"dataset_id": row.get("dataset_id", ""), "effect": effect, "se": se, "p_value": p_value or 1.0}


def _approx_se(effect: float, p_value: float | None) -> float | None:
    if p_value is None or p_value <= 0 or p_value >= 1 or effect == 0:
        return None
    z = _normal_z_from_two_sided_p(p_value)
    if z <= 0:
        return None
    return abs(effect) / z


def _normal_z_from_two_sided_p(p_value: float) -> float:
    # Lightweight monotonic approximation good enough for QC weighting when explicit SE is absent.
    return max(0.1, math.sqrt(-2.0 * math.log(max(p_value / 2.0, 1e-300))))


def _fixed_effect(studies: list[dict]) -> dict | None:
    if not studies:
        return None
    weights = [1.0 / max(float(row["se"]) ** 2, 1e-12) for row in studies]
    total_w = sum(weights)
    effect = sum(weight * float(row["effect"]) for weight, row in zip(weights, studies)) / total_w
    se = math.sqrt(1.0 / total_w)
    z = effect / se if se else 0.0
    return {"effect": effect, "se": se, "z": z, "weights": weights}


def _random_effect(studies: list[dict], fixed: dict | None) -> dict | None:
    if not studies or fixed is None:
        return None
    weights = fixed["weights"]
    fixed_effect = fixed["effect"]
    q = sum(weight * (float(row["effect"]) - fixed_effect) ** 2 for weight, row in zip(weights, studies))
    df = max(1, len(studies) - 1)
    c = sum(weights) - (sum(weight**2 for weight in weights) / sum(weights))
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    random_weights = [1.0 / max(float(row["se"]) ** 2 + tau2, 1e-12) for row in studies]
    total_w = sum(random_weights)
    effect = sum(weight * float(row["effect"]) for weight, row in zip(random_weights, studies)) / total_w
    se = math.sqrt(1.0 / total_w)
    i2 = max(0.0, (q - df) / q) if q > 0 else 0.0
    return {"effect": effect, "se": se, "tau2": tau2, "q": q, "i2": i2}


def _qc_flags(rows: list[dict[str, str]], direction_consistency: float, random: dict | None) -> list[str]:
    flags = []
    if len(rows) < 2:
        flags.append("single_dataset")
    if direction_consistency < 0.75:
        flags.append("direction_conflict")
    if random and float(random.get("i2", 0)) >= 0.5:
        flags.append("high_heterogeneity")
    return flags


def _write_forest_svg(project_dir: Path, forest_dir: Path, gene: str, studies: list[dict], fixed: dict | None, random: dict | None) -> Path | None:
    if not studies:
        return None
    safe_gene = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in gene)[:80] or "gene"
    path = forest_dir / f"{safe_gene}.svg"
    effects = [float(row["effect"]) for row in studies]
    ses = [float(row["se"]) for row in studies]
    lo = min([effect - 1.96 * se for effect, se in zip(effects, ses)] + ([random["effect"] - 1.96 * random["se"]] if random else []))
    hi = max([effect + 1.96 * se for effect, se in zip(effects, ses)] + ([random["effect"] + 1.96 * random["se"]] if random else []))
    if lo == hi:
        lo -= 1
        hi += 1
    width = 720
    row_h = 28
    height = 80 + row_h * (len(studies) + 2)
    plot_x0 = 220
    plot_x1 = 680

    def x(value: float) -> float:
        return plot_x0 + (value - lo) / (hi - lo) * (plot_x1 - plot_x0)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="24" y="28" font-family="Arial" font-size="16" font-weight="700">{_xml(gene)} meta-analysis forest plot</text>',
        f'<line x1="{x(0):.1f}" y1="46" x2="{x(0):.1f}" y2="{height - 32}" stroke="#9ca3af" stroke-dasharray="4 4"/>',
        f'<line x1="{plot_x0}" y1="{height - 32}" x2="{plot_x1}" y2="{height - 32}" stroke="#374151"/>',
    ]
    for idx, row in enumerate(studies):
        y = 60 + idx * row_h
        effect = float(row["effect"])
        se = float(row["se"])
        lines.extend(
            [
                f'<text x="24" y="{y + 5}" font-family="Arial" font-size="12">{_xml(str(row.get("dataset_id", "")))}</text>',
                f'<line x1="{x(effect - 1.96 * se):.1f}" y1="{y}" x2="{x(effect + 1.96 * se):.1f}" y2="{y}" stroke="#2563eb" stroke-width="2"/>',
                f'<circle cx="{x(effect):.1f}" cy="{y}" r="4" fill="#2563eb"/>',
            ]
        )
    if random:
        y = 60 + len(studies) * row_h + 8
        effect = float(random["effect"])
        se = float(random["se"])
        points = f'{x(effect):.1f},{y - 7} {x(effect + 1.96 * se):.1f},{y} {x(effect):.1f},{y + 7} {x(effect - 1.96 * se):.1f},{y}'
        lines.extend(
            [
                f'<text x="24" y="{y + 5}" font-family="Arial" font-size="12" font-weight="700">Random effect</text>',
                f'<polygon points="{points}" fill="#dc2626" opacity="0.85"/>',
            ]
        )
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
