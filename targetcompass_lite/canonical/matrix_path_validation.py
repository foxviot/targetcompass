from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import load_artifact_registry
from .backend_writer import write_json_artifact
from .schemas import now_iso


MATRIX_PATH_VALIDATION_SCHEMA = "v5.matrix_path_validation/0.1"
MATRIX_SOURCES = {"sra", "cellxgene", "cz_cellxgene"}


def build_matrix_path_validation(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    gate = _read_json(project_dir / "v5" / "resource_discovery" / "resource_gate_report.json", {})
    main_path = _read_json(project_dir / "v5" / "analysis_main_path" / "main_path_manifest.json", {})
    report = _read_json(project_dir / "v5" / "reports" / "canonical_report_manifest.json", {})
    artifacts = load_artifact_registry(project_dir)
    rows = [_validate_gate_item(project_dir, row, artifacts, main_path, report) for row in gate.get("gate_items", []) if _is_matrix_source(row)]
    payload = {
        "schema_version": MATRIX_PATH_VALIDATION_SCHEMA,
        "project_id": project_dir.name,
        "status": "PASS" if rows and all(row["status"] == "PASS" for row in rows) else "REVIEW",
        "candidate_count": len(rows),
        "pass_count": sum(1 for row in rows if row["status"] == "PASS"),
        "review_count": sum(1 for row in rows if row["status"] != "PASS"),
        "rows": rows,
        "policy": {
            "sra": "SRA passes only with expression_matrix.tsv, metadata.tsv, quantification_manifest.json, artifact registration, TaskRun, QCReport, and report reference.",
            "cellxgene": "cellxgene passes only with expression_matrix.tsv, metadata.tsv, cellxgene_manifest.json or h5ad export, artifact registration, TaskRun, QCReport, and report reference.",
            "metadata_only": "Metadata-verified candidates remain REVIEW and cannot be counted as true matrix-path validation.",
        },
        "generated_at": now_iso(),
    }
    write_json_artifact(project_dir, "v5/platform/matrix_path_validation.json", payload, producer="matrix_path_validation", artifact_type="matrix_path_validation")
    return payload


def _validate_gate_item(project_dir: Path, item: dict[str, Any], artifacts: list[dict[str, Any]], main_path: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    source = str(item.get("source_database", "")).lower()
    accession = str(item.get("accession", "")).upper()
    data_dir = project_dir / "data" / accession
    expression = data_dir / "expression_matrix.tsv"
    metadata = data_dir / "metadata.tsv"
    quant = data_dir / "quantification_manifest.json"
    cxg_manifest = data_dir / "cellxgene_manifest.json"
    h5ad_exports = list(data_dir.glob("*.h5ad")) if data_dir.exists() else []
    required_manifest_ok = quant.exists() if source == "sra" else (cxg_manifest.exists() or bool(h5ad_exports))
    expression_rel = _rel(expression, project_dir)
    metadata_rel = _rel(metadata, project_dir)
    required_manifest_rel = _rel(quant if source == "sra" else (cxg_manifest if cxg_manifest.exists() else h5ad_exports[0] if h5ad_exports else cxg_manifest), project_dir)
    artifact_paths = {str(row.get("path", "")).replace("\\", "/") for row in artifacts if row.get("exists") is True and row.get("is_placeholder") is not True}
    artifact_refs_ok = expression_rel in artifact_paths and metadata_rel in artifact_paths and required_manifest_rel in artifact_paths
    selected_accession = str(main_path.get("selected_dataset", {}).get("accession", "")).upper()
    main_path_matches = selected_accession == accession and main_path.get("status") in {"completed", "review_required", "PASS", "pass"}
    task_run_ok = bool(main_path_matches and main_path.get("task_run_refs"))
    qc_ok = bool(main_path_matches and main_path.get("qc_report_refs"))
    report_ok = bool(report.get("status") or main_path.get("canonical_report_manifest_ref"))
    checks = [
        _check("matrix_file", expression.exists(), expression_rel, "Upload or parse expression_matrix.tsv."),
        _check("metadata_file", metadata.exists(), metadata_rel, "Upload or parse metadata.tsv and align sample IDs."),
        _check("source_manifest", required_manifest_ok, required_manifest_rel, "Attach quantification_manifest.json for SRA or cellxgene_manifest.json/h5ad for cellxgene."),
        _check("artifact_registry", artifact_refs_ok, "v5/artifact_registry.jsonl", "Register matrix, metadata, and source manifest in Artifact Registry/ArtifactStore."),
        _check("task_run", task_run_ok, "v5/task_runs/", "Run v5-analysis-main-path after dataset lock."),
        _check("qc_report", qc_ok, "v5/qc_reports/", "Generate QCReport for the matrix-driven task."),
        _check("report_ref", report_ok, "v5/reports/canonical_report_manifest.json", "Build canonical report manifest after analysis/QC."),
    ]
    return {
        "source_database": source,
        "accession": accession,
        "status": "PASS" if all(row["status"] == "PASS" for row in checks) else "REVIEW",
        "gate_status": item.get("gate_status", ""),
        "matrix_parse_ready": item.get("matrix_parse_ready", False),
        "checks": checks,
        "next_action": "" if all(row["status"] == "PASS" for row in checks) else _first_recovery(checks),
    }


def _check(check_id: str, ok: bool, ref: str, recovery: str) -> dict[str, str]:
    return {"check_id": check_id, "status": "PASS" if ok else "REVIEW", "ref": ref, "recovery": "" if ok else recovery}


def _first_recovery(checks: list[dict[str, str]]) -> str:
    for row in checks:
        if row["status"] != "PASS":
            return row["recovery"]
    return ""


def _is_matrix_source(row: dict[str, Any]) -> bool:
    return str(row.get("source_database", "")).lower() in MATRIX_SOURCES


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
