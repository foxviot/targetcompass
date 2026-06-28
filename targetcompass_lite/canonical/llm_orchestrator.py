from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .agent_specs import build_agent_specs
from .llm_role_execution import ChatCaller, execute_llm_role
from .schemas import now_iso
from ..secrets import apply_project_secrets


LLM_ORCHESTRATION_SCHEMA = "v5.llm_orchestration_run/0.1"
CANONICAL_AGENT_ORDER = [
    "question_normalizer",
    "scope_resolver",
    "evidence_plan_builder",
    "resource_discovery_agent",
    "method_adapter_workorder_compiler",
    "result_auditor",
    "evidence_synthesizer_reporter",
]


def run_canonical_llm_roles(
    project_dir: str | Path,
    *,
    user_question: str = "",
    model_by_agent: dict[str, str] | None = None,
    max_retries: int = 1,
    fallback_to_local: bool = True,
    chat_caller: ChatCaller | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    apply_project_secrets(project_dir)
    specs = build_agent_specs()
    model_by_agent = model_by_agent or {}
    previous_refs: dict[str, Any] = {"user_question": user_question}
    role_runs = []
    for agent_id in CANONICAL_AGENT_ORDER:
        if agent_id not in specs:
            raise ValueError(f"missing canonical agent spec: {agent_id}")
        run = _execute_with_retry(
            project_dir,
            agent_id,
            input_refs=previous_refs,
            model=model_by_agent.get(agent_id, ""),
            max_retries=max_retries,
            fallback_to_local=fallback_to_local,
            chat_caller=chat_caller,
            timeout=timeout,
        )
        role_runs.append(run)
        previous_refs = _next_refs(previous_refs, run)
        if run.get("status") == "blocked":
            break
    payload = {
        "schema_version": LLM_ORCHESTRATION_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "agent_count": len(CANONICAL_AGENT_ORDER),
        "executed_count": len([row for row in role_runs if row.get("status") == "executed"]),
        "fallback_count": len([row for row in role_runs if row.get("executor_backend") == "local_fallback"]),
        "failed_count": len([row for row in role_runs if row.get("status") in {"failed", "blocked"}]),
        "role_runs": role_runs,
        "status": "completed" if role_runs and all(row.get("status") in {"executed", "fallback"} for row in role_runs) else "review_required",
        "policy": {
            "default_backend": "llm",
            "schema_validation_required": True,
            "retry_attempts": max_retries,
            "fallback_to_local": fallback_to_local,
            "audit_log": "v5/llm_roles/audit.jsonl",
        },
    }
    out = project_dir / "v5" / "llm_roles" / "llm_orchestration_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _execute_with_retry(
    project_dir: Path,
    agent_id: str,
    *,
    input_refs: dict[str, Any],
    model: str,
    max_retries: int,
    fallback_to_local: bool,
    chat_caller: ChatCaller | None,
    timeout: int,
) -> dict[str, Any]:
    attempts = []
    for attempt in range(max(1, max_retries + 1)):
        result = execute_llm_role(
            project_dir,
            agent_id,
            input_refs=input_refs,
            prompt=_agent_prompt(agent_id),
            model=model,
            actor="canonical_llm_orchestrator",
            timeout=timeout,
            chat_caller=chat_caller,
        )
        attempts.append(_summarize_execution(result, attempt + 1, "llm"))
        if result.get("status") == "executed":
            return {**attempts[-1], "attempts": attempts, "output_refs": (result.get("parsed_output") or {}).get("output_object_refs", [])}
    if fallback_to_local:
        fallback = _local_fallback(agent_id, input_refs)
        attempts.append({"attempt": len(attempts) + 1, "executor_backend": "local_fallback", "status": "fallback", "failure_reason": ""})
        return {**attempts[-1], "agent_id": agent_id, "attempts": attempts, "output_refs": fallback["output_object_refs"], "parsed_output": fallback}
    failed = attempts[-1] if attempts else {"status": "failed", "failure_reason": "not executed"}
    failed["agent_id"] = agent_id
    return failed


def _summarize_execution(result: dict[str, Any], attempt: int, backend: str) -> dict[str, Any]:
    return {
        "agent_id": result.get("agent_id", ""),
        "attempt": attempt,
        "executor_backend": backend,
        "status": result.get("status", ""),
        "request_id": result.get("request_id", ""),
        "execution_id": result.get("execution_id", ""),
        "failure_reason": result.get("failure_reason", ""),
        "artifacts": result.get("artifacts", {}),
        "schema_validation": result.get("schema_validation", {}),
    }


def _local_fallback(agent_id: str, input_refs: dict[str, Any]) -> dict[str, Any]:
    spec = build_agent_specs()[agent_id]
    return {
        "agent_id": agent_id,
        "status": "fallback",
        "output_object_refs": [{"object_type": ref, "object_id": f"fallback_{agent_id}_{idx}"} for idx, ref in enumerate(spec.get("required_output_refs", []), 1)],
        "assumptions": ["Local fallback emitted refs only; no biological claim was generated."],
        "open_questions": [],
        "blocking_issues": [],
        "claim_ceiling": {"max_allowed_claim": spec.get("max_claim_level", "descriptive"), "reason": "Fallback output contains references only."},
        "audit_notes": ["Fallback used after LLM execution did not produce a valid role output."],
        "input_refs": input_refs,
    }


def _next_refs(previous_refs: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    refs = dict(previous_refs)
    refs[f"{run.get('agent_id', 'agent')}_output_refs"] = run.get("output_refs", [])
    refs[f"{run.get('agent_id', 'agent')}_status"] = run.get("status", "")
    return refs


def _agent_prompt(agent_id: str) -> str:
    return (
        f"Execute canonical role {agent_id}. Return JSON only. "
        "Pass object/artifact/evidence refs rather than prose conclusions. "
        "If required inputs are missing, report blocking_issues."
    )
