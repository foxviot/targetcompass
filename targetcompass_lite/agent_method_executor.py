import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .methods.contracts import MethodContext
from .methods.registry import available_project_methods, load_method_config, run_method
from .llm_gateway import LLM_ROLE_POLICIES
from .orchestration_policies import ROLE_DEPENDENCIES, fallback_method_for_role
from .v4 import content_hash, v4_dir


AGENT_METHOD_CALL_SCHEMA = "v4.agent_method_call/0.1"
AGENT_METHOD_RECOVERY_SCHEMA = "v4.agent_method_recovery/0.1"


def execute_agent_role_method(
    project_dir: Path,
    role_id: str,
    input_refs: dict[str, Any],
    method_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    actor: str = "agent_service",
) -> dict[str, Any]:
    parameters = parameters or {}
    selected_method = method_id or load_method_config(project_dir).get(role_id, "")
    request = _method_request(project_dir, role_id, input_refs, selected_method, parameters, actor)
    request_path = _call_dir(project_dir) / f"{request['call_id']}_request.json"
    result_path = _call_dir(project_dir) / f"{request['call_id']}_result.json"
    request_path.write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        context = MethodContext(
            project_dir=project_dir,
            interest=_read_text(project_dir / "research_interest.md"),
            parser=str(parameters.get("parser", "typed_orchestration")),
            selected_datasets=_selected_datasets(project_dir),
            confirmed=True,
            idea_count=int(parameters.get("idea_count", 0) or 0),
            role_id=role_id,
            input_refs=input_refs,
            parameters={**parameters, "actor": actor, "method_call_id": request["call_id"]},
        )
        method_result = run_method(role_id, context, method_id=selected_method or None)
        typed_output = _typed_output_from_method_result(project_dir, role_id, method_result.details)
        response = {
            "schema_version": AGENT_METHOD_CALL_SCHEMA,
            "call_id": request["call_id"],
            "project_id": project_dir.name,
            "role_id": role_id,
            "method_id": method_result.details.get("method_id", selected_method),
            "method_status": method_result.status,
            "method_message": method_result.message,
            "input_packet": str(request_path.relative_to(project_dir)).replace("\\", "/"),
            "output_schema": _schema_name(role_id),
            "typed_output": typed_output,
            "recovery": {"required": False},
            "finished_at": _now(),
        }
        if method_result.status.lower() in {"fail", "failed", "error"}:
            raise RuntimeError(method_result.message or f"{role_id} method failed")
        result_path.write_text(json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8")
        return response
    except Exception as exc:
        recovery = _write_recovery(project_dir, role_id, request, str(exc))
        failure = {
            "schema_version": AGENT_METHOD_CALL_SCHEMA,
            "call_id": request["call_id"],
            "project_id": project_dir.name,
            "role_id": role_id,
            "method_id": selected_method,
            "method_status": "failed",
            "method_message": str(exc),
            "input_packet": str(request_path.relative_to(project_dir)).replace("\\", "/"),
            "output_schema": _schema_name(role_id),
            "typed_output": {},
            "recovery": recovery,
            "finished_at": _now(),
        }
        result_path.write_text(json.dumps(failure, indent=2, ensure_ascii=False), encoding="utf-8")
        raise RuntimeError(f"{role_id} method execution failed: {exc}") from exc


def record_agent_method_recovery(
    project_dir: Path,
    role_id: str,
    failure_reason: str,
    input_refs: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    call_id: str = "",
) -> dict[str, Any]:
    request = {
        "call_id": call_id or "schema_validation_" + content_hash({"project": project_dir.name, "role": role_id, "failure": failure_reason})[:16],
        "input_refs": input_refs or {},
        "parameters": parameters or {},
    }
    return _write_recovery(project_dir, role_id, request, failure_reason)


def _method_request(
    project_dir: Path,
    role_id: str,
    input_refs: dict[str, Any],
    method_id: str,
    parameters: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    method_meta = _method_meta(project_dir, role_id, method_id)
    payload = {
        "project_id": project_dir.name,
        "role_id": role_id,
        "method_id": method_id,
        "input_refs": input_refs,
        "parameters": parameters,
        "actor": actor,
        "created_at": _now(),
    }
    call_id = "agent_method_call_" + content_hash(payload)[:16]
    return {
        "schema_version": AGENT_METHOD_CALL_SCHEMA,
        "call_id": call_id,
        "project_id": project_dir.name,
        "role_id": role_id,
        "method_id": method_id,
        "method_label": method_meta.get("label", ""),
        "gpt_compatible": bool(method_meta.get("gpt_compatible", False)),
        "llm_call_packet": _llm_call_packet(project_dir, role_id, method_id, method_meta),
        "input_schema": {
            "role_id": "string",
            "input_refs": "object",
            "parameters": "object",
            "method_id": "string",
        },
        "expected_output_schema": _schema_name(role_id),
        "dependencies": ROLE_DEPENDENCIES.get(role_id, []),
        "input_refs": input_refs,
        "parameters": parameters,
        "actor": actor,
        "created_at": payload["created_at"],
    }


def _typed_output_from_method_result(project_dir: Path, role_id: str, details: dict[str, Any]) -> dict[str, Any]:
    if isinstance(details.get("typed_output"), dict):
        return details["typed_output"]
    if role_id == "disease_normalizer":
        return {
            "project_id": project_dir.name,
            "research_spec_ref": _existing(project_dir, "research_spec.json"),
            "disease_spec_ref": _existing(project_dir, "v4/disease_spec.json"),
            "normalized_terms": details.get("normalized_terms", []),
        }
    if role_id == "dataset_scout":
        return {
            "project_id": project_dir.name,
            "dataset_candidates_ref": _existing(project_dir, "dataset_match_report.csv"),
            "recommendations_ref": _existing(project_dir, "results/geo_discovery/geo_recommendations.json"),
            "candidate_count": _count_csv_rows(project_dir / "eligible_datasets.csv"),
        }
    if role_id == "planner":
        return {
            "project_id": project_dir.name,
            "analysis_plan_ref": _existing(project_dir, "analysis_plan.json"),
            "work_orders_ref": _existing(project_dir, "v4/work_orders.json"),
            "module_count": _module_count(project_dir),
        }
    if role_id in {"method_reviewer", "result_reviewer", "causal_reviewer"}:
        subject = ROLE_DEPENDENCIES.get(role_id, [""])[-1] or role_id
        payload = {
            "project_id": project_dir.name,
            "review_items": details.get("review_items")
            or [
                {
                    "review_id": "review_" + content_hash({"project": project_dir.name, "role": role_id, "details": details})[:12],
                    "subject_role": subject,
                    "decision": details.get("decision", "needs_review"),
                    "reason": details.get("reason", "Agent method completed; reviewer record created for human or reviewer-agent resolution."),
                }
            ],
            "decision": details.get("decision", "needs_review"),
        }
        if role_id == "causal_reviewer":
            payload["causal_grades_ref"] = _existing(project_dir, "results/causal_evidence/causal_evidence_grades.tsv") or "not_available"
        return payload
    return {
        "project_id": project_dir.name,
        "report_ref": _existing(project_dir, "reports/target_report.html"),
        "structured_report_ref": _existing(project_dir, "reports/target_report_structured.json"),
        "evidence_refs": _report_evidence_refs(project_dir),
    }


def _write_recovery(project_dir: Path, role_id: str, request: dict[str, Any], failure_reason: str) -> dict[str, Any]:
    payload = {
        "schema_version": AGENT_METHOD_RECOVERY_SCHEMA,
        "project_id": project_dir.name,
        "role_id": role_id,
        "failed_call_id": request["call_id"],
        "failure_reason": failure_reason,
        "fallback_method": fallback_method_for_role(role_id),
        "resume_payload": {
            "role_id": role_id,
            "input_refs": request.get("input_refs", {}),
            "parameters": request.get("parameters", {}),
        },
        "recommended_actions": [
            "inspect method input packet",
            "verify required artifact refs exist",
            "retry with fallback method or force local rerun",
        ],
        "created_at": _now(),
    }
    out = v4_dir(project_dir) / "agent_recovery" / f"{role_id}_last_failure.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**payload, "path": str(out.relative_to(project_dir)).replace("\\", "/")}


def _llm_call_packet(project_dir: Path, role_id: str, method_id: str, method_meta: dict[str, Any]) -> dict[str, Any]:
    policy = LLM_ROLE_POLICIES.get(role_id, {})
    return {
        "provider": "openai_or_local",
        "model_config_ref": "configs/role_models.json",
        "role_id": role_id,
        "method_id": method_id,
        "enabled": bool(method_meta.get("gpt_compatible", False) and policy.get("allowed", False)),
        "policy_ref": "v4.llm_role_policies/0.1",
        "policy": policy,
        "prompt_inputs": ["research_interest.md", *ROLE_DEPENDENCIES.get(role_id, [])],
        "output_contract": _schema_name(role_id),
        "privacy": {"project_local_files_only": True, "external_calls_require_configured_api_key": True},
    }


def _method_meta(project_dir: Path, role_id: str, method_id: str) -> dict[str, Any]:
    for row in available_project_methods(project_dir).get(role_id, []):
        if row.get("method_id") == method_id:
            return row
    return {}


def _call_dir(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "agent_method_calls"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _schema_name(role_id: str) -> str:
    return {
        "disease_normalizer": "DiseaseNormalizerOutput",
        "dataset_scout": "DatasetScoutOutput",
        "planner": "PlannerOutput",
        "method_reviewer": "MethodReviewerOutput",
        "result_reviewer": "ResultReviewerOutput",
        "causal_reviewer": "CausalReviewerOutput",
        "report_writer": "ReportWriterOutput",
    }[role_id]


def _selected_datasets(project_dir: Path) -> list[str]:
    path = project_dir / "eligible_datasets.csv"
    if not path.exists():
        return []
    rows = path.read_text(encoding="utf-8").splitlines()[1:]
    return [row.split(",", 1)[0].split("\t", 1)[0].strip() for row in rows if row.strip()]


def _existing(project_dir: Path, relative: str) -> str:
    return relative if (project_dir / relative).exists() else ""


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    return max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1)


def _module_count(project_dir: Path) -> int:
    data = json.loads((project_dir / "analysis_plan.json").read_text(encoding="utf-8")) if (project_dir / "analysis_plan.json").exists() else {}
    return len(data.get("modules", [])) if isinstance(data, dict) else 0


def _report_evidence_refs(project_dir: Path) -> list[str]:
    data = json.loads((project_dir / "reports" / "target_report_structured.json").read_text(encoding="utf-8")) if (project_dir / "reports" / "target_report_structured.json").exists() else {}
    refs = []
    for payload in data.get("report_evidence_refs", {}).values() if isinstance(data, dict) else []:
        if isinstance(payload, dict):
            refs.extend(payload.get("evidence_refs", []))
    return refs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
