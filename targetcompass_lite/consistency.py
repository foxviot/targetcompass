import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_consistency_check(project_dir: Path) -> dict[str, Any]:
    report = _read_json(project_dir / "reports" / "target_report_structured.json", {})
    evidence_index = _read_json(project_dir / "v4" / "evidence_review_report_index.json", {})
    evidence_snapshot = _read_json(project_dir / "v4" / "evidence_db_snapshot.json", {})
    approval = _read_json(project_dir / "results" / "approval_state.json", {})
    dag = _read_json(project_dir / "v4" / "work_order_dag.json", {})
    review_queue = _read_json(project_dir / "results" / "review_queue.json", {"items": [], "queue_count": 0})
    engineering_closure = _read_json(project_dir / "v4" / "codex_engineering" / "engineering_closure.json", {})
    storage = _read_json(project_dir / "v4" / "storage_backend_manifest.json", {})
    checks = [
        _check_report_uses_current_index(project_dir, report, evidence_index),
        _check_evidence_snapshot_matches_index(evidence_snapshot, evidence_index),
        _check_evidence_db_indexes(evidence_snapshot),
        _check_storage_backend_contract(storage, evidence_snapshot),
        _check_codex_engineering_closure(engineering_closure),
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


def _check_evidence_snapshot_matches_index(evidence_snapshot: dict[str, Any], evidence_index: dict[str, Any]) -> dict[str, Any]:
    snapshot_count = evidence_snapshot.get("row_count")
    index_count = evidence_index.get("evidence_count")
    ok = isinstance(snapshot_count, int) and snapshot_count == index_count
    return {
        "check": "evidence_snapshot_matches_trace_index",
        "status": "PASS" if ok else "REVIEW",
        "detail": "Evidence DB snapshot row count matches trace index evidence count" if ok else "Evidence DB snapshot is missing or does not match trace index",
        "expected": {"evidence_count": index_count},
        "observed": {"row_count": snapshot_count, "snapshot_hash": evidence_snapshot.get("snapshot_hash", "")},
    }


def _check_evidence_db_indexes(evidence_snapshot: dict[str, Any]) -> dict[str, Any]:
    index_names = {row.get("name", "") for row in evidence_snapshot.get("indexes", [])}
    required = {
        "idx_evidence_entity_symbol",
        "idx_evidence_type",
        "idx_evidence_dataset",
        "idx_evidence_review_status",
        "idx_evidence_artifact",
        "idx_evidence_run",
        "idx_evidence_gene_type",
    }
    missing = sorted(required - index_names)
    ok = not missing and evidence_snapshot.get("evidence_schema_version")
    return {
        "check": "evidence_db_has_required_indexes",
        "status": "PASS" if ok else "REVIEW",
        "detail": "Evidence DB production indexes are present" if ok else "Evidence DB migration/index snapshot is incomplete",
        "missing_indexes": missing,
        "schema_version": evidence_snapshot.get("evidence_schema_version", ""),
    }


def _check_storage_backend_contract(storage: dict[str, Any], evidence_snapshot: dict[str, Any]) -> dict[str, Any]:
    active = storage.get("active_backends", {}) if isinstance(storage, dict) else {}
    sqlite_fallback_ok = bool(storage.get("sqlite_local", {}).get("exists"))
    sqlite_ok = active.get("evidence_db") == "sqlite_local" and sqlite_fallback_ok
    postgres = storage.get("postgres_contract", {}) if isinstance(storage, dict) else {}
    postgres_ok = (
        active.get("evidence_db") == "postgres_local"
        and postgres.get("enabled") is True
        and postgres.get("migration_mode") == "active_local_docker"
        and sqlite_fallback_ok
    )
    snapshot_ref_ok = evidence_snapshot.get("storage_backend_ref") == "v4/storage_backend_manifest.json"
    ok = bool((sqlite_ok or postgres_ok) and snapshot_ref_ok)
    return {
        "check": "storage_backend_manifest_is_current",
        "status": "PASS" if ok else "REVIEW",
        "detail": "Evidence/Report storage backend manifest is present and referenced by Evidence DB snapshot" if ok else "storage backend manifest is missing or not referenced by Evidence DB snapshot",
        "active_backends": active,
        "sqlite_fallback_exists": sqlite_fallback_ok,
        "postgres_contract_enabled": postgres.get("enabled", False),
        "postgres_migration_mode": postgres.get("migration_mode", ""),
        "snapshot_storage_ref": evidence_snapshot.get("storage_backend_ref", ""),
    }


def _check_codex_engineering_closure(closure: dict[str, Any]) -> dict[str, Any]:
    if not closure:
        return {
            "check": "codex_engineering_results_have_closure",
            "status": "PASS",
            "detail": "no Codex engineering result closure required yet",
            "result_count": 0,
        }
    missing = [
        row
        for row in closure.get("results", [])
        if not row.get("linked_attempt_ids") or not row.get("evidence_snapshot_id")
    ]
    ok = not missing
    return {
        "check": "codex_engineering_results_have_closure",
        "status": "PASS" if ok else "REVIEW",
        "detail": "Codex engineering results are linked to WorkOrder attempts and Evidence snapshot" if ok else "some Codex engineering results lack attempt or Evidence snapshot links",
        "result_count": closure.get("result_count", 0),
        "missing_count": len(missing),
        "missing": missing[:20],
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
