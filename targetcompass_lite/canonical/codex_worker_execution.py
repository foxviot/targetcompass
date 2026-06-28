from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib import request

from .artifacts import register_artifact
from .codex_worker_protocol import (
    REQUIRED_ENGINEERING_FORBIDDEN_PATHS,
    complete_task,
    fail_task,
    load_worker_queue,
)
from .schemas import now_iso


CodexExecutor = Callable[[Path, dict[str, Any]], dict[str, Any]]


def execute_claimed_codex_task(
    project_dir: str | Path,
    task_id: str,
    worker_id: str,
    *,
    executor: CodexExecutor | None = None,
) -> dict[str, Any]:
    """Execute a claimed v5 EngineeringTaskPacket and write back worker protocol state."""
    project_dir = Path(project_dir)
    record = _load_claimed_record(project_dir, task_id)
    _require_worker(record, worker_id)
    packet = record.get("packet", {})
    record["status"] = "running"
    record["running_at"] = now_iso()
    try:
        _validate_engineering_packet_for_execution(packet)
        output_manifest = (executor or _default_v4_codex_executor)(project_dir, record)
        _validate_output_manifest(output_manifest)
        artifact_manifests = _register_output_artifacts(project_dir, task_id, output_manifest)
        output_manifest = dict(output_manifest)
        output_manifest["artifact_manifest_refs"] = [artifact["artifact_id"] for artifact in artifact_manifests]
        completed = complete_task(project_dir, task_id, worker_id, output_manifest)
        return {"status": "completed", "record": completed, "output_manifest": output_manifest, "artifacts": artifact_manifests}
    except Exception as exc:
        failed = fail_task(project_dir, task_id, worker_id, str(exc))
        return {"status": "failed", "record": failed, "failure_reason": str(exc), "artifacts": []}


def _default_v4_codex_executor(project_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    packet = record.get("packet", {})
    codex_job_id = packet.get("codex_job_id")
    if not codex_job_id:
        raise ValueError("EngineeringTaskPacket requires codex_job_id for default v4 Codex executor")
    from targetcompass_lite.codex_engineering import run_codex_task_tests

    result = run_codex_task_tests(project_dir, codex_job_id, actor=record.get("worker_id", "codex"), allow_unapproved_dispatch=False)
    status = result.get("status", "")
    if status != "success":
        raise RuntimeError(result.get("failure_reason") or f"Codex engineering result status={status}")
    return {
        "schema_version": "v5.codex_worker_output/0.1",
        "executor": "v4.codex_engineering.run_codex_task_tests",
        "codex_job_id": codex_job_id,
        "result_ref": result.get("result_id", ""),
        "artifacts": [{"path": path, "artifact_type": "codex_engineering_artifact"} for path in result.get("artifacts", [])],
        "patch_refs": result.get("patch_refs", []),
        "test_refs": result.get("test_refs", []),
        "created_at": now_iso(),
    }


def build_subprocess_codex_executor(command: list[str], *, timeout_seconds: int = 300) -> CodexExecutor:
    """Build a controlled subprocess executor for an approved Codex worker task.

    The command is executed only after the task was approved, claimed by the
    matching worker, and passed path-policy validation. The task packet and
    claimed record are passed through environment variables so an external
    Codex wrapper can read them without broad filesystem privileges.
    """

    if not command:
        raise ValueError("subprocess Codex executor requires a command")
    if any(not str(part).strip() for part in command):
        raise ValueError("subprocess Codex executor command contains an empty argument")

    def executor(project_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
        output_dir = project_dir / "v5" / "codex_outputs" / record.get("task_id", "task")
        output_dir.mkdir(parents=True, exist_ok=True)
        request_path = output_dir / "worker_request.json"
        response_path = output_dir / "worker_response.json"
        request_payload = {
            "schema_version": "v5.codex_worker_subprocess_request/0.1",
            "project_dir": str(project_dir),
            "task_id": record.get("task_id", ""),
            "worker_id": record.get("worker_id", ""),
            "packet": record.get("packet", {}),
            "expected_response_path": str(response_path),
        }
        request_path.write_text(json.dumps(request_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "TARGETCOMPASS_CODEX_WORKER_REQUEST": str(request_path),
                "TARGETCOMPASS_CODEX_WORKER_RESPONSE": str(response_path),
                "TARGETCOMPASS_PROJECT_DIR": str(project_dir),
                "TARGETCOMPASS_CODEX_TASK_ID": record.get("task_id", ""),
            }
        )
        completed = subprocess.run(
            command,
            cwd=project_dir.parent.parent if project_dir.parent.name == "projects" else project_dir,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        stdout_path = output_dir / "subprocess_stdout.txt"
        stderr_path = output_dir / "subprocess_stderr.txt"
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(f"Codex subprocess failed with exit code {completed.returncode}: {(completed.stderr or completed.stdout)[-500:]}")
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
        else:
            response = {
                "schema_version": "v5.codex_worker_output/0.1",
                "executor": "subprocess_codex_worker",
                "result_ref": record.get("task_id", ""),
                "artifacts": [
                    {"path": _rel(stdout_path, project_dir), "artifact_type": "codex_worker_stdout"},
                    {"path": _rel(stderr_path, project_dir), "artifact_type": "codex_worker_stderr"},
                    {"path": _rel(request_path, project_dir), "artifact_type": "codex_worker_request"},
                ],
                "patch_refs": [],
                "test_refs": [],
                "limitations": ["Subprocess completed without a structured response file; stdout/stderr were preserved for review."],
                "created_at": now_iso(),
            }
        response.setdefault("schema_version", "v5.codex_worker_output/0.1")
        response.setdefault("executor", "subprocess_codex_worker")
        response.setdefault("result_ref", record.get("task_id", ""))
        response.setdefault("artifacts", [])
        response["artifacts"].extend(
            [
                {"path": _rel(stdout_path, project_dir), "artifact_type": "codex_worker_stdout"},
                {"path": _rel(stderr_path, project_dir), "artifact_type": "codex_worker_stderr"},
                {"path": _rel(request_path, project_dir), "artifact_type": "codex_worker_request"},
            ]
        )
        response.setdefault("created_at", now_iso())
        return response

    return executor


def build_remote_codex_worker_executor(endpoint: str, *, token: str = "", timeout_seconds: int = 300) -> CodexExecutor:
    if not endpoint:
        raise ValueError("remote Codex worker endpoint is required")

    def executor(project_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(
            {
                "schema_version": "v5.remote_codex_worker_request/0.1",
                "project_id": project_dir.name,
                "task_id": record.get("task_id", ""),
                "worker_id": record.get("worker_id", ""),
                "packet": record.get("packet", {}),
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(endpoint, data=payload, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        data.setdefault("schema_version", "v5.codex_worker_output/0.1")
        data.setdefault("executor", "remote_codex_worker")
        data.setdefault("artifacts", [])
        data.setdefault("created_at", now_iso())
        return data

    return executor


def _load_claimed_record(project_dir: Path, task_id: str) -> dict[str, Any]:
    for record in load_worker_queue(project_dir).get("claimed", []):
        if record.get("task_id") == task_id:
            return record
    raise ValueError(f"task {task_id} is not claimed")


def _require_worker(record: dict[str, Any], worker_id: str) -> None:
    if record.get("worker_id") != worker_id:
        raise ValueError(f"worker_id mismatch for task {record.get('task_id')}")


def _validate_engineering_packet_for_execution(packet: dict[str, Any]) -> None:
    if packet.get("packet_type") != "EngineeringTaskPacket":
        raise ValueError("Codex worker execution currently supports EngineeringTaskPacket only")
    if not packet.get("allowed_paths"):
        raise ValueError("EngineeringTaskPacket requires allowed_paths")
    forbidden_paths = packet.get("forbidden_paths") or []
    missing_forbidden = [path for path in REQUIRED_ENGINEERING_FORBIDDEN_PATHS if path not in forbidden_paths]
    if missing_forbidden:
        raise ValueError(f"EngineeringTaskPacket forbidden_paths missing required entries: {', '.join(missing_forbidden)}")
    for path in packet.get("allowed_paths", []):
        _reject_forbidden_path(path, forbidden_paths)
    for command in packet.get("test_commands", []):
        if any(token in command for token in ["&", "|", ";", ">", "<", "\n", "\r"]):
            raise ValueError(f"test command is not allowed: {command}")


def _validate_output_manifest(output_manifest: dict[str, Any]) -> None:
    if not output_manifest:
        raise ValueError("executor output_manifest is required")
    if "artifacts" not in output_manifest or not isinstance(output_manifest["artifacts"], list):
        raise ValueError("executor output_manifest.artifacts is required")


def _register_output_artifacts(project_dir: Path, task_id: str, output_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = []
    for artifact in output_manifest.get("artifacts", []):
        relative_path = artifact.get("path") if isinstance(artifact, dict) else str(artifact)
        if not relative_path:
            continue
        artifacts.append(
            register_artifact(
                project_dir,
                relative_path,
                producer=task_id,
                artifact_type=(artifact.get("artifact_type") if isinstance(artifact, dict) else "") or "codex_engineering_artifact",
                expected_by_task_ids=[task_id],
                supports_subquestion_ids=output_manifest.get("supports_subquestion_ids", []),
                producer_run_id=output_manifest.get("result_ref", ""),
                qc_status="pass",
                limitations=output_manifest.get("limitations", []),
            )
        )
    return artifacts


def _reject_forbidden_path(path: str, forbidden_patterns: list[str]) -> None:
    normalized = path.replace("\\", "/").lstrip("/")
    for pattern in forbidden_patterns:
        normalized_pattern = pattern.replace("\\", "/").lstrip("/")
        if normalized == normalized_pattern.rstrip("/") or fnmatch.fnmatch(normalized, normalized_pattern):
            raise ValueError(f"allowed_paths contains forbidden path: {path}")


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
