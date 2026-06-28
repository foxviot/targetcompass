import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema_validation import load_schema, validate_object
from .v4 import content_hash


TASK_QC_SCHEMA_VERSION = "v0.1.task_qc_report"
QC_LAYERS = ["Execution", "Data", "Statistical", "Biological"]


def build_task_qc_report(
    project_dir: Path,
    order: dict[str, Any],
    node: dict[str, Any],
    dispatch: dict[str, Any],
) -> dict[str, Any]:
    layers = [
        _execution_qc(project_dir, order, node, dispatch),
        _data_qc(project_dir, order, node, dispatch),
        _statistical_qc(project_dir, order, node, dispatch),
        _biological_qc(project_dir, order, node, dispatch),
    ]
    blocking = [issue for layer in layers for issue in layer.get("blocking_reasons", [])]
    warnings = [issue for layer in layers for issue in layer.get("warnings", [])]
    if any(layer["status"] == "fail" for layer in layers):
        overall = "fail"
    elif any(layer["status"] == "review" for layer in layers):
        overall = "review"
    else:
        overall = "pass"
    report = {
        "schema_version": TASK_QC_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "work_order_id": order.get("work_order_id", node.get("work_order_id", "")),
        "module_id": order.get("module_id", node.get("module_id", "")),
        "dataset_id": order.get("dataset_id", node.get("dataset_id", "")),
        "overall_status": overall,
        "layers": layers,
        "blocking_reasons": _dedupe(blocking),
        "warnings": _dedupe(warnings),
        "artifacts": _dedupe([str(item) for item in dispatch.get("artifacts", []) if item]),
        "generated_at": _now(),
    }
    _validate(report)
    _write_report(project_dir, report)
    return report


def load_task_qc_index(project_dir: Path) -> dict[str, Any]:
    path = _index_path(project_dir)
    if not path.exists():
        return {"schema_version": "v0.1.task_qc_report_index", "project_id": project_dir.name, "reports": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _execution_qc(project_dir: Path, order: dict[str, Any], node: dict[str, Any], dispatch: dict[str, Any]) -> dict[str, Any]:
    status = dispatch.get("status", "failed")
    artifacts = list(dispatch.get("artifacts", []) or [])
    checks = [
        _check("dispatch returned a terminal status", status in {"success", "failed", "blocked", "skipped"}),
        _check("executor backend is recorded", bool(dispatch.get("backend"))),
        _check("failure reason is recorded when execution fails", status == "success" or bool(dispatch.get("failure_reason"))),
    ]
    if dispatch.get("executor_manifest"):
        checks.append(_check("executor manifest exists", (project_dir / dispatch["executor_manifest"]).exists()))
    elif status == "success" and order.get("module") not in {"descriptive_evidence"}:
        checks.append(_check("executor manifest exists", False, severity="review"))
    expected = list(order.get("expected_artifacts", []) or [])
    if status == "success" and expected:
        produced = set(artifacts)
        checks.append(_check("at least one expected artifact is produced", bool(produced.intersection(expected))))
    return _layer("Execution", checks)


def _data_qc(project_dir: Path, order: dict[str, Any], node: dict[str, Any], dispatch: dict[str, Any]) -> dict[str, Any]:
    resolution = dispatch.get("input_resolution") or node.get("input_resolution") or {}
    missing = resolution.get("missing", []) if isinstance(resolution, dict) else []
    checks = [_check("input resolution is available", bool(resolution), severity="review")]
    checks.append(_check("required inputs are resolved", not missing))
    profile = _dataset_profile(project_dir, order.get("dataset_id", ""))
    if profile:
        checks.append(_check("DatasetProfile exists", True))
        readiness = profile.get("analysis_readiness", "")
        if order.get("module") in {"bulk_deg", "scrna_pseudobulk"}:
            checks.append(_check("dataset is analysis-ready for expression module", readiness == "ready"))
        checks.append(_check("metadata quality is recorded", bool(profile.get("metadata_quality")), severity="review"))
    elif order.get("dataset_id"):
        checks.append(_check("DatasetProfile exists", False, severity="review"))
    return _layer("Data", checks)


def _statistical_qc(project_dir: Path, order: dict[str, Any], node: dict[str, Any], dispatch: dict[str, Any]) -> dict[str, Any]:
    module = order.get("module", "")
    qc_payload = _module_qc(project_dir, order, dispatch)
    checks = []
    if module in {"annotation", "cell_type_evidence"}:
        checks.append(_check("statistical QC not required for annotation-only task", True))
        return _layer("Statistical", checks)
    checks.append(_check("module QC summary exists when statistical claims are made", bool(qc_payload), severity="review"))
    if qc_payload:
        qc_status = str(qc_payload.get("qc_status") or qc_payload.get("status") or "").lower()
        if qc_status:
            checks.append(_check("module QC status is not failed", qc_status not in {"fail", "failed"}))
        if module == "bulk_deg":
            checks.append(_check("case/control sample counts are recorded", "case_n" in qc_payload and "control_n" in qc_payload, severity="review"))
            if "full_rank" in qc_payload:
                checks.append(_check("design matrix is full rank", bool(qc_payload.get("full_rank"))))
        if module == "scrna_pseudobulk":
            checks.append(_check("donor-level QC is present", "donor_group_qc" in json.dumps(qc_payload, ensure_ascii=False), severity="review"))
    return _layer("Statistical", checks)


def _biological_qc(project_dir: Path, order: dict[str, Any], node: dict[str, Any], dispatch: dict[str, Any]) -> dict[str, Any]:
    module = order.get("module", "")
    params = order.get("parameters", {}) or {}
    checks = [
        _check("claim limit or method contract is recorded", bool(params.get("method_contract_id") or order.get("method_id") or order.get("qc_checks")), severity="review"),
        _check("task does not grant causal claims by default", "causal" not in str(params.get("claim_limit", "")).lower() or module in {"genetic_coloc_mr", "causal_evidence"}),
    ]
    evidence_writes = node.get("evidence_writes", []) or []
    if module in {"bulk_deg", "sasp_score", "annotation", "cell_type_evidence", "meta_analysis", "causal_evidence", "genetic_coloc_mr"}:
        if dispatch.get("status") == "success":
            checks.append(_check("EvidenceItem write is present or deferred to import-evidence", bool(evidence_writes) or module in {"bulk_deg", "sasp_score", "annotation", "cell_type_evidence"}, severity="review"))
    if module == "sasp_score":
        checks.append(_check("SASP claim remains phenotype/program evidence", "causal" not in str(params.get("claim_limit", "")).lower()))
    if module == "cell_type_evidence":
        checks.append(_check("cell type provenance is required", True))
    return _layer("Biological", checks)


def _layer(name: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    if any(not check["passed"] and check.get("severity") == "fail" for check in checks):
        status = "fail"
    elif any(not check["passed"] for check in checks):
        status = "review"
    else:
        status = "pass"
    return {
        "layer": name,
        "status": status,
        "checks": checks,
        "blocking_reasons": [check["name"] for check in checks if not check["passed"] and check.get("severity") == "fail"],
        "warnings": [check["name"] for check in checks if not check["passed"] and check.get("severity") != "fail"],
    }


def _check(name: str, passed: bool, severity: str = "fail") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "severity": severity}


def _dataset_profile(project_dir: Path, dataset_id: str) -> dict[str, Any]:
    if not dataset_id:
        return {}
    payload = _read_json(project_dir / "results" / "evidence_planning" / "dataset_profiles.json", {})
    for profile in payload.get("profiles", []):
        if profile.get("dataset_id") == dataset_id:
            return profile
    return {}


def _module_qc(project_dir: Path, order: dict[str, Any], dispatch: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for artifact in dispatch.get("artifacts", []) or []:
        if str(artifact).endswith("qc_summary.json"):
            candidates.append(project_dir / artifact)
    module = order.get("module", "")
    dataset_id = order.get("dataset_id", "")
    if module == "bulk_deg" and dataset_id:
        candidates.append(project_dir / "results" / f"bulk_deg_{dataset_id}" / "qc_summary.json")
    if module == "scrna_pseudobulk" and dataset_id:
        candidates.append(project_dir / "results" / f"scrna_pseudobulk_{dataset_id}" / "qc_summary.json")
    if module in {"enrichment", "meta_analysis", "genetic_coloc_mr"}:
        candidates.extend(sorted((project_dir / "results").glob(f"{module}*/qc_summary.json")))
    for path in candidates:
        payload = _read_json(path, {})
        if payload:
            return payload
    return {}


def _write_report(project_dir: Path, report: dict[str, Any]) -> None:
    out_dir = project_dir / "results" / "qc"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_id = "qc_" + content_hash({"work_order": report["work_order_id"], "module": report["module_id"], "time": report["generated_at"]})[:16]
    report["qc_report_id"] = report_id
    report_path = out_dir / f"{report_id}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    index = load_task_qc_index(project_dir)
    index["reports"] = [row for row in index.get("reports", []) if row.get("work_order_id") != report["work_order_id"]]
    index["reports"].append(
        {
            "qc_report_id": report_id,
            "work_order_id": report["work_order_id"],
            "module_id": report["module_id"],
            "dataset_id": report.get("dataset_id", ""),
            "overall_status": report["overall_status"],
            "path": str(report_path.relative_to(project_dir)).replace("\\", "/"),
            "generated_at": report["generated_at"],
        }
    )
    index["updated_at"] = _now()
    _index_path(project_dir).write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _index_path(project_dir: Path) -> Path:
    return project_dir / "results" / "qc" / "task_qc_reports.json"


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _validate(report: dict[str, Any]) -> None:
    errors = validate_object(report, load_schema("task_qc_report.schema.json"), "TaskQCReport")
    if errors:
        raise ValueError("; ".join(errors))


def _dedupe(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
