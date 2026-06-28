import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .orchestrator import submit_orchestrator_run
from .schema_validation import load_schema, validate_object
from .v4 import content_hash, load_codex_task_packet, load_v4_work_orders, v4_dir


QUEUE_SCHEMA_VERSION = "v0.1.codex_task_queue"
DEFAULT_STALE_AFTER_SECONDS = 4 * 60 * 60


def sync_codex_task_queue(project_dir: Path) -> dict[str, Any]:
    packets = _load_plan_packets(project_dir)
    queue = _read_queue(project_dir)
    existing = {row.get("task_id", ""): row for row in queue.get("tasks", [])}
    orders_by_module = {row.get("module_id", ""): row for row in load_v4_work_orders(project_dir)}
    tasks = []
    for packet in packets:
        task_id = packet.get("task_id", "")
        old = existing.get(task_id, {})
        module_id = packet.get("name", "")
        order = orders_by_module.get(module_id, {})
        task = {
            "schema_version": "v0.1.codex_queue_task",
            "task_id": task_id,
            "codex_job_id": old.get("codex_job_id") or packet.get("codex_job_id") or "cqj_" + content_hash({"project": project_dir.name, "task_id": task_id, "module_id": module_id})[:16],
            "project_id": project_dir.name,
            "module_id": module_id,
            "work_order_id": order.get("work_order_id", old.get("work_order_id", "")),
            "method_contract_id": packet.get("method_contract_id", ""),
            "task_kind": "analysis_execution" if order and not order.get("requires_codex") else "engineering",
            "status": old.get("status", "pending"),
            "claim": old.get("claim", {}),
            "packet": packet,
            "refs": {
                "packet_source": "results/evidence_planning/codex_task_packets.json",
                "work_order": f"v4/work_orders/{order.get('work_order_id', '')}.json" if order else "",
                "result": old.get("refs", {}).get("result", ""),
                "patch": old.get("refs", {}).get("patch", ""),
                "test": old.get("refs", {}).get("test", ""),
            },
            "failure_reason": old.get("failure_reason", ""),
            "recovery": old.get("recovery", {}),
            "started_at": old.get("started_at", ""),
            "finished_at": old.get("finished_at", ""),
            "released_at": old.get("released_at", ""),
            "updated_at": old.get("updated_at", _now()),
        }
        tasks.append(task)
    queue = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "task_count": len(tasks),
        "status_summary": _status_summary(tasks),
        "tasks": tasks,
        "updated_at": _now(),
    }
    _validate_queue(queue)
    _write_json(queue_path(project_dir), queue)
    return queue


def claim_codex_task(project_dir: Path, worker_id: str = "local_codex_worker", task_id: str = "") -> dict[str, Any]:
    sync_codex_task_queue(project_dir)
    release_stale_codex_tasks(project_dir)
    queue = _read_queue(project_dir)
    selected = None
    for task in queue["tasks"]:
        if task_id and task.get("task_id") != task_id and task.get("module_id") != task_id:
            continue
        if task.get("status") in {"pending", "failed", "released"}:
            selected = task
            break
    if selected is None:
        return {"schema_version": "v0.1.codex_queue_claim", "project_id": project_dir.name, "claimed": False, "reason": "no claimable task"}
    claim_id = "claim_" + content_hash({"task": selected["task_id"], "worker": worker_id, "time": _now()})[:16]
    selected["status"] = "claimed"
    selected["claim"] = {"claim_id": claim_id, "worker_id": worker_id, "claimed_at": _now()}
    selected["updated_at"] = _now()
    _save_tasks(project_dir, queue["tasks"])
    return {"schema_version": "v0.1.codex_queue_claim", "project_id": project_dir.name, "claimed": True, "claim_id": claim_id, "task": selected}


def execute_codex_queue_task(project_dir: Path, task_id: str = "", worker_id: str = "local_codex_worker", force: bool = False) -> dict[str, Any]:
    claimed_task = _claimed_task_for_worker(project_dir, worker_id, task_id)
    if claimed_task:
        task = claimed_task
    else:
        claim = claim_codex_task(project_dir, worker_id=worker_id, task_id=task_id)
        if not claim.get("claimed"):
            return claim
        task = claim["task"]
    task["status"] = "running"
    task["started_at"] = _now()
    _update_task(project_dir, task)
    try:
        if task.get("task_kind") == "analysis_execution":
            execution = submit_orchestrator_run(
                project_dir,
                run_type="work_order_dag",
                idempotency_key="codex_queue_" + content_hash({"task": task["task_id"], "force": force, "time": _now()})[:24],
                module_id=task.get("module_id", ""),
                force=force,
                actor=worker_id,
            )
            status = "success" if execution.get("status") == "success" else "failed"
            artifacts = _execution_artifacts(execution)
            failure = execution.get("failure_reason", "") or _node_failure(execution)
            patch = _record_queue_patch(project_dir, task, "not_applicable", [], "Analysis packet executed through registered module; no source patch was produced.")
            test = _record_queue_test(project_dir, task, "four_layer_qc", "passed" if status == "success" else "failed", artifacts, failure)
            result = _record_queue_result(project_dir, task, status, artifacts, failure, execution, patch, test)
        else:
            engineering = _execute_engineering_task(project_dir, task, worker_id)
            patch = _record_queue_patch(project_dir, task, engineering["patch_status"], engineering["patch_refs"], engineering["patch_summary"])
            test = _record_queue_test(project_dir, task, "codex_engineering_loop", engineering["test_status"], engineering["artifacts"], engineering["failure_reason"])
            result = _record_queue_result(project_dir, task, engineering["status"], engineering["artifacts"], engineering["failure_reason"], engineering["execution"], patch, test)
            status = engineering["status"]
        task["status"] = "succeeded" if status == "success" else status
        task["finished_at"] = _now()
        task["refs"]["patch"] = patch.get("patch_ref", "")
        task["refs"]["test"] = test.get("test_ref", "")
        task["refs"]["result"] = result.get("result_ref", "")
        task["failure_reason"] = result.get("failure_reason", "")
        task["updated_at"] = _now()
        _update_task(project_dir, task)
        _refresh_task_registry(project_dir)
        return {"schema_version": "v0.1.codex_queue_execution", "project_id": project_dir.name, "task": task, "patch": patch, "test": test, "result": result}
    except Exception as exc:
        task["status"] = "failed"
        task["failure_reason"] = str(exc)
        task["finished_at"] = _now()
        _update_task(project_dir, task)
        _refresh_task_registry(project_dir)
        return {"schema_version": "v0.1.codex_queue_execution", "project_id": project_dir.name, "task": task, "status": "failed", "failure_reason": str(exc)}


def execute_codex_queue(project_dir: Path, worker_id: str = "local_codex_worker", limit: int = 0, force: bool = False) -> dict[str, Any]:
    sync_codex_task_queue(project_dir)
    results = []
    count = 0
    while True:
        if limit and count >= limit:
            break
        result = execute_codex_queue_task(project_dir, worker_id=worker_id, force=force)
        if not result.get("task"):
            break
        results.append(result)
        count += 1
    queue = sync_codex_task_queue(project_dir)
    return {
        "schema_version": "v0.1.codex_queue_batch_execution",
        "project_id": project_dir.name,
        "executed_count": len(results),
        "status_summary": queue.get("status_summary", {}),
        "results": results,
    }


def release_stale_codex_tasks(project_dir: Path, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> dict[str, Any]:
    queue = _read_queue(project_dir)
    tasks = queue.get("tasks", [])
    released = []
    now = datetime.now(timezone.utc)
    for task in tasks:
        if task.get("status") not in {"claimed", "running"}:
            continue
        timestamp = task.get("started_at") or (task.get("claim", {}) or {}).get("claimed_at") or task.get("updated_at", "")
        age = _age_seconds(timestamp, now)
        if age is None or age < stale_after_seconds:
            continue
        previous_status = task.get("status", "")
        task["status"] = "released"
        task["failure_reason"] = f"released stale {previous_status} task after {int(age)} seconds"
        task["released_at"] = _now()
        task.setdefault("recovery", {})
        task["recovery"].update(
            {
                "previous_status": previous_status,
                "released_at": task["released_at"],
                "reason": task["failure_reason"],
                "resume_action": "claim_codex_task or execute_codex_queue_task",
            }
        )
        released.append({"task_id": task.get("task_id", ""), "previous_status": previous_status, "age_seconds": int(age)})
    if released:
        _save_tasks(project_dir, tasks)
        _refresh_task_registry(project_dir)
    return {
        "schema_version": "v0.1.codex_queue_stale_recovery",
        "project_id": project_dir.name,
        "released_count": len(released),
        "released": released,
    }


def queue_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "codex_task_queue.json"


def queue_patch_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "codex_task_queue_patches.json"


def queue_test_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "codex_task_queue_tests.json"


def queue_result_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "codex_task_queue_results.json"


def _load_plan_packets(project_dir: Path) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for path in [
        project_dir / "results" / "evidence_planning" / "codex_task_packets.json",
        project_dir / "analysis_plan.json",
    ]:
        payload = _read_json(path, {})
        packets.extend(row for row in (payload.get("packets") or payload.get("codex_task_packets") or []) if isinstance(row, dict) and row.get("task_id"))
    for order in load_v4_work_orders(project_dir):
        rel = order.get("codex_task_packet", "")
        if not rel:
            continue
        packet = load_codex_task_packet(project_dir, order)
        if packet and packet.get("task_id"):
            packets.append(packet)
    deduped: dict[str, dict[str, Any]] = {}
    for packet in packets:
        deduped[packet["task_id"]] = packet
    return list(deduped.values())


def _record_queue_patch(project_dir: Path, task: dict[str, Any], status: str, patch_refs: list[str], summary: str) -> dict[str, Any]:
    patch = {
        "schema_version": "v0.1.codex_queue_patch_record",
        "patch_record_id": "cqpatch_" + content_hash({"task": task["task_id"], "status": status, "time": _now()})[:16],
        "task_id": task["task_id"],
        "codex_job_id": task["codex_job_id"],
        "work_order_id": task.get("work_order_id", ""),
        "status": status,
        "patch_refs": patch_refs,
        "summary": summary,
        "recorded_at": _now(),
    }
    _append_registry(queue_patch_registry_path(project_dir), "v0.1.codex_queue_patch_registry", "patches", patch, "patch_record_id")
    patch["patch_ref"] = f"v4/codex_task_queue_patches.json#{patch['patch_record_id']}"
    return patch


def _record_queue_test(project_dir: Path, task: dict[str, Any], command: str, status: str, artifacts: list[str], failure_reason: str) -> dict[str, Any]:
    test = {
        "schema_version": "v0.1.codex_queue_test_record",
        "test_record_id": "cqtest_" + content_hash({"task": task["task_id"], "command": command, "time": _now()})[:16],
        "task_id": task["task_id"],
        "codex_job_id": task["codex_job_id"],
        "work_order_id": task.get("work_order_id", ""),
        "command": command,
        "status": status,
        "artifacts": artifacts,
        "failure_reason": failure_reason,
        "recorded_at": _now(),
    }
    _append_registry(queue_test_registry_path(project_dir), "v0.1.codex_queue_test_registry", "tests", test, "test_record_id")
    test["test_ref"] = f"v4/codex_task_queue_tests.json#{test['test_record_id']}"
    return test


def _record_queue_result(project_dir: Path, task: dict[str, Any], status: str, artifacts: list[str], failure_reason: str, execution: dict[str, Any], patch: dict[str, Any], test: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema_version": "v0.1.codex_queue_result_record",
        "result_record_id": "cqresult_" + content_hash({"task": task["task_id"], "status": status, "time": _now()})[:16],
        "task_id": task["task_id"],
        "codex_job_id": task["codex_job_id"],
        "work_order_id": task.get("work_order_id", ""),
        "module_id": task.get("module_id", ""),
        "status": status,
        "artifacts": artifacts,
        "failure_reason": failure_reason,
        "patch_ref": patch.get("patch_ref", ""),
        "test_ref": test.get("test_ref", ""),
        "orchestrator_run_id": execution.get("orchestrator_run_id", ""),
        "recorded_at": _now(),
    }
    _append_registry(queue_result_registry_path(project_dir), "v0.1.codex_queue_result_registry", "results", result, "result_record_id")
    result["result_ref"] = f"v4/codex_task_queue_results.json#{result['result_record_id']}"
    return result


def _execution_artifacts(execution: dict[str, Any]) -> list[str]:
    artifacts = []
    for node in execution.get("result", {}).get("node_results", []):
        artifacts.extend(str(item) for item in node.get("artifacts", []) if item)
        qc = node.get("task_qc_report", {})
        if qc.get("qc_report_id"):
            artifacts.append(f"results/qc/{qc['qc_report_id']}.json")
    return sorted(set(artifacts))


def _node_failure(execution: dict[str, Any]) -> str:
    for node in execution.get("result", {}).get("node_results", []):
        if node.get("reason"):
            return node["reason"]
    return ""


def _refresh_task_registry(project_dir: Path) -> None:
    try:
        from .task_registry import build_task_registry

        build_task_registry(project_dir)
    except Exception:
        pass


def _execute_engineering_task(project_dir: Path, task: dict[str, Any], worker_id: str) -> dict[str, Any]:
    try:
        from .codex_engineering import create_isolated_workspace, record_codex_result, run_codex_task_tests

        work_order_id = task.get("work_order_id", "")
        if not work_order_id:
            raise ValueError("engineering queue task has no work_order_id")
        workspace = create_isolated_workspace(project_dir, work_order_id, actor=worker_id)
        codex_job_id = task.get("codex_job_id", "")
        try:
            engineering_result = run_codex_task_tests(project_dir, codex_job_id, actor=worker_id, allow_unapproved_dispatch=True)
        except Exception as exc:
            engineering_result = record_codex_result(
                project_dir,
                codex_job_id,
                "needs_review",
                artifacts=[workspace.get("workspace_path", "")],
                failure_reason=str(exc),
                actor=worker_id,
            )
        status = "success" if engineering_result.get("status") == "success" else "needs_review"
        artifacts = [item for item in engineering_result.get("artifacts", []) if item]
        if workspace.get("workspace_path"):
            artifacts.append(workspace["workspace_path"])
        test_results = engineering_result.get("test_results", [])
        test_status = "passed" if test_results and all(row.get("status") == "passed" for row in test_results) else "needs_review"
        return {
            "status": status,
            "patch_status": "required",
            "patch_refs": engineering_result.get("patch_refs", []),
            "patch_summary": "Engineering packet entered Codex engineering loop; source patch must be reviewed before merge.",
            "test_status": test_status,
            "artifacts": sorted(set(artifacts)),
            "failure_reason": engineering_result.get("failure_reason", ""),
            "execution": {"orchestrator_run_id": "", "codex_engineering_result_id": engineering_result.get("result_id", "")},
        }
    except Exception as exc:
        return {
            "status": "needs_review",
            "patch_status": "required",
            "patch_refs": [],
            "patch_summary": "Engineering packet could not complete Codex engineering loop.",
            "test_status": "failed",
            "artifacts": [],
            "failure_reason": str(exc),
            "execution": {},
        }


def _read_queue(project_dir: Path) -> dict[str, Any]:
    return _read_json(queue_path(project_dir), {"schema_version": QUEUE_SCHEMA_VERSION, "project_id": project_dir.name, "tasks": []})


def _claimed_task_for_worker(project_dir: Path, worker_id: str, task_id: str = "") -> dict[str, Any]:
    queue = _read_queue(project_dir)
    for task in queue.get("tasks", []):
        if task_id and task.get("task_id") != task_id and task.get("module_id") != task_id:
            continue
        claim = task.get("claim", {}) or {}
        if task.get("status") == "claimed" and claim.get("worker_id") == worker_id:
            return task
    return {}


def _save_tasks(project_dir: Path, tasks: list[dict[str, Any]]) -> None:
    queue = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "task_count": len(tasks),
        "status_summary": _status_summary(tasks),
        "tasks": tasks,
        "updated_at": _now(),
    }
    _validate_queue(queue)
    _write_json(queue_path(project_dir), queue)


def _update_task(project_dir: Path, task: dict[str, Any]) -> None:
    queue = _read_queue(project_dir)
    tasks = queue.get("tasks", [])
    for idx, row in enumerate(tasks):
        if row.get("task_id") == task.get("task_id"):
            tasks[idx] = task
            break
    else:
        tasks.append(task)
    _save_tasks(project_dir, tasks)


def _append_registry(path: Path, schema_version: str, key: str, item: dict[str, Any], id_key: str) -> None:
    registry = _read_json(path, {"schema_version": schema_version, key: []})
    rows = registry.setdefault(key, [])
    rows[:] = [row for row in rows if row.get(id_key) != item.get(id_key)]
    rows.append(item)
    registry["updated_at"] = _now()
    _write_json(path, registry)


def _status_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for task in tasks:
        status = task.get("status", "")
        out[status] = out.get(status, 0) + 1
    return out


def _validate_queue(queue: dict[str, Any]) -> None:
    errors = validate_object(queue, load_schema("codex_task_queue.schema.json"), "CodexTaskQueue")
    if errors:
        raise ValueError("; ".join(errors))


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=False)
    last_error: OSError | None = None
    for attempt in range(5):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.05 * (attempt + 1))
    if last_error:
        raise last_error


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(timestamp: str, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now - value).total_seconds()
