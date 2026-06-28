import json
from pathlib import Path
from typing import Any
import glob


RESOLVER_SCHEMA = "v4.artifact_resolution/0.1"


def resolve_work_order_inputs(project_dir: Path, order: dict[str, Any]) -> dict[str, Any]:
    inputs = order.get("inputs", {}) or {}
    resolved = []
    missing = []
    for key, value in inputs.items():
        item = _resolve_one(project_dir, key, value)
        resolved.append(item)
        if item["status"] == "missing":
            missing.append(item)
    module = order.get("module", "")
    if module == "scrna_pseudobulk":
        for key in ["count_matrix", "metadata"]:
            if key not in inputs:
                item = _resolve_one(project_dir, key, "")
                resolved.append(item)
                missing.append(item)
    return {
        "schema_version": RESOLVER_SCHEMA,
        "project_id": project_dir.name,
        "work_order_id": order.get("work_order_id", ""),
        "module_id": order.get("module_id", ""),
        "module": module,
        "status": "pass" if not missing else "failed",
        "resolved": resolved,
        "missing": missing,
        "recovery": _recovery(order, missing),
    }


def write_artifact_resolution(project_dir: Path, order: dict[str, Any], resolution: dict[str, Any] | None = None) -> str:
    resolution = resolution or resolve_work_order_inputs(project_dir, order)
    out_dir = project_dir / "v4" / "artifact_resolution"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = order.get("work_order_id") or order.get("module_id") or "unknown"
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(resolution, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.relative_to(project_dir)).replace("\\", "/")


def _resolve_one(project_dir: Path, key: str, value: Any) -> dict[str, Any]:
    raw = "" if value is None else str(value)
    if raw:
        path = project_dir / raw
        if path.exists():
            return {"key": key, "declared": raw, "status": "available", "path": _rel(path, project_dir), "source": "declared_path"}
        matches = sorted(Path(item) for item in glob.glob(str(project_dir / raw)))
        if matches:
            return {"key": key, "declared": raw, "status": "available", "path": _rel(matches[0], project_dir), "source": "declared_glob", "match_count": len(matches)}
        inferred = _infer_logical(project_dir, key)
        if inferred:
            return {"key": key, "declared": raw, "status": "available", "path": _rel(inferred, project_dir), "source": "inferred_logical_artifact"}
        return {"key": key, "declared": raw, "status": "missing", "path": "", "source": "declared_path"}
    inferred = _infer_logical(project_dir, key)
    if inferred:
        return {"key": key, "declared": raw, "status": "available", "path": _rel(inferred, project_dir), "source": "inferred_logical_artifact"}
    return {"key": key, "declared": raw, "status": "missing", "path": "", "source": "logical_artifact"}


def _infer_logical(project_dir: Path, key: str) -> Path | None:
    normalized = key.lower()
    if normalized in {"count_matrix", "expression_matrix"}:
        for path in sorted((project_dir / "data").glob("*/expression_matrix.tsv")):
            if _has_cell_metadata(path.parent / "metadata.tsv"):
                return path
    if normalized == "metadata":
        for path in sorted((project_dir / "data").glob("*/metadata.tsv")):
            if _has_cell_metadata(path):
                return path
    if normalized in {"deg", "deg_results", "candidate_genes"}:
        candidates = sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))
        return candidates[0] if candidates else None
    if normalized in {"annotation_sources", "cell_type_sources"}:
        for candidate in [
            project_dir / "knowledge_imports" / "normalized",
            project_dir / "knowledge_base" / "annotation_tables",
            project_dir.parent.parent / "knowledge_base" / "annotation_tables",
        ]:
            if candidate.exists():
                return candidate
    if normalized in {"sasp", "sasp_scores"}:
        path = project_dir / "results" / "sasp_score" / "sasp_gene_scores.tsv"
        return path if path.exists() else None
    return None


def _has_cell_metadata(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        header = path.read_text(encoding="utf-8").splitlines()[0].split("\t")
    except Exception:
        return False
    return {"cell_id", "donor_id", "group"}.issubset(set(header))


def _recovery(order: dict[str, Any], missing: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not missing:
        return []
    module = order.get("module", "")
    keys = ", ".join(row.get("key", "") for row in missing)
    if module == "scrna_pseudobulk":
        return [
            {
                "type": "provide_input",
                "message": f"Provide scRNA/snRNA count_matrix and metadata for missing input(s): {keys}. Metadata must include cell_id, donor_id, group, and optionally cell_type.",
            },
            {
                "type": "geo_raw_import",
                "message": "Run GEO raw import or 10x pseudobulk preparation, then update WorkOrder inputs with the generated paths.",
            },
        ]
    return [{"type": "provide_input", "message": f"Provide declared input artifact(s): {keys}."}]


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
