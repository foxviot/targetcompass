from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .agent_protocol import validate_agent_handoff
from .agent_specs import build_agent_specs
from .ids import hash_payload, make_stable_id
from .schemas import CLAIM_LEVELS, now_iso


ChatCaller = Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]]

LLM_ROLE_EXECUTION_SCHEMA = "v5.llm_role_execution/0.1"
LLM_ROLE_REQUEST_SCHEMA = "v5.llm_role_request/0.1"


def prepare_llm_role_request(
    project_dir: str | Path,
    agent_id: str,
    *,
    input_refs: dict[str, Any],
    prompt: str = "",
    model: str = "",
    actor: str = "agent_service",
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    specs = build_agent_specs()
    if agent_id not in specs:
        raise ValueError(f"unknown canonical agent_id: {agent_id}")
    provider = os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "openai")
    base_url = _base_url()
    model = model or os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "gpt-4.1-mini")
    request = {
        "schema_version": LLM_ROLE_REQUEST_SCHEMA,
        "request_id": "",
        "project_id": project_dir.name,
        "agent_id": agent_id,
        "actor": actor,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "input_refs": input_refs,
        "prompt": prompt,
        "agent_spec": specs[agent_id],
        "output_contract": _output_contract(specs[agent_id]),
        "created_at": now_iso(),
        "status": "ready" if os.environ.get("OPENAI_API_KEY") else "blocked_missing_api_key",
    }
    request["request_id"] = make_stable_id("llm_role_request", {"project_id": project_dir.name, "agent_id": agent_id, "input_refs": input_refs, "prompt": prompt, "model": model})
    _write_json(_request_path(project_dir, request["request_id"]), request)
    _append_audit(project_dir, request, status="prepared", failure_reason="")
    return request


def execute_llm_role(
    project_dir: str | Path,
    agent_id: str,
    *,
    input_refs: dict[str, Any],
    prompt: str = "",
    model: str = "",
    actor: str = "agent_service",
    timeout: int = 60,
    chat_caller: ChatCaller | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    request = prepare_llm_role_request(project_dir, agent_id, input_refs=input_refs, prompt=prompt, model=model, actor=actor)
    output_path = _output_path(project_dir, request["request_id"])
    if not os.environ.get("OPENAI_API_KEY"):
        output = _execution_output(request, status="blocked", parsed_output={}, validation={"valid": False, "errors": ["OPENAI_API_KEY is not set"]}, failure_reason="OPENAI_API_KEY is not set")
        _write_json(output_path, output)
        _append_audit(project_dir, request, status="blocked", failure_reason=output["failure_reason"], artifacts=output["artifacts"])
        return output

    chat_request = _chat_request(request)
    _write_json(_chat_request_path(project_dir, request["request_id"]), _redact_chat_request(chat_request))
    try:
        caller = chat_caller or _call_chat_completion
        response_payload = caller(f"{request['base_url']}/chat/completions", chat_request, _headers(), timeout)
        _write_json(_chat_response_path(project_dir, request["request_id"]), _redact_chat_response(response_payload))
        parsed = _parse_json_text(_extract_chat_text(response_payload))
        validation = validate_llm_role_output(parsed, request["agent_spec"])
        status = "executed" if validation["valid"] else "failed"
        failure_reason = "" if validation["valid"] else "; ".join(validation["errors"])
        output = _execution_output(request, status=status, parsed_output=parsed, validation=validation, failure_reason=failure_reason)
        _write_json(output_path, output)
        _append_audit(project_dir, request, status=status, failure_reason=failure_reason, artifacts=output["artifacts"])
        return output
    except Exception as exc:
        output = _execution_output(request, status="failed", parsed_output={}, validation={"valid": False, "errors": [str(exc)]}, failure_reason=str(exc))
        _write_json(output_path, output)
        _append_audit(project_dir, request, status="failed", failure_reason=str(exc), artifacts=output["artifacts"])
        return output


def validate_llm_role_output(output: dict[str, Any], agent_spec: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if not isinstance(output, dict):
        return {"valid": False, "errors": ["role output: expected JSON object"]}
    required = ["agent_id", "status", "output_object_refs", "assumptions", "open_questions", "blocking_issues", "claim_ceiling", "audit_notes"]
    for field in required:
        if field not in output:
            errors.append(f"{field}: missing required field")
    if errors:
        return {"valid": False, "errors": errors}
    if output.get("agent_id") != agent_spec["agent_id"]:
        errors.append(f"agent_id mismatch: expected {agent_spec['agent_id']}")
    for field in ["output_object_refs", "assumptions", "open_questions", "blocking_issues", "audit_notes"]:
        if not isinstance(output.get(field), list):
            errors.append(f"{field}: expected list")
    if isinstance(output.get("output_object_refs"), list):
        for idx, ref in enumerate(output.get("output_object_refs") or []):
            if not isinstance(ref, dict):
                errors.append(f"output_object_refs[{idx}]: expected object ref")
    ceiling = output.get("claim_ceiling") or {}
    if not isinstance(ceiling, dict):
        errors.append("claim_ceiling: expected object")
        max_allowed = None
    else:
        max_allowed = ceiling.get("max_allowed_claim")
    if max_allowed not in CLAIM_LEVELS:
        errors.append("claim_ceiling.max_allowed_claim is invalid")
    elif CLAIM_LEVELS.index(max_allowed) > CLAIM_LEVELS.index(agent_spec["max_claim_level"]):
        errors.append(f"claim ceiling exceeds agent max_claim_level {agent_spec['max_claim_level']}")
    handoff = output.get("handoff")
    expected_to = agent_spec.get("handoff_contract", {}).get("to_agent")
    if isinstance(handoff, dict) and expected_to:
        result = validate_agent_handoff(handoff, agent_spec["agent_id"], expected_to)
        if result["status"] == "invalid":
            errors.extend([f"handoff: {item}" for item in result["errors"]])
    return {"valid": not errors, "errors": errors}


def load_llm_role_audit(project_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(project_dir) / "v5" / "llm_roles" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _output_contract(agent_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "json_object",
        "required_fields": ["agent_id", "status", "output_object_refs", "assumptions", "open_questions", "blocking_issues", "claim_ceiling", "audit_notes"],
        "field_shapes": {
            "output_object_refs": [{"object_type": "string", "object_id": "string"}],
            "assumptions": ["string"],
            "open_questions": ["string"],
            "blocking_issues": ["string"],
            "claim_ceiling": {"max_allowed_claim": agent_spec["max_claim_level"], "reason": "string"},
            "audit_notes": ["string"],
        },
        "must_not": agent_spec["forbidden_actions"],
        "max_claim_level": agent_spec["max_claim_level"],
        "required_output_refs": agent_spec["required_output_refs"],
    }


def _chat_request(request: dict[str, Any]) -> dict[str, Any]:
    system = (
        "You are a TargetCompass v5 canonical agent. Return only one JSON object. "
        "Do not include markdown. Do not execute tools. Do not invent evidence. "
        "Respect forbidden_actions and max_claim_level. "
        "All *_refs fields must be arrays of objects, never maps or strings. "
        "claim_ceiling must be an object with max_allowed_claim and reason."
    )
    user = {
        "agent_id": request["agent_id"],
        "responsibility": request["agent_spec"]["responsibility"],
        "forbidden_actions": request["agent_spec"]["forbidden_actions"],
        "input_refs": request["input_refs"],
        "prompt": request["prompt"],
        "output_contract": request["output_contract"],
    }
    return {
        "model": request["model"],
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    }


def _call_chat_completion(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}


def _base_url() -> str:
    return os.environ.get("TARGETCOMPASS_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _extract_chat_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return str(message.get("content", ""))


def _parse_json_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    return json.loads(stripped)


def _execution_output(request: dict[str, Any], *, status: str, parsed_output: dict[str, Any], validation: dict[str, Any], failure_reason: str) -> dict[str, Any]:
    request_id = request["request_id"]
    return {
        "schema_version": LLM_ROLE_EXECUTION_SCHEMA,
        "execution_id": make_stable_id("llm_role_execution", {"request_id": request_id, "status": status, "output": parsed_output}),
        "project_id": request["project_id"],
        "request_id": request_id,
        "agent_id": request["agent_id"],
        "actor": request["actor"],
        "provider": request["provider"],
        "model": request["model"],
        "status": status,
        "parsed_output": parsed_output,
        "schema_validation": validation,
        "failure_reason": failure_reason,
        "finished_at": now_iso(),
        "artifacts": {
            "request": f"v5/llm_roles/requests/{request_id}.json",
            "chat_request": f"v5/llm_roles/requests/{request_id}.chat_request.json",
            "chat_response": f"v5/llm_roles/responses/{request_id}.chat_response.json",
            "output": f"v5/llm_roles/outputs/{request_id}.json",
        },
    }


def _append_audit(project_dir: Path, request: dict[str, Any], *, status: str, failure_reason: str, artifacts: dict[str, str] | None = None) -> None:
    path = project_dir / "v5" / "llm_roles" / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "v5.llm_role_audit/0.1",
        "timestamp": now_iso(),
        "project_id": project_dir.name,
        "request_id": request.get("request_id", ""),
        "agent_id": request.get("agent_id", ""),
        "actor": request.get("actor", ""),
        "provider": request.get("provider", ""),
        "model": request.get("model", ""),
        "status": status,
        "failure_reason": failure_reason,
        "input_refs_hash": hash_payload(request.get("input_refs", {})),
        "prompt_hash": hash_payload(request.get("prompt", "")),
        "artifacts": artifacts or {},
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _request_path(project_dir: Path, request_id: str) -> Path:
    return project_dir / "v5" / "llm_roles" / "requests" / f"{request_id}.json"


def _chat_request_path(project_dir: Path, request_id: str) -> Path:
    return project_dir / "v5" / "llm_roles" / "requests" / f"{request_id}.chat_request.json"


def _chat_response_path(project_dir: Path, request_id: str) -> Path:
    return project_dir / "v5" / "llm_roles" / "responses" / f"{request_id}.chat_response.json"


def _output_path(project_dir: Path, request_id: str) -> Path:
    return project_dir / "v5" / "llm_roles" / "outputs" / f"{request_id}.json"


def _redact_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload)


def _redact_chat_response(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload)
