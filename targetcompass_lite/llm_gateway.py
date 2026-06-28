import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .orchestration_policies import ROLE_DEPENDENCIES
from .schema_validation import validate_object
from .secrets import apply_project_secrets
from .v4 import content_hash, v4_dir


LLM_TASK_SCHEMA = "v4.llm_task_packet/0.1"
LLM_TASK_EXECUTION_SCHEMA = "v4.llm_task_execution/0.1"
LLM_AUDIT_SCHEMA = "v4.llm_call_audit/0.1"

LLM_ROLE_POLICIES = {
    "disease_normalizer": {
        "allowed": True,
        "purpose": "Convert user research request into structured ResearchSpec and DiseaseSpec.",
        "risk": "low",
        "must_not": ["invent datasets", "claim results", "approve own output"],
    },
    "dataset_scout": {
        "allowed": True,
        "purpose": "Propose database/GEO search strategies and explain metadata grouping candidates.",
        "risk": "medium",
        "must_not": ["download arbitrary URLs", "accept low-confidence groups without review"],
    },
    "planner": {
        "allowed": True,
        "purpose": "Suggest analysis decomposition and method choices from registered modules.",
        "risk": "medium",
        "must_not": ["change execution artifacts directly", "approve its own plan"],
    },
    "method_reviewer": {
        "allowed": True,
        "purpose": "Review method fit, grouping validity, and execution readiness.",
        "risk": "medium",
        "must_not": ["review outputs it generated", "skip evidence references"],
    },
    "result_reviewer": {
        "allowed": True,
        "purpose": "Review statistical outputs, logs, QC, candidate ranking, and limitations.",
        "risk": "high",
        "must_not": ["turn association into causality", "ignore failed QC"],
    },
    "causal_reviewer": {
        "allowed": True,
        "purpose": "Grade causal evidence after GWAS/QTL/coloc/MR artifacts exist.",
        "risk": "high",
        "must_not": ["infer causality without genetic or perturbation evidence"],
    },
    "report_writer": {
        "allowed": True,
        "purpose": "Write report text from accepted evidence, review items, and artifacts.",
        "risk": "medium",
        "must_not": ["add unsupported citations", "omit limitations"],
    },
}


def prepare_llm_task_packet(
    project_dir: Path,
    role_id: str,
    prompt: str = "",
    input_refs: dict[str, Any] | None = None,
    model: str = "",
    purpose: str = "",
    actor: str = "agent_service",
) -> dict[str, Any]:
    if role_id not in LLM_ROLE_POLICIES:
        raise ValueError(f"unknown LLM role_id: {role_id}")
    input_refs = input_refs or {}
    policy = LLM_ROLE_POLICIES[role_id]
    provider = os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "openai")
    model = model or _role_model(project_dir, role_id) or os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "gpt-4.1-mini")
    payload = {
        "schema_version": LLM_TASK_SCHEMA,
        "project_id": project_dir.name,
        "role_id": role_id,
        "actor": actor,
        "model": model,
        "provider": provider,
        "base_url": os.environ.get("TARGETCOMPASS_LLM_BASE_URL", ""),
        "purpose": purpose or policy["purpose"],
        "policy": policy,
        "dependencies": ROLE_DEPENDENCIES.get(role_id, []),
        "input_refs": input_refs,
        "prompt": prompt,
        "prompt_hash": content_hash({"prompt": prompt, "input_refs": input_refs}),
        "output_contract": _output_contract(role_id),
        "execution_mode": "ready" if os.environ.get("OPENAI_API_KEY") else "blocked_missing_api_key",
        "privacy": {
            "send_project_files_directly": False,
            "send_only_declared_prompt_and_refs": True,
            "requires_local_user_api_key": True,
        },
        "created_at": _now(),
    }
    packet_id = "llm_task_" + content_hash(payload)[:16]
    payload["packet_id"] = packet_id
    path = llm_task_dir(project_dir) / f"{packet_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["path"] = str(path.relative_to(project_dir)).replace("\\", "/")
    _record_llm_audit(project_dir, payload, status="prepared", failure_reason="")
    return payload


def execute_llm_task_packet(
    project_dir: Path,
    packet_id: str = "",
    role_id: str = "",
    prompt: str = "",
    input_refs: dict[str, Any] | None = None,
    model: str = "",
    purpose: str = "",
    actor: str = "agent_service",
) -> dict[str, Any]:
    apply_project_secrets(project_dir)
    packet = _load_or_prepare_packet(
        project_dir,
        packet_id=packet_id,
        role_id=role_id,
        prompt=prompt,
        input_refs=input_refs or {},
        model=model,
        purpose=purpose,
        actor=actor,
    )
    request_payload = _build_chat_request(packet)
    paths = _execution_paths(project_dir, packet["packet_id"])
    paths["request"].write_text(json.dumps(_redact_request(request_payload), indent=2, ensure_ascii=False), encoding="utf-8")
    started_at = _now()
    try:
        response_payload = _call_chat_completion(packet, request_payload)
        raw_text = _extract_chat_text(response_payload)
        parsed_output = _parse_json_text(raw_text)
        validation = _validate_role_output(packet["role_id"], parsed_output)
        status = "executed" if validation["valid"] else "failed"
        failure_reason = "" if validation["valid"] else "schema_validation_failed: " + "; ".join(validation["errors"])
        output = {
            "schema_version": LLM_TASK_EXECUTION_SCHEMA,
            "project_id": project_dir.name,
            "packet_id": packet["packet_id"],
            "role_id": packet["role_id"],
            "actor": actor,
            "provider": packet.get("provider", ""),
            "model": packet.get("model", ""),
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "parsed_output": parsed_output,
            "schema_validation": validation,
            "artifacts": {
                "request": _rel(paths["request"], project_dir),
                "response": _rel(paths["response"], project_dir),
                "output": _rel(paths["output"], project_dir),
            },
            "failure_reason": failure_reason,
        }
        paths["response"].write_text(json.dumps(_redact_response(response_payload), indent=2, ensure_ascii=False), encoding="utf-8")
        paths["output"].write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        _record_llm_audit(project_dir, packet, status=status, failure_reason=failure_reason, execution_artifacts=output["artifacts"])
        if not validation["valid"]:
            _write_recovery(project_dir, packet, failure_reason, output["artifacts"])
        return output
    except Exception as exc:
        failure_reason = str(exc)
        output = {
            "schema_version": LLM_TASK_EXECUTION_SCHEMA,
            "project_id": project_dir.name,
            "packet_id": packet.get("packet_id", ""),
            "role_id": packet.get("role_id", ""),
            "actor": actor,
            "provider": packet.get("provider", ""),
            "model": packet.get("model", ""),
            "status": "failed",
            "started_at": started_at,
            "finished_at": _now(),
            "parsed_output": {},
            "schema_validation": {"valid": False, "errors": [failure_reason]},
            "artifacts": {"request": _rel(paths["request"], project_dir), "output": _rel(paths["output"], project_dir)},
            "failure_reason": failure_reason,
        }
        paths["output"].write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        recovery = _write_recovery(project_dir, packet, failure_reason, output["artifacts"])
        output["recovery"] = recovery
        _record_llm_audit(project_dir, packet, status="failed", failure_reason=failure_reason, execution_artifacts=output["artifacts"])
        return output


def query_llm_audit(project_dir: Path, role_id: str = "", status: str = "", limit: int = 50) -> dict[str, Any]:
    rows = []
    path = llm_audit_path(project_dir)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if role_id and row.get("role_id") != role_id:
                continue
            if status and row.get("status") != status:
                continue
            rows.append(row)
    return {
        "schema_version": "v4.llm_call_audit_query/0.1",
        "project_id": project_dir.name,
        "query": {"role_id": role_id, "status": status, "limit": limit},
        "match_count": len(rows),
        "items": rows[-limit:],
    }


def llm_task_dir(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "llm_tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_audit_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "llm_call_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _record_llm_audit(
    project_dir: Path,
    packet: dict[str, Any],
    status: str,
    failure_reason: str,
    execution_artifacts: dict[str, str] | None = None,
) -> None:
    row = {
        "schema_version": LLM_AUDIT_SCHEMA,
        "timestamp": _now(),
        "project_id": project_dir.name,
        "packet_id": packet.get("packet_id", ""),
        "role_id": packet.get("role_id", ""),
        "actor": packet.get("actor", ""),
        "model": packet.get("model", ""),
        "provider": packet.get("provider", ""),
        "status": status,
        "execution_mode": packet.get("execution_mode", ""),
        "failure_reason": failure_reason,
        "prompt_hash": packet.get("prompt_hash", ""),
        "input_refs_hash": content_hash(packet.get("input_refs", {})),
        "output_contract": packet.get("output_contract", ""),
        "execution_artifacts": execution_artifacts or {},
    }
    with llm_audit_path(project_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _role_model(project_dir: Path, role_id: str) -> str:
    path = project_dir / "configs" / "role_models.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get(role_id, "")).strip()


def _output_contract(role_id: str) -> str:
    return {
        "disease_normalizer": "DiseaseNormalizerOutput",
        "dataset_scout": "DatasetScoutOutput",
        "planner": "PlannerOutput",
        "method_reviewer": "ReviewItem[]",
        "result_reviewer": "ReviewItem[]",
        "causal_reviewer": "CausalGradeReview",
        "report_writer": "StructuredReportPatch",
    }[role_id]


def _load_or_prepare_packet(
    project_dir: Path,
    packet_id: str,
    role_id: str,
    prompt: str,
    input_refs: dict[str, Any],
    model: str,
    purpose: str,
    actor: str,
) -> dict[str, Any]:
    if packet_id:
        path = llm_task_dir(project_dir) / f"{packet_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"LLM task packet not found: {packet_id}")
        return json.loads(path.read_text(encoding="utf-8"))
    if not role_id:
        raise ValueError("role_id is required when packet_id is not provided")
    return prepare_llm_task_packet(
        project_dir,
        role_id,
        prompt=prompt,
        input_refs=input_refs,
        model=model,
        purpose=purpose,
        actor=actor,
    )


def _build_chat_request(packet: dict[str, Any]) -> dict[str, Any]:
    schema = _role_schema(packet["role_id"])
    system_prompt = (
        "You are executing one TargetCompass v4.0 agent role. Return JSON only. "
        "Do not invent datasets, evidence, citations, artifacts, statistical results, or approvals. "
        "Use only the declared prompt and input_refs. Follow the output JSON schema exactly.\n"
        f"Role policy: {json.dumps(packet.get('policy', {}), ensure_ascii=False)}\n"
        f"Output schema: {json.dumps(schema, ensure_ascii=False)}"
    )
    return {
        "model": packet.get("model", ""),
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "project_id": packet.get("project_id", ""),
                        "role_id": packet.get("role_id", ""),
                        "purpose": packet.get("purpose", ""),
                        "prompt": packet.get("prompt", ""),
                        "input_refs": packet.get("input_refs", {}),
                        "dependencies": packet.get("dependencies", []),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }


def _call_chat_completion(packet: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    provider = (packet.get("provider") or os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "openai")).strip().lower()
    base_url = (packet.get("base_url") or os.environ.get("TARGETCOMPASS_LLM_BASE_URL", "")).strip().rstrip("/")
    if not base_url:
        base_url = "https://api.deepseek.com" if provider == "deepseek" else "https://api.openai.com/v1"
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider} chat request failed: {exc.code} {detail}") from exc


def _extract_chat_text(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if content:
            return content
    raise RuntimeError("chat completion response did not contain message content")


def _parse_json_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM output must be a JSON object")
    return parsed


def _validate_role_output(role_id: str, output: dict[str, Any]) -> dict[str, Any]:
    schema = _role_schema(role_id)
    errors = validate_object(output, schema, schema.get("schema_name", role_id))
    return {"schema_name": schema.get("schema_name", ""), "valid": not errors, "errors": errors}


def _role_schema(role_id: str) -> dict[str, Any]:
    from .orchestration_graph import ROLE_OUTPUT_SCHEMAS

    if role_id not in ROLE_OUTPUT_SCHEMAS:
        raise ValueError(f"unknown LLM role_id: {role_id}")
    return ROLE_OUTPUT_SCHEMAS[role_id]


def _execution_paths(project_dir: Path, packet_id: str) -> dict[str, Path]:
    base = llm_task_dir(project_dir)
    return {
        "request": base / f"{packet_id}_request.json",
        "response": base / f"{packet_id}_response.json",
        "output": base / f"{packet_id}_output.json",
        "recovery": base / f"{packet_id}_recovery.json",
    }


def _write_recovery(project_dir: Path, packet: dict[str, Any], failure_reason: str, artifacts: dict[str, str]) -> dict[str, Any]:
    recovery = {
        "schema_version": "v4.llm_task_recovery/0.1",
        "project_id": project_dir.name,
        "packet_id": packet.get("packet_id", ""),
        "role_id": packet.get("role_id", ""),
        "failure_reason": failure_reason,
        "retry_advice": [
            "Check that the selected model supports JSON object responses.",
            "Tighten the prompt with explicit required fields from output_contract.",
            "Retry with the same packet_id after correcting provider/model/API key configuration.",
        ],
        "artifacts": artifacts,
        "created_at": _now(),
    }
    path = _execution_paths(project_dir, packet.get("packet_id", "unknown"))["recovery"]
    path.write_text(json.dumps(recovery, indent=2, ensure_ascii=False), encoding="utf-8")
    recovery["path"] = _rel(path, project_dir)
    return recovery


def _redact_request(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _redact_response(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _rel(path: Path, project_dir: Path) -> str:
    return str(path.relative_to(project_dir)).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
