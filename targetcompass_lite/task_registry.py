import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .qc import load_task_qc_index
from .schema_validation import load_schema, validate_object
from .v4 import load_codex_task_packet, load_v4_work_orders, read_work_order_attempts, v4_dir


TASK_REGISTRY_SCHEMA_VERSION = "v0.1.task_registry"


def build_task_registry(project_dir: Path) -> dict[str, Any]:
    plan = _read_json(project_dir / "analysis_plan.json", {})
    packets = plan.get("codex_task_packets", []) if isinstance(plan, dict) else []
    orders = load_v4_work_orders(project_dir)
    attempts = read_work_order_attempts(project_dir).get("attempts", [])
    latest_attempts = _latest_attempts(attempts)
    qc_by_work_order = {row.get("work_order_id", ""): row for row in load_task_qc_index(project_dir).get("reports", [])}
    queue_by_task = _queue_by_task(project_dir)
    engineering_by_job = _engineering_by_job(project_dir)
    packet_by_module = {row.get("name", ""): row for row in packets}
    tasks = []
    for order in orders:
        module_id = order.get("module_id", "")
        packet = packet_by_module.get(module_id, {}) or load_codex_task_packet(project_dir, order) or {}
        latest = latest_attempts.get(order.get("work_order_id", ""), {})
        qc = qc_by_work_order.get(order.get("work_order_id", ""), {})
        queue_task = queue_by_task.get(packet.get("task_id", "")) or queue_by_task.get(module_id, {})
        engineering = engineering_by_job.get((queue_task or {}).get("codex_job_id") or packet.get("codex_job_id", ""), {})
        tasks.append(_registry_task(order, packet, latest, qc, queue_task, engineering))
    packet_module_ids = {task.get("module_id", "") for task in tasks}
    for packet in packets:
        if packet.get("name", "") in packet_module_ids:
            continue
        queue_task = queue_by_task.get(packet.get("task_id", "")) or queue_by_task.get(packet.get("name", ""), {})
        engineering = engineering_by_job.get((queue_task or {}).get("codex_job_id") or packet.get("codex_job_id", ""), {})
        tasks.append(_packet_only_task(packet, queue_task, engineering))
    payload = {
        "schema_version": TASK_REGISTRY_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "task_count": len(tasks),
        "status_summary": _status_summary(tasks),
        "tasks": tasks,
    }
    _validate(payload)
    out = task_registry_path(project_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def task_registry_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "task_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _registry_task(
    order: dict[str, Any],
    packet: dict[str, Any],
    latest: dict[str, Any],
    qc: dict[str, Any],
    queue_task: dict[str, Any],
    engineering: dict[str, Any],
) -> dict[str, Any]:
    status = _task_status(order, latest, qc, queue_task, engineering)
    qc_gate = _qc_gate_summary(qc)
    return {
        "task_id": packet.get("task_id") or order.get("work_order_id", ""),
        "task_kind": queue_task.get("task_kind") or ("codex_task_packet" if packet else "work_order"),
        "work_order_id": order.get("work_order_id", ""),
        "module_id": order.get("module_id", ""),
        "module": order.get("module", ""),
        "dataset_id": order.get("dataset_id", ""),
        "method_contract_id": packet.get("method_contract_id") or order.get("parameters", {}).get("method_contract_id", ""),
        "status": status,
        "state": {
            "work_order_status": order.get("status", ""),
            "attempt_status": latest.get("status", ""),
            "qc_status": qc.get("overall_status", ""),
            "review_status": order.get("review_status", ""),
            "queue_status": queue_task.get("status", ""),
            "engineering_status": engineering.get("result_status", ""),
        },
        "refs": {
            "work_order": f"v4/work_orders/{order.get('work_order_id', '')}.json" if order.get("work_order_id") else "",
            "codex_task_packet": order.get("codex_task_packet", ""),
            "qc_report": qc.get("path", ""),
            "attempt_id": latest.get("attempt_id", ""),
            "queue_result": (queue_task.get("refs", {}) or {}).get("result", ""),
            "queue_test": (queue_task.get("refs", {}) or {}).get("test", ""),
            "queue_patch": (queue_task.get("refs", {}) or {}).get("patch", ""),
            "engineering_result": engineering.get("result_ref", ""),
        },
        "qc_gate": qc_gate,
        "queue": {
            "codex_job_id": queue_task.get("codex_job_id", ""),
            "claim": queue_task.get("claim", {}),
            "failure_reason": queue_task.get("failure_reason", ""),
        },
        "engineering": engineering,
        "inputs": order.get("inputs", {}),
        "expected_artifacts": order.get("expected_artifacts", []),
        "forbidden_actions": packet.get("forbidden_actions", []),
    }


def _packet_only_task(packet: dict[str, Any], queue_task: dict[str, Any], engineering: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": packet.get("task_id", ""),
        "task_kind": queue_task.get("task_kind") or "codex_task_packet",
        "work_order_id": "",
        "module_id": packet.get("name", ""),
        "module": packet.get("method", {}).get("name", ""),
        "dataset_id": packet.get("dataset", {}).get("dataset_id", ""),
        "method_contract_id": packet.get("method_contract_id", ""),
        "status": queue_task.get("status") or "packet_compiled",
        "state": {"work_order_status": "", "attempt_status": "", "qc_status": "", "review_status": "", "queue_status": queue_task.get("status", ""), "engineering_status": engineering.get("result_status", "")},
        "refs": {
            "work_order": "",
            "codex_task_packet": "analysis_plan.json#codex_task_packets",
            "qc_report": "",
            "attempt_id": "",
            "queue_result": (queue_task.get("refs", {}) or {}).get("result", ""),
            "queue_test": (queue_task.get("refs", {}) or {}).get("test", ""),
            "queue_patch": (queue_task.get("refs", {}) or {}).get("patch", ""),
            "engineering_result": engineering.get("result_ref", ""),
        },
        "qc_gate": {"status": "not_applicable", "reason": "packet has no executed WorkOrder QC report yet"},
        "queue": {"codex_job_id": queue_task.get("codex_job_id", ""), "claim": queue_task.get("claim", {}), "failure_reason": queue_task.get("failure_reason", "")},
        "engineering": engineering,
        "inputs": packet.get("inputs", {}),
        "expected_artifacts": packet.get("expected_outputs", []),
        "forbidden_actions": packet.get("forbidden_actions", []),
    }


def _task_status(order: dict[str, Any], latest: dict[str, Any], qc: dict[str, Any], queue_task: dict[str, Any], engineering: dict[str, Any]) -> str:
    if engineering.get("merge_status") == "merged":
        return "engineering_merged"
    if engineering.get("result_status") == "success" and engineering.get("merge_status") == "approved_for_merge":
        return "ready_to_merge"
    if engineering.get("result_status") == "success" and engineering.get("merge_status") != "approved_for_merge":
        return "engineering_review_required"
    if queue_task.get("status") in {"running", "claimed", "failed", "needs_review"}:
        return "queue_" + queue_task.get("status")
    if qc.get("overall_status") == "fail":
        return "qc_failed"
    if qc.get("overall_status") == "review":
        return "qc_review_required"
    if latest.get("status") == "running":
        return "running"
    if latest.get("status") == "failed":
        return "failed"
    if latest.get("status") == "success":
        if qc.get("overall_status") == "fail":
            return "qc_failed"
        if qc.get("overall_status") == "review":
            return "qc_review_required"
        if qc.get("overall_status") == "pass":
            return "qc_passed"
        return "executed"
    if order.get("requires_codex"):
        return "codex_required"
    return order.get("status", "compiled") or "compiled"


def _qc_gate_summary(qc: dict[str, Any]) -> dict[str, Any]:
    status = qc.get("overall_status", "")
    if not qc:
        return {
            "status": "review",
            "decision": "Evidence import requires review.",
            "reason": "No TaskQCReport is linked to this WorkOrder.",
            "evidence_import": "QC_REVIEW_REQUIRED",
        }
    if status == "pass":
        return {
            "status": "pass",
            "decision": "Evidence can enter Evidence DB.",
            "reason": f"{qc.get('path', '')} overall_status=pass",
            "evidence_import": "ALLOW",
        }
    if status == "fail":
        return {
            "status": "fail",
            "decision": "Evidence is blocked from Evidence DB.",
            "reason": f"{qc.get('path', '')} overall_status=fail",
            "evidence_import": "REJECT",
        }
    return {
        "status": "review",
        "decision": "Evidence can be imported only as QC_REVIEW_REQUIRED.",
        "reason": f"{qc.get('path', '')} overall_status={status or 'unknown'}",
        "evidence_import": "QC_REVIEW_REQUIRED",
    }


def _queue_by_task(project_dir: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(v4_dir(project_dir) / "codex_task_queue.json", {})
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("tasks", []) if isinstance(payload, dict) else []:
        if row.get("task_id"):
            out[row["task_id"]] = row
        if row.get("module_id"):
            out[row["module_id"]] = row
    return out


def _engineering_by_job(project_dir: Path) -> dict[str, dict[str, Any]]:
    root = v4_dir(project_dir) / "codex_engineering"
    results = _read_json(root / "result_registry.json", {"results": []}).get("results", [])
    patches = _read_json(root / "patch_registry.json", {"patches": []}).get("patches", [])
    tests = _read_json(root / "test_registry.json", {"tests": []}).get("tests", [])
    out: dict[str, dict[str, Any]] = {}
    for result in results:
        job_id = result.get("codex_job_id", "")
        if not job_id:
            continue
        out[job_id] = {
            "codex_job_id": job_id,
            "result_status": result.get("status", ""),
            "merge_status": result.get("merge_status", ""),
            "review_status": result.get("review_status", ""),
            "merge_ref": result.get("merge_ref", ""),
            "result_ref": f"v4/codex_engineering/result_registry.json#{result.get('result_id', '')}",
            "patch_count": len([row for row in patches if row.get("codex_job_id") == job_id]),
            "test_count": len([row for row in tests if row.get("codex_job_id") == job_id]),
            "failure_reason": result.get("failure_reason", ""),
        }
    return out


def _latest_attempts(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        key = attempt.get("work_order_id", "")
        if not key:
            continue
        if key not in latest or attempt.get("started_at", "") >= latest[key].get("started_at", ""):
            latest[key] = attempt
    return latest


def _status_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for task in tasks:
        status = task.get("status", "")
        out[status] = out.get(status, 0) + 1
    return out


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _validate(payload: dict[str, Any]) -> None:
    errors = validate_object(payload, load_schema("task_registry.schema.json"), "TaskRegistry")
    if errors:
        raise ValueError("; ".join(errors))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
