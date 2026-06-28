import json
import os
from pathlib import Path
from typing import Any

from .agent_method_executor import execute_agent_role_method
from .llm_gateway import LLM_ROLE_POLICIES, execute_llm_task_packet
from .methods.registry import available_project_methods, load_method_config
from .secrets import apply_project_secrets


EXECUTION_DISPATCH_SCHEMA = "v4.role_execution_dispatch/0.1"


def dispatch_agent_role_execution(
    project_dir: Path,
    role_id: str,
    input_refs: dict[str, Any],
    method_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    actor: str = "agent_service",
) -> dict[str, Any]:
    parameters = parameters or {}
    apply_project_secrets(project_dir)
    selected_method = method_id or load_method_config(project_dir).get(role_id, "")
    method_meta = _method_meta(project_dir, role_id, selected_method)
    backend = _select_backend(project_dir, role_id, selected_method, method_meta, parameters)
    if backend == "llm":
        try:
            return _execute_llm_role(project_dir, role_id, input_refs, selected_method, method_meta, parameters, actor)
        except Exception as exc:
            if str(parameters.get("execution_backend", "auto")).strip().lower() == "llm":
                raise
            local_result = _execute_local_role(project_dir, role_id, input_refs, selected_method, parameters, actor)
            local_result["llm_fallback"] = {"triggered": True, "failure_reason": str(exc)}
            return local_result
    if backend == "codex":
        raise RuntimeError("codex role execution backend is reserved; use local or llm for this role")
    return _execute_local_role(project_dir, role_id, input_refs, selected_method, parameters, actor)


def _execute_local_role(
    project_dir: Path,
    role_id: str,
    input_refs: dict[str, Any],
    selected_method: str,
    parameters: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    local_result = execute_agent_role_method(project_dir, role_id, input_refs, method_id=selected_method or None, parameters=parameters, actor=actor)
    return {
        "schema_version": EXECUTION_DISPATCH_SCHEMA,
        "project_id": project_dir.name,
        "role_id": role_id,
        "method_id": local_result.get("method_id", selected_method),
        "executor_backend": "local",
        "model": "local",
        "typed_output": local_result["typed_output"],
        "artifacts": {
            "input_packet": local_result.get("input_packet", ""),
            "method_call_id": local_result.get("call_id", ""),
        },
        "recovery": local_result.get("recovery", {"required": False}),
    }


def _execute_llm_role(
    project_dir: Path,
    role_id: str,
    input_refs: dict[str, Any],
    method_id: str,
    method_meta: dict[str, Any],
    parameters: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    prompt = _role_prompt(project_dir, role_id, input_refs, method_id, method_meta, parameters)
    result = execute_llm_task_packet(
        project_dir,
        role_id=role_id,
        prompt=prompt,
        input_refs=input_refs,
        model=str(parameters.get("model", "")),
        purpose=method_meta.get("description", ""),
        actor=actor,
    )
    if result.get("status") != "executed":
        raise RuntimeError(result.get("failure_reason") or f"{role_id} LLM execution failed")
    return {
        "schema_version": EXECUTION_DISPATCH_SCHEMA,
        "project_id": project_dir.name,
        "role_id": role_id,
        "method_id": method_id,
        "executor_backend": "llm",
        "model": result.get("model", ""),
        "typed_output": result.get("parsed_output", {}),
        "artifacts": result.get("artifacts", {}),
        "llm_packet_id": result.get("packet_id", ""),
        "recovery": {"required": False},
    }


def _select_backend(
    project_dir: Path,
    role_id: str,
    method_id: str,
    method_meta: dict[str, Any],
    parameters: dict[str, Any],
) -> str:
    requested = str(parameters.get("execution_backend") or _configured_backend(project_dir, role_id) or "auto").strip().lower()
    if requested in {"local", "llm", "codex"}:
        if requested == "llm" and not _llm_ready(project_dir, role_id, method_meta):
            raise RuntimeError(f"LLM backend requested but unavailable for {role_id}")
        return requested
    if requested != "auto":
        raise ValueError(f"unsupported role execution_backend: {requested}")
    if _llm_ready(project_dir, role_id, method_meta):
        return "llm"
    return "local"


def _configured_backend(project_dir: Path, role_id: str) -> str:
    path = project_dir / "configs" / "role_execution_backends.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    value = str(data.get(role_id, "")).strip().lower()
    return value if value in {"auto", "local", "llm", "codex"} else ""


def _llm_ready(project_dir: Path, role_id: str, method_meta: dict[str, Any]) -> bool:
    apply_project_secrets(project_dir)
    policy = LLM_ROLE_POLICIES.get(role_id, {})
    return bool(os.environ.get("OPENAI_API_KEY") and method_meta.get("gpt_compatible", False) and policy.get("allowed", False))


def _method_meta(project_dir: Path, role_id: str, method_id: str) -> dict[str, Any]:
    for row in available_project_methods(project_dir).get(role_id, []):
        if row.get("method_id") == method_id:
            return row
    return {"method_id": method_id, "gpt_compatible": False, "description": ""}


def _role_prompt(project_dir: Path, role_id: str, input_refs: dict[str, Any], method_id: str, method_meta: dict[str, Any], parameters: dict[str, Any]) -> str:
    interest = (project_dir / "research_interest.md").read_text(encoding="utf-8", errors="replace") if (project_dir / "research_interest.md").exists() else ""
    return (
        f"Execute TargetCompass role: {role_id}\n"
        f"Project: {project_dir.name}\n"
        f"Research interest: {interest[:4000]}\n"
        f"Selected method: {method_id}\n"
        f"Method description: {method_meta.get('description', '')}\n"
        f"Input refs: {input_refs}\n"
        f"Parameters: {parameters}\n"
        "Return only the role output JSON object required by the schema. "
        "Use artifact paths from input_refs or existing project conventions; do not invent unsupported scientific findings."
    )
