from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codex_worker_execution import execute_claimed_codex_task
from .codex_worker_protocol import approve_task, claim_task, export_task_packet, load_worker_queue
from .schemas import now_iso


CODEX_REGISTRY_SCHEMA = "v5.codex_worker_registry/0.1"


def run_approved_codex_worker_task(
    project_dir: str | Path,
    packet: dict[str, Any],
    *,
    approver: str = "reviewer",
    worker_id: str = "local_codex_worker",
    executor=None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    task_id = packet.get("task_id", "")
    export_task_packet(project_dir, packet)
    approve_task(project_dir, task_id, approver)
    claim_task(project_dir, worker_id, task_id)
    result = execute_claimed_codex_task(project_dir, task_id, worker_id, executor=executor)
    registry = refresh_codex_worker_registry(project_dir)
    return {"status": result.get("status"), "result": result, "registry": registry}


def refresh_codex_worker_registry(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    queue = load_worker_queue(project_dir)
    completed = queue.get("completed", [])
    failed = queue.get("failed", [])
    patches = []
    tests = []
    results = []
    for record in completed + failed:
        output = record.get("output_manifest", {})
        task_id = record.get("task_id", "")
        patch_refs = output.get("patch_refs", []) or []
        test_refs = output.get("test_refs", []) or []
        result_ref = output.get("result_ref", "")
        if patch_refs:
            patches.append({"task_id": task_id, "patch_refs": patch_refs, "status": "pending_review"})
        if test_refs:
            tests.append({"task_id": task_id, "test_refs": test_refs, "status": "recorded"})
        results.append(
            {
                "task_id": task_id,
                "status": record.get("status", ""),
                "result_ref": result_ref,
                "artifact_refs": output.get("artifact_manifest_refs", []),
                "merge_status": _merge_status(record, output),
                "failure_reason": record.get("failure_reason", ""),
            }
        )
    payload = {
        "schema_version": CODEX_REGISTRY_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "queue_counts": {name: len(rows) for name, rows in queue.items()},
        "patch_registry": patches,
        "test_registry": tests,
        "result_registry": results,
        "ready_for_merge_count": len([row for row in results if row.get("merge_status") == "ready_for_human_merge_approval"]),
        "blocked_count": len([row for row in results if row.get("merge_status") == "blocked"]),
        "policy": {
            "approval_required_before_claim": True,
            "worker_id_must_match": True,
            "allowed_paths_enforced": True,
            "forbidden_paths_enforced": True,
            "merge_requires_human_approval": True,
            "subprocess_execution": "only inside approved executor contract",
        },
    }
    out = project_dir / "v5" / "codex" / "worker_registry.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _merge_status(record: dict[str, Any], output: dict[str, Any]) -> str:
    if record.get("status") != "completed":
        return "blocked"
    if not output.get("patch_refs"):
        return "no_patch_to_merge"
    if not output.get("test_refs"):
        return "blocked"
    return "ready_for_human_merge_approval"
