import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_SASP_CORE = [
    "IL6",
    "CXCL8",
    "CCL2",
    "CXCL1",
    "CXCL2",
    "IL1B",
    "TNF",
    "MMP3",
    "MMP9",
    "SERPINE1",
    "ICAM1",
    "VCAM1",
    "TGFB1",
]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _sasp_gene_set(project_dir: Path) -> list[str]:
    spec = _read_json(project_dir / "research_spec.json", {})
    configured = spec.get("sasp_core") or spec.get("sasp_gene_set") or spec.get("sasp_markers")
    if isinstance(configured, list):
        genes = [str(item).strip().upper() for item in configured if str(item).strip()]
        if genes:
            return sorted(set(genes))
    return DEFAULT_SASP_CORE


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def _component_score(log_fc: float, adj_p: float, is_core: bool) -> float:
    magnitude = min(abs(log_fc), 5.0) * 2.0
    direction_bonus = 2.0 if log_fc > 0 else -1.0
    significance = 0.0
    if adj_p <= 0.001:
        significance = 5.0
    elif adj_p <= 0.01:
        significance = 4.0
    elif adj_p <= 0.05:
        significance = 3.0
    elif adj_p <= 0.1:
        significance = 1.0
    core_bonus = 3.0 if is_core else 0.0
    return round(max(0.0, magnitude + direction_bonus + significance + core_bonus), 3)


def _dataset_id_from_deg_path(path: Path) -> str:
    name = path.parent.name
    return name.replace("bulk_deg_", "", 1) if name.startswith("bulk_deg_") else name


def run_sasp_score(project_dir: Path) -> dict[str, Any]:
    """Score SASP-like expression signal from real DEG outputs.

    The score is intentionally separate from candidate target ranking. It tells the
    reviewer whether the disease contrast contains a senescence/SASP expression
    signal and which candidate genes overlap that signal.
    """

    core = set(_sasp_gene_set(project_dir))
    deg_paths = sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))
    gene_rows: list[dict[str, Any]] = []
    dataset_summaries: list[dict[str, Any]] = []
    for deg_path in deg_paths:
        dataset_id = _dataset_id_from_deg_path(deg_path)
        rows = _read_tsv(deg_path)
        matched = 0
        up = 0
        down = 0
        score_sum = 0.0
        top_gene = ""
        top_score = -1.0
        for row in rows:
            gene = (row.get("gene_symbol") or row.get("Gene") or row.get("symbol") or "").strip().upper()
            if gene not in core:
                continue
            log_fc = _as_float(row.get("logFC") or row.get("log2FoldChange") or row.get("effect_size"))
            adj_p = _as_float(row.get("adj_p_value") or row.get("adj.P.Val") or row.get("padj") or row.get("p_value"), 1.0)
            direction = row.get("direction") or ("up" if log_fc > 0 else "down" if log_fc < 0 else "flat")
            component = _component_score(log_fc, adj_p, True)
            matched += 1
            up += 1 if direction == "up" or log_fc > 0 else 0
            down += 1 if direction == "down" or log_fc < 0 else 0
            score_sum += component
            if component > top_score:
                top_gene = gene
                top_score = component
            gene_rows.append(
                {
                    "dataset_id": dataset_id,
                    "gene_symbol": gene,
                    "logFC": f"{log_fc:.6g}",
                    "adj_p_value": f"{adj_p:.6g}",
                    "direction": direction,
                    "is_sasp_core": "true",
                    "sasp_component_score": f"{component:.3f}",
                    "source_artifact": str(deg_path.relative_to(project_dir)),
                }
            )
        normalized = round(score_sum / max(len(core), 1), 3)
        dataset_summaries.append(
            {
                "dataset_id": dataset_id,
                "deg_artifact": str(deg_path.relative_to(project_dir)),
                "sasp_core_size": len(core),
                "matched_sasp_genes": matched,
                "up_sasp_genes": up,
                "down_sasp_genes": down,
                "sasp_dataset_score": f"{normalized:.3f}",
                "top_sasp_gene": top_gene,
                "status": "PASS" if matched else "NO_SASP_CORE_MATCH",
            }
        )
    gene_rows.sort(key=lambda r: (-_as_float(r["sasp_component_score"]), r["dataset_id"], r["gene_symbol"]))
    dataset_summaries.sort(key=lambda r: (-_as_float(r["sasp_dataset_score"]), r["dataset_id"]))
    out_dir = project_dir / "results" / "sasp_score"
    gene_path = out_dir / "sasp_gene_scores.tsv"
    dataset_path = out_dir / "sasp_dataset_scores.tsv"
    _write_tsv(
        gene_path,
        gene_rows,
        ["dataset_id", "gene_symbol", "logFC", "adj_p_value", "direction", "is_sasp_core", "sasp_component_score", "source_artifact"],
    )
    _write_tsv(
        dataset_path,
        dataset_summaries,
        ["dataset_id", "deg_artifact", "sasp_core_size", "matched_sasp_genes", "up_sasp_genes", "down_sasp_genes", "sasp_dataset_score", "top_sasp_gene", "status"],
    )
    manifest = {
        "schema_version": "sasp_score_manifest_v1",
        "project_id": project_dir.name,
        "method": "SASP core overlap from bulk DEG outputs",
        "sasp_core_genes": sorted(core),
        "input_deg_files": [str(path.relative_to(project_dir)) for path in deg_paths],
        "dataset_count": len(deg_paths),
        "sasp_gene_score_count": len(gene_rows),
        "outputs": {
            "gene_scores": str(gene_path.relative_to(project_dir)),
            "dataset_scores": str(dataset_path.relative_to(project_dir)),
        },
        "input_hash": hashlib.sha256(
            json.dumps(
                {"core": sorted(core), "deg_files": [str(path) + ":" + str(path.stat().st_mtime_ns) for path in deg_paths]},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }
    manifest_path = out_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [gene_path, dataset_path, manifest_path],
            producer="sasp_score",
            artifact_type="sasp_score_output",
            task_id="sasp_score",
        )
    except Exception:
        pass
    return {"manifest": manifest, "manifest_path": str(manifest_path)}
