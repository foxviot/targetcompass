import json
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable

from .agent_roles import AGENT_ROLES, write_agent_role_manifest
from .methods.registry import load_method_config
from .v4 import content_hash, v4_dir


ROLE_RUN_SCHEMA = "v4.role_run/0.1"
ROLE_RUN_INDEX_SCHEMA = "v4.role_runs/0.1"


def role_runs_dir(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "role_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def role_run_index_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "role_runs.json"


def load_role_runs(project_dir: Path) -> dict[str, Any]:
    path = role_run_index_path(project_dir)
    if not path.exists():
        return {"schema_version": ROLE_RUN_INDEX_SCHEMA, "project_id": project_dir.name, "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def run_role(
    project_dir: Path,
    role_id: str,
    input_refs: dict[str, Any],
    operation: Callable[[], Any],
    runner: str = "local_wrapped_function",
    method_id: str | None = None,
    model: str = "local",
    parameters: dict[str, Any] | None = None,
    manual_override: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    role = _role_by_id(role_id)
    parameters = parameters or {}
    manual_override = manual_override or {}
    method_config = load_method_config(project_dir)
    selected_method = method_id or method_config.get(role_id) or _legacy_stage_method(role_id, method_config)
    started_at = datetime.now(timezone.utc).isoformat()
    seed = {
        "project": project_dir.name,
        "role": role_id,
        "started_at": started_at,
        "input_refs": input_refs,
        "method_id": selected_method,
    }
    run_id = "role_run_" + content_hash(seed)[:16]
    out_dir = role_runs_dir(project_dir)
    packet_path = out_dir / f"{run_id}_input.json"
    result_path = out_dir / f"{run_id}_output.json"
    log_path = out_dir / f"{run_id}_log.txt"
    packet = {
        "schema_version": "v4.role_input_packet/0.1",
        "project_id": project_dir.name,
        "role_id": role_id,
        "run_id": run_id,
        "runner": runner,
        "method_id": selected_method,
        "model": model,
        "parameters": parameters,
        "manual_override": manual_override,
        "stage": role.get("stage", ""),
        "expected_schema": role.get("schema", ""),
        "input_refs": input_refs,
        "declared_input_refs": role.get("input_refs", []),
        "declared_output_refs": role.get("output_refs", []),
        "created_at": started_at,
    }
    packet_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    stdout = StringIO()
    stderr = StringIO()
    status = "success"
    failure_reason = ""
    output: Any = None
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            output = operation()
    except Exception as exc:
        status = "failed"
        failure_reason = str(exc)
        stderr.write(traceback.format_exc())
    finished_at = datetime.now(timezone.utc).isoformat()
    execution_dispatch = _extract_execution_dispatch(output)
    output_packet = {
        "schema_version": "v4.role_output_packet/0.1",
        "project_id": project_dir.name,
        "role_id": role_id,
        "run_id": run_id,
        "status": status,
        "failure_reason": failure_reason,
        "typed_output": _json_safe(_strip_execution_dispatch(output)),
        "execution_dispatch": execution_dispatch,
        "output_summary": _summarize_output(output),
        "schema": role.get("schema", ""),
        "method_id": selected_method,
        "model": model,
        "parameters_hash": content_hash(parameters),
        "manual_override_hash": content_hash(manual_override),
        "output_refs": _existing_outputs(project_dir, role.get("output_refs", [])),
        "finished_at": finished_at,
    }
    try:
        from .orchestration_graph import validate_role_output_packet

        temp_record = {"role_id": role_id, "output_packet": str(result_path.relative_to(project_dir))}
        result_path.write_text(json.dumps(output_packet, indent=2, ensure_ascii=False), encoding="utf-8")
        validation = validate_role_output_packet(project_dir, role_id, temp_record)
        output_packet["schema_validation"] = {
            "schema_name": validation.get("schema_name", ""),
            "valid": validation.get("valid", False),
            "errors": validation.get("errors", []),
        }
    except Exception as exc:
        output_packet["schema_validation"] = {"schema_name": role.get("schema", ""), "valid": False, "errors": [str(exc)]}
    result_path.write_text(json.dumps(output_packet, indent=2, ensure_ascii=False), encoding="utf-8")
    log_path.write_text(stdout.getvalue() + stderr.getvalue(), encoding="utf-8")
    record = {
        "schema_version": ROLE_RUN_SCHEMA,
        "role_run_id": run_id,
        "role_id": role_id,
        "stage": role.get("stage", ""),
        "runner": runner,
        "method_id": selected_method,
        "model": model,
        "parameters_hash": content_hash(parameters),
        "manual_override": manual_override,
        "status": status,
        "executor_backend": execution_dispatch.get("executor_backend", "unknown"),
        "started_at": started_at,
        "finished_at": finished_at,
        "failure_reason": failure_reason,
        "input_packet": str(packet_path.relative_to(project_dir)),
        "output_packet": str(result_path.relative_to(project_dir)),
        "log": str(log_path.relative_to(project_dir)),
        "decision_id": "decision_" + content_hash(output_packet)[:16],
        "resume_key": "role_resume_" + content_hash({"role": role_id, "input_refs": input_refs})[:16],
        "method_config_hash": content_hash(method_config),
        "schema_valid": output_packet.get("schema_validation", {}).get("valid", False),
        "schema_errors": output_packet.get("schema_validation", {}).get("errors", []),
        "execution_dispatch": execution_dispatch,
    }
    _append_role_run(project_dir, record)
    _refresh_role_manifest(project_dir)
    if status != "success":
        raise RuntimeError(f"{role_id} failed: {failure_reason}")
    return output, record


def _append_role_run(project_dir: Path, record: dict[str, Any]) -> None:
    index = load_role_runs(project_dir)
    index["runs"].append(record)
    index["updated_at"] = datetime.now(timezone.utc).isoformat()
    role_run_index_path(project_dir).write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _refresh_role_manifest(project_dir: Path) -> None:
    observations = {}
    for record in load_role_runs(project_dir).get("runs", []):
        observations[record["role_id"]] = {
            "latest_role_run_id": record["role_run_id"],
            "latest_status": record["status"],
            "latest_output_packet": record["output_packet"],
        }
    write_agent_role_manifest(project_dir, observations)


def _role_by_id(role_id: str) -> dict[str, Any]:
    for role in AGENT_ROLES:
        if role["role_id"] == role_id:
            return role
    raise ValueError(f"Unknown v4 role: {role_id}")


def _legacy_stage_method(role_id: str, method_config: dict[str, str]) -> str:
    if role_id == "method_reviewer":
        return method_config.get("audit", "")
    if role_id == "result_reviewer":
        return method_config.get("experiment", "")
    if role_id == "disease_normalizer":
        return method_config.get("query", "")
    return ""


def _existing_outputs(project_dir: Path, refs: list[str]) -> list[str]:
    existing = []
    for ref in refs:
        if "*" in ref:
            existing.extend(str(path.relative_to(project_dir)) for path in project_dir.glob(ref))
        elif (project_dir / ref).exists():
            existing.append(ref)
    return sorted(existing)


def _summarize_output(output: Any) -> Any:
    if isinstance(output, (str, int, float, bool)) or output is None:
        return output
    if isinstance(output, Path):
        return str(output)
    if isinstance(output, dict):
        return {key: _short_value(value) for key, value in list(output.items())[:20]}
    if isinstance(output, list):
        return {"count": len(output), "preview": [_short_value(value) for value in output[:5]]}
    return str(output)


def _short_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": sorted(str(key) for key in value.keys())[:12]}
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _extract_execution_dispatch(output: Any) -> dict[str, Any]:
    if isinstance(output, dict) and isinstance(output.get("_execution_dispatch"), dict):
        return _json_safe(output["_execution_dispatch"])
    return {}


def _strip_execution_dispatch(output: Any) -> Any:
    if isinstance(output, dict) and "_execution_dispatch" in output:
        return {key: value for key, value in output.items() if key != "_execution_dispatch"}
    return output
