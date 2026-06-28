import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, finish_work_order_attempt, read_work_order_attempts, v4_dir, write_work_order_attempts


ENGINEERING_CLOSURE_SCHEMA = "v4.engineering_closure/0.1"


def refresh_engineering_closure(project_dir: Path) -> dict[str, Any]:
    from .codex_engineering import load_codex_engineering
    from .evidence_db import build_evidence_db_snapshot
    from .trace_orchestrator import refresh_traceability

    engineering = load_codex_engineering(project_dir)
    evidence_snapshot = build_evidence_db_snapshot(project_dir)
    traceability = refresh_traceability(project_dir, include_review_queue=True)
    attempts = read_work_order_attempts(project_dir).get("attempts", [])
    result_links = []
    for result in engineering.get("results", []):
        linked_attempts = _link_attempts(project_dir, result, attempts)
        result_links.append(
            {
                "result_id": result.get("result_id", ""),
                "codex_job_id": result.get("codex_job_id", ""),
                "work_order_id": result.get("work_order_id", ""),
                "status": result.get("status", ""),
                "merge_status": result.get("merge_status", ""),
                "review_status": result.get("review_status", ""),
                "patch_refs": result.get("patch_refs", []),
                "test_refs": result.get("test_refs", []),
                "artifact_count": len(result.get("artifacts", [])),
                "linked_attempt_ids": [row.get("attempt_id", "") for row in linked_attempts],
                "evidence_snapshot_id": evidence_snapshot.get("snapshot_hash", ""),
                "traceability_ref": "v4/traceability_refresh.json",
            }
        )
    payload = {
        "schema_version": ENGINEERING_CLOSURE_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result_count": len(result_links),
        "approved_for_merge_count": len([row for row in result_links if row.get("merge_status") == "approved_for_merge"]),
        "blocked_count": len([row for row in result_links if row.get("merge_status") in {"blocked", "merge_blocked"}]),
        "evidence_db_snapshot": "v4/evidence_db_snapshot.json",
        "evidence_snapshot_hash": evidence_snapshot.get("snapshot_hash", ""),
        "traceability_hash": content_hash(traceability),
        "results": result_links,
    }
    out = engineering_closure_path(project_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def engineering_closure_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "codex_engineering" / "engineering_closure.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _link_attempts(project_dir: Path, result: dict[str, Any], attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked = [
        row
        for row in attempts
        if row.get("work_order_id") == result.get("work_order_id")
        or row.get("metadata", {}).get("executor_dispatch", {}).get("codex_result", {}).get("result_id") == result.get("result_id")
    ]
    if linked:
        return linked
    if not result.get("work_order_id"):
        return []
    synthetic = {
        "attempt_id": "attempt_codex_" + content_hash({"result": result.get("result_id", ""), "work_order": result.get("work_order_id", "")})[:16],
        "work_order_id": result.get("work_order_id", ""),
        "module_id": "",
        "run_id": "codex_engineering",
        "status": "engineering_review_required" if result.get("status") == "success" else "failed",
        "started_at": result.get("recorded_at", ""),
        "finished_at": result.get("recorded_at", ""),
        "failure_reason": result.get("failure_reason", ""),
        "artifacts": result.get("artifacts", []),
        "resume_key": result.get("codex_job_id", ""),
        "metadata": {"codex_result": result},
    }
    manifest = read_work_order_attempts(project_dir)
    if not any(row.get("attempt_id") == synthetic["attempt_id"] for row in manifest.get("attempts", [])):
        manifest.setdefault("attempts", []).append(synthetic)
        write_work_order_attempts(project_dir, manifest)
    return [synthetic]


def record_engineering_attempt_update(project_dir: Path, result: dict[str, Any]) -> None:
    attempts = read_work_order_attempts(project_dir).get("attempts", [])
    linked = _link_attempts(project_dir, result, attempts)
    for attempt in linked:
        finish_work_order_attempt(
            project_dir,
            attempt["attempt_id"],
            attempt.get("status", "engineering_review_required"),
            artifacts=result.get("artifacts", []),
            failure_reason=result.get("failure_reason", ""),
            metadata={"codex_result": result},
        )
