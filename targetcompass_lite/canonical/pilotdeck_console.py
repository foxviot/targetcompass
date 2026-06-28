from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .backend_access import load_artifact_registry_preferred
from .nextflow_execution import load_qc_reports, load_task_runs
from .schemas import now_iso


PILOTDECK_CONSOLE_SCHEMA = "v5.pilotdeck_console/0.1"


def build_pilotdeck_console(project_dir: str | Path, *, write: bool = True) -> dict[str, Any]:
    project_dir = Path(project_dir)
    artifacts = load_artifact_registry_preferred(project_dir).get("artifacts", [])
    task_runs = load_task_runs(project_dir)
    qc_reports = load_qc_reports(project_dir)
    evidence_query = _read_json(project_dir / "v4" / "evidence_db_last_query.json", {})
    trace_query = _read_json(project_dir / "v4" / "evidence_trace_last_query.json", {})
    approval = _read_json(project_dir / "results" / "approval_state.json", {})
    recovery = _load_failure_recovery(project_dir)
    nextflow = _read_json(project_dir / "v5" / "nextflow" / "production_validation.json", {})
    llm = _read_json(project_dir / "v5" / "llm_roles" / "llm_orchestration_run.json", {})
    codex = _read_json(project_dir / "v5" / "codex" / "worker_registry.json", {})
    payload = {
        "schema_version": PILOTDECK_CONSOLE_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "project_management": {
            "current_project": project_dir.name,
            "project_state_ref": "v5/project_state.json",
            "create_project_cli": "python tc_lite.py init --project <name>",
        },
        "run_history": {
            "task_run_count": len(task_runs),
            "recent_task_runs": task_runs[-10:],
            "llm_orchestration_status": llm.get("status", "not_run"),
            "nextflow_status": nextflow.get("status", "not_run"),
        },
        "approval_detail": {
            "approval_status": approval.get("status", "draft"),
            "review_count": len(approval.get("reviews", [])) if isinstance(approval.get("reviews", []), list) else approval.get("review_count", 0),
            "signoff_ref": "results/approval_state.json" if approval else "",
        },
        "failure_recovery": {
            "status": recovery.get("status", "not_run"),
            "source_ref": recovery.get("source_ref", ""),
            "open_count": recovery.get("open_count", recovery.get("item_count", 0)),
            "items": recovery.get("items", [])[:10],
            "nextflow_recovery": nextflow.get("recovery", {}),
        },
        "artifact_drilldown": {
            "artifact_count": len(artifacts),
            "recent_artifacts": artifacts[-10:],
        },
        "evidence_drilldown": {
            "last_query_ref": "v4/evidence_db_last_query.json" if evidence_query else "",
            "match_count": evidence_query.get("match_count", 0),
            "items": evidence_query.get("items", [])[:10],
        },
        "claim_drilldown": {
            "trace_query_ref": "v4/evidence_trace_last_query.json" if trace_query else "",
            "trace_items": trace_query.get("items", [])[:10],
            "claim_ceiling_ref": "v5/reports/canonical_report_manifest.json",
        },
        "qc_summary": {
            "qc_report_count": len(qc_reports),
            "failed_qc_count": len([row for row in qc_reports if str(row.get("overall_status", "")).lower() not in {"pass", "passed"}]),
        },
    }
    if write:
        out = project_dir / "v5" / "pilotdeck" / "console.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _load_failure_recovery(project_dir: Path) -> dict[str, Any]:
    v5_path = project_dir / "v5" / "recovery" / "failure_recovery_report.json"
    v5 = _read_json(v5_path, {})
    if v5:
        v5 = dict(v5)
        v5["source_ref"] = "v5/recovery/failure_recovery_report.json"
        v5["open_count"] = len([row for row in v5.get("items", []) if row.get("status", "open") == "open"])
        return v5
    legacy_path = project_dir / "results" / "recovery_manifest.json"
    legacy = _read_json(legacy_path, {})
    if legacy:
        legacy = dict(legacy)
        legacy["source_ref"] = "results/recovery_manifest.json"
        legacy.setdefault("status", "legacy")
        return legacy
    return {"status": "not_run", "source_ref": "", "open_count": 0, "items": []}
