from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ids import make_stable_id
from .schemas import now_iso


TASK_STATUSES = {
    "draft",
    "pending_approval",
    "approved",
    "claimed",
    "running",
    "completed",
    "failed",
    "released",
    "expired",
    "cancelled",
}

QUEUE_DIRS = ["pending", "approved", "claimed", "completed", "failed"]

REQUIRED_ENGINEERING_FORBIDDEN_PATHS = [
    ".git/",
    "secrets",
    ".env",
    "raw_data/",
    "external_agent_runs/*/mock_run/",
]

DEFAULT_LEASE_MINUTES = 30


def export_task_packet(project_dir: str | Path, packet: dict[str, Any]) -> dict[str, Any]:
    _validate_packet_for_export(packet)
    task_id = packet.get("task_id") or make_stable_id("codex_task", packet)
    packet = dict(packet)
    packet["task_id"] = task_id
    record = _build_record(packet, status="pending_approval")
    _write_record(project_dir, "pending", record)
    return record


def request_task_approval(project_dir: str | Path, task_id: str) -> dict[str, Any]:
    record, queue = _load_task_record(project_dir, task_id)
    if queue != "pending":
        raise ValueError(f"task {task_id} is not pending")
    record["status"] = "pending_approval"
    _append_history(record, "approval_requested", actor="system", message="Task approval requested.")
    _write_record(project_dir, "pending", record)
    return record


def approve_task(project_dir: str | Path, task_id: str, actor: str) -> dict[str, Any]:
    record, queue = _load_task_record(project_dir, task_id)
    if queue != "pending" or record.get("status") != "pending_approval":
        raise ValueError(f"task {task_id} must be pending_approval before approval")
    record["status"] = "approved"
    record["approved_by"] = actor
    record["approved_at"] = now_iso()
    _append_history(record, "approved", actor=actor, message="Task approved for claim.")
    _move_record(project_dir, task_id, from_queue=queue, to_queue="approved", record=record)
    return record


def reject_task(project_dir: str | Path, task_id: str, actor: str, reason: str) -> dict[str, Any]:
    record, queue = _load_task_record(project_dir, task_id)
    if queue not in {"pending", "approved"}:
        raise ValueError(f"task {task_id} cannot be rejected from {queue}")
    record["status"] = "failed"
    record["rejected_by"] = actor
    record["rejected_at"] = now_iso()
    record["failure_reason"] = reason
    _append_history(record, "rejected", actor=actor, message=reason)
    _move_record(project_dir, task_id, from_queue=queue, to_queue="failed", record=record)
    return record


def claim_task(project_dir: str | Path, worker_id: str, task_id: str = "") -> dict[str, Any]:
    if not worker_id:
        raise ValueError("worker_id is required")
    if task_id:
        record, queue = _load_task_record(project_dir, task_id)
        if queue == "claimed" and _is_expired(record):
            record["status"] = "expired"
        elif queue != "approved" or record.get("status") != "approved":
            raise ValueError(f"only approved tasks can be claimed: {task_id}")
    else:
        found = _find_first_claimable(project_dir)
        if not found:
            raise ValueError("no approved or expired task is claimable")
        record, queue = found
        task_id = record["task_id"]

    claimed_at = datetime.now(timezone.utc)
    record["status"] = "claimed"
    record["worker_id"] = worker_id
    record["claimed_at"] = claimed_at.isoformat()
    record["lease_expires_at"] = (claimed_at + timedelta(minutes=DEFAULT_LEASE_MINUTES)).isoformat()
    _append_history(record, "claimed", actor=worker_id, message="Task claimed with lease.")
    _move_record(project_dir, task_id, from_queue=queue, to_queue="claimed", record=record)
    return record


def release_task(project_dir: str | Path, task_id: str, worker_id: str, reason: str) -> dict[str, Any]:
    record, queue = _load_task_record(project_dir, task_id)
    if queue != "claimed":
        raise ValueError(f"task {task_id} is not claimed")
    _require_worker(record, worker_id)
    record["status"] = "approved"
    record["released_by"] = worker_id
    record["released_at"] = now_iso()
    record["release_reason"] = reason
    record.pop("worker_id", None)
    record.pop("claimed_at", None)
    record.pop("lease_expires_at", None)
    _append_history(record, "released", actor=worker_id, message=reason)
    _move_record(project_dir, task_id, from_queue="claimed", to_queue="approved", record=record)
    return record


def complete_task(project_dir: str | Path, task_id: str, worker_id: str, output_manifest: dict[str, Any]) -> dict[str, Any]:
    if not output_manifest:
        raise ValueError("output_manifest is required")
    record, queue = _load_task_record(project_dir, task_id)
    if queue != "claimed":
        raise ValueError(f"task {task_id} is not claimed")
    _require_worker(record, worker_id)
    record["status"] = "completed"
    record["completed_by"] = worker_id
    record["completed_at"] = now_iso()
    record["output_manifest"] = output_manifest
    _append_history(record, "completed", actor=worker_id, message="Task completed.")
    _move_record(project_dir, task_id, from_queue="claimed", to_queue="completed", record=record)
    return record


def fail_task(project_dir: str | Path, task_id: str, worker_id: str, failure_reason: str) -> dict[str, Any]:
    record, queue = _load_task_record(project_dir, task_id)
    if queue != "claimed":
        raise ValueError(f"task {task_id} is not claimed")
    _require_worker(record, worker_id)
    record["status"] = "failed"
    record["failed_by"] = worker_id
    record["failed_at"] = now_iso()
    record["failure_reason"] = failure_reason
    _append_history(record, "failed", actor=worker_id, message=failure_reason)
    _move_record(project_dir, task_id, from_queue="claimed", to_queue="failed", record=record)
    return record


def load_worker_queue(project_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    return {queue: [_read_json(path) for path in sorted(_queue_dir(project_dir, queue).glob("*.json"))] for queue in QUEUE_DIRS}


def _build_record(packet: dict[str, Any], status: str) -> dict[str, Any]:
    task_id = packet["task_id"]
    return {
        "schema_version": "v5.codex_worker_task/0.1",
        "queue_record_id": make_stable_id("codex_queue_record", {"task_id": task_id, "packet_hash": make_stable_id("packet", packet)}),
        "task_id": task_id,
        "packet_type": packet.get("packet_type", ""),
        "status": status,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "packet": packet,
        "history": [{"event": "exported", "actor": "system", "created_at": now_iso(), "message": "Task exported for approval."}],
    }


def _validate_packet_for_export(packet: dict[str, Any]) -> None:
    if not packet.get("packet_type"):
        raise ValueError("packet_type is required")
    if packet.get("packet_type") == "EngineeringTaskPacket":
        if not packet.get("allowed_paths"):
            raise ValueError("EngineeringTaskPacket requires allowed_paths")
        forbidden_paths = packet.get("forbidden_paths") or []
        missing_forbidden = [path for path in REQUIRED_ENGINEERING_FORBIDDEN_PATHS if path not in forbidden_paths]
        if missing_forbidden:
            raise ValueError(f"EngineeringTaskPacket forbidden_paths missing required entries: {', '.join(missing_forbidden)}")


def _queue_dir(project_dir: str | Path, queue: str) -> Path:
    if queue not in QUEUE_DIRS:
        raise ValueError(f"unknown queue: {queue}")
    path = Path(project_dir) / "v5" / "codex" / queue
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(project_dir: str | Path, queue: str, task_id: str) -> Path:
    return _queue_dir(project_dir, queue) / f"{task_id}.json"


def _write_record(project_dir: str | Path, queue: str, record: dict[str, Any]) -> None:
    record["updated_at"] = now_iso()
    path = _record_path(project_dir, queue, record["task_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _move_record(project_dir: str | Path, task_id: str, from_queue: str, to_queue: str, record: dict[str, Any]) -> None:
    _write_record(project_dir, to_queue, record)
    old_path = _record_path(project_dir, from_queue, task_id)
    if old_path.exists() and old_path != _record_path(project_dir, to_queue, task_id):
        old_path.unlink()


def _load_task_record(project_dir: str | Path, task_id: str) -> tuple[dict[str, Any], str]:
    for queue in QUEUE_DIRS:
        path = _record_path(project_dir, queue, task_id)
        if path.exists():
            return _read_json(path), queue
    raise FileNotFoundError(f"task not found: {task_id}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_history(record: dict[str, Any], event: str, actor: str, message: str) -> None:
    record.setdefault("history", []).append({"event": event, "actor": actor, "created_at": now_iso(), "message": message})


def _require_worker(record: dict[str, Any], worker_id: str) -> None:
    if record.get("worker_id") != worker_id:
        raise ValueError(f"worker_id mismatch for task {record.get('task_id')}")


def _find_first_claimable(project_dir: str | Path) -> tuple[dict[str, Any], str] | None:
    approved = sorted(_queue_dir(project_dir, "approved").glob("*.json"))
    if approved:
        return _read_json(approved[0]), "approved"
    for path in sorted(_queue_dir(project_dir, "claimed").glob("*.json")):
        record = _read_json(path)
        if _is_expired(record):
            return record, "claimed"
    return None


def _is_expired(record: dict[str, Any]) -> bool:
    raw = record.get("lease_expires_at")
    if not raw:
        return False
    return datetime.fromisoformat(raw) <= datetime.now(timezone.utc)
