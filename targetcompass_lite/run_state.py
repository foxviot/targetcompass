import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def status_path(project_dir: Path) -> Path:
    return project_dir / "results" / "run_status.json"


def cancel_path(project_dir: Path) -> Path:
    return project_dir / "results" / "cancel_requested.json"


def new_run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:8]


def read_status(project_dir: Path) -> dict:
    path = status_path(project_dir)
    if not path.exists():
        return {
            "run_id": "",
            "status": "idle",
            "message": "No workflow run recorded yet.",
            "failure_reason": "",
            "active_stage": "",
            "stdout": "",
            "stderr": "",
            "stages": [],
            "last_request": {},
            "cancel_requested": False,
            "updated_at": "",
        }
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(
    project_dir: Path,
    status: str,
    message: str,
    stdout: str = "",
    stderr: str = "",
    stages: list[dict] | None = None,
    run_id: str | None = None,
    last_request: dict | None = None,
    failure_reason: str = "",
    active_stage: str = "",
) -> dict:
    previous = read_status(project_dir)
    payload = {
        "run_id": run_id or previous.get("run_id") or new_run_id(),
        "status": status,
        "message": message,
        "failure_reason": failure_reason,
        "active_stage": active_stage or _active_stage(stages or []),
        "stdout": stdout,
        "stderr": stderr,
        "stages": stages or [],
        "last_request": last_request if last_request is not None else previous.get("last_request", {}),
        "cancel_requested": cancel_path(project_dir).exists(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = status_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def request_cancel(project_dir: Path, reason: str = "user_requested") -> dict:
    payload = {"requested_at": datetime.now(timezone.utc).isoformat(), "reason": reason}
    path = cancel_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    status = read_status(project_dir)
    write_status(
        project_dir,
        status.get("status", "idle"),
        status.get("message", ""),
        status.get("stdout", ""),
        status.get("stderr", ""),
        status.get("stages", []),
        status.get("run_id") or None,
        status.get("last_request", {}),
        status.get("failure_reason", ""),
        status.get("active_stage", ""),
    )
    return payload


def clear_cancel(project_dir: Path) -> None:
    cancel_path(project_dir).unlink(missing_ok=True)


def check_cancelled(project_dir: Path) -> None:
    path = cancel_path(project_dir)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        raise RuntimeError(f"Run cancelled: {data.get('reason', 'user_requested')}")


def _active_stage(stages: list[dict]) -> str:
    for stage in reversed(stages):
        if stage.get("status") == "running":
            return stage.get("name", "")
    return stages[-1].get("name", "") if stages else ""
