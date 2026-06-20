import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, file_hash, load_codex_task_packet, load_v4_work_orders, save_codex_task_packet, save_v4_work_order, v4_dir


def engineering_dir(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "codex_engineering"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_root(project_dir: Path) -> Path:
    path = engineering_dir(project_dir) / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def patch_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "patch_registry.json"


def test_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "test_registry.json"


def result_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "result_registry.json"


def workspace_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "workspace_registry.json"


def create_isolated_workspace(project_dir: Path, work_order_id: str, actor: str = "codex") -> dict[str, Any]:
    order = _find_work_order(project_dir, work_order_id)
    packet = load_codex_task_packet(project_dir, order)
    if not packet:
        raise ValueError(f"work order has no Codex task packet: {work_order_id}")
    job_id = packet["codex_job_id"]
    path = workspace_root(project_dir) / job_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "task_packet.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    copied = _copy_allowed_inputs(project_dir, path, packet.get("allowed_paths", []))
    manifest = {
        "schema_version": "v4.codex_workspace/0.1",
        "workspace_id": "cws_" + content_hash({"job": job_id, "work_order": work_order_id})[:16],
        "codex_job_id": job_id,
        "work_order_id": work_order_id,
        "project_id": project_dir.name,
        "workspace_path": str(path.relative_to(project_dir)),
        "baseline_commit": packet.get("baseline_commit", ""),
        "allowed_paths": packet.get("allowed_paths", []),
        "copied_inputs": copied,
        "status": "prepared",
        "created_by": actor,
        "created_at": _now(),
    }
    (path / "workspace_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    registry = _read_registry(workspace_registry_path(project_dir), "v4.codex_workspace_registry/0.1", "workspaces")
    _upsert(registry["workspaces"], manifest, "codex_job_id")
    _write_registry(workspace_registry_path(project_dir), registry)
    packet["workspace_ref"] = str((path / "workspace_manifest.json").relative_to(project_dir))
    packet["execution_status"] = "workspace_prepared"
    save_codex_task_packet(project_dir, order, packet)
    order["engineering_status"] = "workspace_prepared"
    order["codex_workspace_ref"] = packet["workspace_ref"]
    save_v4_work_order(project_dir, order)
    return manifest


def register_codex_patch(project_dir: Path, codex_job_id: str, patch_path: str, summary: str = "", actor: str = "codex") -> dict[str, Any]:
    path = project_dir / patch_path
    if not path.exists():
        raise ValueError(f"patch file not found: {patch_path}")
    result = _find_order_and_packet(project_dir, codex_job_id)
    patch = {
        "schema_version": "v4.codex_patch/0.1",
        "patch_id": "cpatch_" + content_hash({"job": codex_job_id, "path": patch_path, "hash": file_hash(path)})[:16],
        "codex_job_id": codex_job_id,
        "work_order_id": result["order"]["work_order_id"],
        "patch_path": patch_path,
        "patch_hash": file_hash(path),
        "summary": summary.strip(),
        "created_by": actor,
        "created_at": _now(),
    }
    registry = _read_registry(patch_registry_path(project_dir), "v4.codex_patch_registry/0.1", "patches")
    _upsert(registry["patches"], patch, "patch_id")
    _write_registry(patch_registry_path(project_dir), registry)
    return patch


def register_codex_test_result(
    project_dir: Path,
    codex_job_id: str,
    command: str,
    status: str,
    stdout_ref: str = "",
    stderr_ref: str = "",
    duration_seconds: float | None = None,
    actor: str = "codex",
) -> dict[str, Any]:
    if status not in {"passed", "failed", "skipped"}:
        raise ValueError(f"unsupported test status: {status}")
    result = _find_order_and_packet(project_dir, codex_job_id)
    test = {
        "schema_version": "v4.codex_test_result/0.1",
        "test_id": "ctest_" + content_hash({"job": codex_job_id, "command": command, "time": _now()})[:16],
        "codex_job_id": codex_job_id,
        "work_order_id": result["order"]["work_order_id"],
        "command": command,
        "status": status,
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
        "duration_seconds": duration_seconds,
        "recorded_by": actor,
        "recorded_at": _now(),
    }
    registry = _read_registry(test_registry_path(project_dir), "v4.codex_test_registry/0.1", "tests")
    registry["tests"].append(test)
    _write_registry(test_registry_path(project_dir), registry)
    return test


def record_codex_result(
    project_dir: Path,
    codex_job_id: str,
    status: str,
    artifacts: list[str] | None = None,
    failure_reason: str = "",
    actor: str = "codex",
) -> dict[str, Any]:
    if status not in {"success", "failed", "cancelled", "needs_review"}:
        raise ValueError(f"unsupported Codex result status: {status}")
    found = _find_order_and_packet(project_dir, codex_job_id)
    patches = [row for row in load_codex_engineering(project_dir).get("patches", []) if row.get("codex_job_id") == codex_job_id]
    tests = [row for row in load_codex_engineering(project_dir).get("tests", []) if row.get("codex_job_id") == codex_job_id]
    merge_status = "pending_human_approval" if status == "success" else "blocked"
    result = {
        "schema_version": "v4.codex_execution_result/0.1",
        "result_id": "cresult_" + content_hash({"job": codex_job_id, "status": status, "time": _now()})[:16],
        "codex_job_id": codex_job_id,
        "work_order_id": found["order"]["work_order_id"],
        "status": status,
        "merge_status": merge_status,
        "patch_refs": [row["patch_id"] for row in patches],
        "test_refs": [row["test_id"] for row in tests],
        "artifacts": artifacts or [],
        "failure_reason": failure_reason,
        "review_status": "pending" if status == "success" else "needs_review",
        "evidence_ref": "v4/evidence_snapshot.json",
        "work_order_ref": found["order"].get("work_order_id", ""),
        "recorded_by": actor,
        "recorded_at": _now(),
    }
    registry = _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")
    _upsert(registry["results"], result, "result_id")
    _write_registry(result_registry_path(project_dir), registry)

    order = found["order"]
    packet = found["packet"]
    order["engineering_status"] = "result_recorded"
    order["codex_result_status"] = status
    order["codex_result_ref"] = f"v4/codex_engineering/result_registry.json#{result['result_id']}"
    order["status"] = "engineering_review_required" if status == "success" else "engineering_failed"
    packet["execution_status"] = status
    packet["result_ref"] = order["codex_result_ref"]
    packet["merge_status"] = merge_status
    save_codex_task_packet(project_dir, order, packet)
    save_v4_work_order(project_dir, order)
    return result


def mark_codex_result_reviewed(project_dir: Path, result_id: str, action: str, reason: str, reviewer: str = "human") -> dict[str, Any]:
    registry = _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")
    result = next((row for row in registry["results"] if row.get("result_id") == result_id), None)
    if not result:
        raise ValueError(f"Codex result not found: {result_id}")
    if action not in {"approve", "reject", "needs_review"}:
        raise ValueError(f"unsupported review action: {action}")
    result["review_status"] = action
    result["review_reason"] = reason
    result["reviewer"] = reviewer
    result["reviewed_at"] = _now()
    result["merge_status"] = "approved_for_merge" if action == "approve" else "merge_blocked"
    _write_registry(result_registry_path(project_dir), registry)
    found = _find_order_and_packet(project_dir, result["codex_job_id"])
    order = found["order"]
    packet = found["packet"]
    order["engineering_review_status"] = action
    order["engineering_review_reason"] = reason
    order["engineering_merge_status"] = result["merge_status"]
    packet["engineering_review_status"] = action
    packet["merge_status"] = result["merge_status"]
    save_codex_task_packet(project_dir, order, packet)
    save_v4_work_order(project_dir, order)
    return result


def load_codex_engineering(project_dir: Path) -> dict[str, Any]:
    return {
        "workspaces": _read_registry(workspace_registry_path(project_dir), "v4.codex_workspace_registry/0.1", "workspaces")["workspaces"],
        "patches": _read_registry(patch_registry_path(project_dir), "v4.codex_patch_registry/0.1", "patches")["patches"],
        "tests": _read_registry(test_registry_path(project_dir), "v4.codex_test_registry/0.1", "tests")["tests"],
        "results": _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")["results"],
    }


def _copy_allowed_inputs(project_dir: Path, workspace: Path, allowed_paths: list[str]) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    input_dir = workspace / "allowed_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    for raw in allowed_paths:
        rel = str(raw).replace("\\", "/")
        if "*" in rel:
            continue
        source = (project_dir / rel).resolve()
        if not source.exists() or not source.is_file():
            continue
        try:
            source.relative_to(project_dir.resolve())
        except ValueError:
            continue
        target = input_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"source": rel, "workspace_path": str(target.relative_to(workspace)), "hash": file_hash(target)})
    return copied


def _find_work_order(project_dir: Path, work_order_id: str) -> dict[str, Any]:
    for order in load_v4_work_orders(project_dir):
        if order.get("work_order_id") == work_order_id:
            return order
    raise ValueError(f"work order not found: {work_order_id}")


def _find_order_and_packet(project_dir: Path, codex_job_id: str) -> dict[str, Any]:
    for order in load_v4_work_orders(project_dir):
        packet = load_codex_task_packet(project_dir, order)
        if packet.get("codex_job_id") == codex_job_id:
            return {"order": order, "packet": packet}
    raise ValueError(f"Codex task not found: {codex_job_id}")


def _read_registry(path: Path, schema_version: str, key: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": schema_version, "updated_at": "", key: []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_registry(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _upsert(rows: list[dict[str, Any]], item: dict[str, Any], key: str) -> None:
    for idx, row in enumerate(rows):
        if row.get(key) == item.get(key):
            rows[idx] = item
            return
    rows.append(item)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
