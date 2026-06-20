import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_consistency_check(project_dir: Path) -> dict[str, Any]:
    report = _read_json(project_dir / "reports" / "target_report_structured.json", {})
    evidence_index = _read_json(project_dir / "v4" / "evidence_review_report_index.json", {})
    approval = _read_json(project_dir / "results" / "approval_state.json", {})
    dag = _read_json(project_dir / "v4" / "work_order_dag.json", {})
    review_queue = _read_json(project_dir / "results" / "review_queue.json", {"items": [], "queue_count": 0})
    checks = [
        _check_report_uses_current_index(project_dir, report, evidence_index),
        _check_signoff_trace_hashes(project_dir, approval),
        _check_dag_evidence_writes(dag),
        _check_review_queue(review_queue),
    ]
    payload = {
        "schema_version": "v4.consistency_check/0.1",
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(row["status"] == "PASS" for row in checks) else "REVIEW",
        "checks": checks,
    }
    path = consistency_check_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def consistency_check_path(project_dir: Path) -> Path:
    path = project_dir / "v4" / "consistency_check.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _check_report_uses_current_index(project_dir: Path, report: dict[str, Any], evidence_index: dict[str, Any]) -> dict[str, Any]:
    report_index = report.get("evidence_review_report_index", {}) if isinstance(report, dict) else {}
    expected_path = "v4/evidence_review_report_index.json"
    current_id = evidence_index.get("index_id", "")
    report_id = report_index.get("index_id", "")
    ok = bool(report_index) and report_index.get("path") == expected_path and report_id == current_id
    return {
        "check": "report_references_current_evidence_index",
        "status": "PASS" if ok else "REVIEW",
        "detail": "report structured JSON references current evidence index" if ok else "report structured JSON is missing or references an older evidence index",
        "expected": {"path": expected_path, "index_id": current_id},
        "observed": {"path": report_index.get("path", ""), "index_id": report_id},
    }


def _check_signoff_trace_hashes(project_dir: Path, approval: dict[str, Any]) -> dict[str, Any]:
    if approval.get("status") != "signed_off":
        return {
            "check": "signoff_trace_hash_matches_current_artifacts",
            "status": "REVIEW",
            "detail": "project is not signed off; no frozen traceability hash is expected yet",
            "expected": "signed_off approval state",
            "observed": approval.get("status", "draft"),
        }
    snapshot = approval.get("traceability_snapshot", {})
    mismatches = []
    for name, row in snapshot.items():
        path = project_dir / row.get("path", "")
        expected_hash = row.get("hash", "")
        observed_hash = _file_hash(path) if path.exists() else ""
        if expected_hash != observed_hash:
            mismatches.append({"artifact": name, "path": row.get("path", ""), "expected_hash": expected_hash, "observed_hash": observed_hash})
    ok = bool(snapshot) and not mismatches
    return {
        "check": "signoff_trace_hash_matches_current_artifacts",
        "status": "PASS" if ok else "REVIEW",
        "detail": "signed-off traceability hashes match current artifacts" if ok else "signed-off traceability hashes are missing or stale",
        "mismatches": mismatches,
    }


def _check_dag_evidence_writes(dag: dict[str, Any]) -> dict[str, Any]:
    nodes = dag.get("nodes", []) if isinstance(dag, dict) else []
    nodes_with_outputs = [row for row in nodes if row.get("outputs")]
    nodes_with_evidence = [row for row in nodes if row.get("evidence_writes")]
    ok = bool(nodes) and bool(nodes_with_evidence)
    return {
        "check": "dag_contains_evidence_writes",
        "status": "PASS" if ok else "REVIEW",
        "detail": "WorkOrder DAG contains evidence_writes links" if ok else "WorkOrder DAG has no evidence_writes links yet",
        "node_count": len(nodes),
        "nodes_with_outputs": len(nodes_with_outputs),
        "nodes_with_evidence_writes": len(nodes_with_evidence),
    }


def _check_review_queue(review_queue: dict[str, Any]) -> dict[str, Any]:
    pending = [
        row
        for row in review_queue.get("items", [])
        if row.get("review_status", "pending") not in {"approve", "accepted"}
    ]
    return {
        "check": "review_queue_has_no_pending_items",
        "status": "PASS" if not pending else "REVIEW",
        "detail": "review queue is empty" if not pending else f"{len(pending)} review item(s) still pending",
        "pending_count": len(pending),
        "pending_items": pending[:20],
    }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
