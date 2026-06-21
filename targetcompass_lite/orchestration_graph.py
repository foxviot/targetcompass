import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_roles import AGENT_ROLES
from .methods.registry import load_method_config
from .role_runner import load_role_runs
from .schema_validation import validate_object
from .v4 import content_hash, v4_dir


ORCHESTRATION_SCHEMA = "v4.typed_orchestration_graph/0.1"

ROLE_DEPENDENCIES = {
    "disease_normalizer": [],
    "dataset_scout": ["disease_normalizer"],
    "planner": ["dataset_scout"],
    "method_reviewer": ["planner"],
    "result_reviewer": ["method_reviewer"],
    "causal_reviewer": ["result_reviewer"],
    "report_writer": ["result_reviewer", "causal_reviewer"],
}

REVIEWER_ROLES = {"method_reviewer", "result_reviewer", "causal_reviewer"}
GENERATOR_ROLES = {"disease_normalizer", "dataset_scout", "planner", "report_writer"}

REVIEW_ITEM_SCHEMA = {
    "type": "object",
    "required": ["review_id", "subject_role", "decision", "reason"],
    "properties": {
        "review_id": {"type": "string", "minLength": 1},
        "subject_role": {"type": "string", "minLength": 1},
        "decision": {"type": "string", "enum": ["approve", "needs_review", "reject"]},
        "reason": {"type": "string", "minLength": 1},
    },
}

ROLE_OUTPUT_SCHEMAS = {
    "disease_normalizer": {
        "schema_name": "DiseaseNormalizerOutput",
        "type": "object",
        "required": ["project_id", "research_spec_ref", "disease_spec_ref"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "research_spec_ref": {"type": "string", "minLength": 1},
            "disease_spec_ref": {"type": "string", "minLength": 1},
            "normalized_terms": {"type": "array", "items": {"type": "string"}},
        },
    },
    "dataset_scout": {
        "schema_name": "DatasetScoutOutput",
        "type": "object",
        "required": ["project_id", "dataset_candidates_ref", "recommendations_ref"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "dataset_candidates_ref": {"type": "string", "minLength": 1},
            "recommendations_ref": {"type": "string", "minLength": 1},
            "candidate_count": {"type": "integer", "minimum": 0},
        },
    },
    "planner": {
        "schema_name": "PlannerOutput",
        "type": "object",
        "required": ["project_id", "analysis_plan_ref", "work_orders_ref"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "analysis_plan_ref": {"type": "string", "minLength": 1},
            "work_orders_ref": {"type": "string", "minLength": 1},
            "module_count": {"type": "integer", "minimum": 0},
        },
    },
    "method_reviewer": {
        "schema_name": "MethodReviewerOutput",
        "type": "object",
        "required": ["project_id", "review_items"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "review_items": {"type": "array", "minItems": 1, "items": REVIEW_ITEM_SCHEMA},
            "decision": {"type": "string", "enum": ["approve", "needs_review", "reject"]},
        },
    },
    "result_reviewer": {
        "schema_name": "ResultReviewerOutput",
        "type": "object",
        "required": ["project_id", "review_items"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "review_items": {"type": "array", "minItems": 1, "items": REVIEW_ITEM_SCHEMA},
            "decision": {"type": "string", "enum": ["approve", "needs_review", "reject"]},
        },
    },
    "causal_reviewer": {
        "schema_name": "CausalReviewerOutput",
        "type": "object",
        "required": ["project_id", "review_items", "causal_grades_ref"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "review_items": {"type": "array", "minItems": 1, "items": REVIEW_ITEM_SCHEMA},
            "causal_grades_ref": {"type": "string", "minLength": 1},
            "decision": {"type": "string", "enum": ["approve", "needs_review", "reject"]},
        },
    },
    "report_writer": {
        "schema_name": "ReportWriterOutput",
        "type": "object",
        "required": ["project_id", "report_ref", "structured_report_ref", "evidence_refs"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1},
            "report_ref": {"type": "string", "minLength": 1},
            "structured_report_ref": {"type": "string", "minLength": 1},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def build_typed_orchestration_graph(project_dir: Path) -> dict[str, Any]:
    runs = load_role_runs(project_dir).get("runs", [])
    latest_by_role = _latest_by_role(runs)
    method_config = load_method_config(project_dir)
    nodes = []
    edges = []
    for role in AGENT_ROLES:
        role_id = role["role_id"]
        latest = latest_by_role.get(role_id, {})
        validation = validate_role_output_packet(project_dir, role_id, latest)
        nodes.append(
            {
                "node_id": f"role:{role_id}",
                "role_id": role_id,
                "stage": role.get("stage", ""),
                "schema": ROLE_OUTPUT_SCHEMAS[role_id]["schema_name"],
                "schema_hash": content_hash(ROLE_OUTPUT_SCHEMAS[role_id]),
                "selected_method": method_config.get(role_id, ""),
                "selected_model": latest.get("model", "local"),
                "latest_role_run_id": latest.get("role_run_id", ""),
                "status": latest.get("status", "pending") if latest else "pending",
                "output_packet": latest.get("output_packet", ""),
                "schema_valid": validation["valid"],
                "schema_errors": validation["errors"],
                "retry_policy": retry_policy_for_role(role_id),
                "fallback_policy": fallback_policy_for_role(role_id),
                "approval_policy": approval_policy_for_role(role_id),
            }
        )
        for dep in ROLE_DEPENDENCIES.get(role_id, []):
            edges.append({"from": f"role:{dep}", "to": f"role:{role_id}", "edge_type": "requires_output"})
    payload = {
        "schema_version": ORCHESTRATION_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "strict_json_schema_per_role": True,
            "generator_cannot_approve_own_outputs": True,
            "reviewer_must_write_review_items": True,
            "role_retry_and_fallback_required": True,
        },
        "role_schemas": {role_id: _public_schema(schema) for role_id, schema in ROLE_OUTPUT_SCHEMAS.items()},
        "nodes": nodes,
        "edges": edges,
        "graph_hash": content_hash({"nodes": nodes, "edges": edges}),
    }
    path = typed_orchestration_graph_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def typed_orchestration_graph_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "typed_orchestration_graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def validate_role_output_packet(project_dir: Path, role_id: str, role_run: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = ROLE_OUTPUT_SCHEMAS[role_id]
    role_run = role_run or _latest_by_role(load_role_runs(project_dir).get("runs", [])).get(role_id, {})
    if not role_run:
        return {"valid": False, "errors": ["role has not run"]}
    output_packet = _read_json(project_dir / role_run.get("output_packet", ""), {})
    normalized = _normalize_output(role_id, project_dir, output_packet)
    errors = validate_object(normalized, schema, schema["schema_name"])
    errors.extend(_approval_policy_errors(role_id, role_run, output_packet, normalized))
    return {
        "schema_name": schema["schema_name"],
        "role_id": role_id,
        "valid": not errors,
        "errors": errors,
        "normalized_output": normalized,
    }


def retry_policy_for_role(role_id: str) -> dict[str, Any]:
    return {
        "max_attempts": 2 if role_id in REVIEWER_ROLES else 1,
        "retry_on": ["schema_validation_failed", "transient_tool_failure"],
        "backoff_seconds": [0, 5],
    }


def fallback_policy_for_role(role_id: str) -> dict[str, Any]:
    return {
        "fallback_allowed": True,
        "fallback_method": {
            "disease_normalizer": "local_disease_normalizer_v0",
            "dataset_scout": "local_dataset_scout_v0",
            "planner": "local_planner_v0",
            "method_reviewer": "local_method_reviewer_v0",
            "result_reviewer": "local_result_reviewer_v0",
            "causal_reviewer": "local_causal_reviewer_v0",
            "report_writer": "local_report_writer_v0",
        }[role_id],
        "requires_review_after_fallback": role_id in REVIEWER_ROLES,
    }


def approval_policy_for_role(role_id: str) -> dict[str, Any]:
    return {
        "can_approve_outputs": role_id in REVIEWER_ROLES,
        "cannot_approve_roles": sorted(GENERATOR_ROLES if role_id in GENERATOR_ROLES else [role_id]),
        "must_write_review_items": role_id in REVIEWER_ROLES,
    }


def _normalize_output(role_id: str, project_dir: Path, output_packet: dict[str, Any]) -> dict[str, Any]:
    refs = set(output_packet.get("output_refs", []))
    summary = output_packet.get("output_summary", {})
    if role_id == "disease_normalizer":
        return {
            "project_id": project_dir.name,
            "research_spec_ref": _first_ref(refs, "research_spec.json"),
            "disease_spec_ref": _first_ref(refs, "v4/disease_spec.json"),
            "normalized_terms": summary.get("normalized_terms", []) if isinstance(summary, dict) else [],
        }
    if role_id == "dataset_scout":
        return {
            "project_id": project_dir.name,
            "dataset_candidates_ref": _first_ref(refs, "dataset_match_report.csv"),
            "recommendations_ref": _first_ref(refs, "results/geo_discovery/geo_recommendations.json"),
            "candidate_count": int(summary.get("candidate_count", 0)) if isinstance(summary, dict) and str(summary.get("candidate_count", "0")).isdigit() else 0,
        }
    if role_id == "planner":
        return {
            "project_id": project_dir.name,
            "analysis_plan_ref": _first_ref(refs, "analysis_plan.json"),
            "work_orders_ref": _first_ref(refs, "v4/work_orders.json"),
            "module_count": int(summary.get("module_count", 0)) if isinstance(summary, dict) and str(summary.get("module_count", "0")).isdigit() else 0,
        }
    if role_id in REVIEWER_ROLES:
        return {
            "project_id": project_dir.name,
            "review_items": _review_items_from_packet(output_packet),
            "causal_grades_ref": _first_ref(refs, "results/causal_review.json") if role_id == "causal_reviewer" else "not_applicable",
            "decision": summary.get("decision", "needs_review") if isinstance(summary, dict) else "needs_review",
        }
    return {
        "project_id": project_dir.name,
        "report_ref": _first_ref(refs, "reports/target_report.html"),
        "structured_report_ref": _first_ref(refs, "reports/target_report_structured.json"),
        "evidence_refs": summary.get("evidence_refs", []) if isinstance(summary, dict) else [],
    }


def _review_items_from_packet(output_packet: dict[str, Any]) -> list[dict[str, Any]]:
    summary = output_packet.get("output_summary", {})
    if isinstance(summary, dict) and isinstance(summary.get("review_items"), list):
        return summary["review_items"]
    if output_packet.get("status") == "success":
        return [
            {
                "review_id": "review_" + content_hash(output_packet)[:12],
                "subject_role": output_packet.get("role_id", ""),
                "decision": "needs_review",
                "reason": "Role completed; explicit reviewer item required before approval.",
            }
        ]
    return []


def _approval_policy_errors(role_id: str, role_run: dict[str, Any], output_packet: dict[str, Any], normalized: dict[str, Any]) -> list[str]:
    errors = []
    approved_subjects = output_packet.get("approved_subjects", [])
    if role_id in GENERATOR_ROLES and approved_subjects:
        errors.append(f"{role_id}: generator role cannot approve outputs")
    if role_id in REVIEWER_ROLES and not normalized.get("review_items"):
        errors.append(f"{role_id}: reviewer role must write ReviewItem records")
    if role_id in REVIEWER_ROLES:
        for item in normalized.get("review_items", []):
            if item.get("subject_role") == role_id and item.get("decision") == "approve":
                errors.append(f"{role_id}: reviewer cannot approve its own output")
    if role_run.get("role_id") and role_run.get("role_id") != role_id:
        errors.append(f"{role_id}: role_run belongs to {role_run.get('role_id')}")
    return errors


def _first_ref(refs: set[str], expected: str) -> str:
    if expected in refs:
        return expected
    return next((ref for ref in refs if ref.endswith(expected)), "")


def _latest_by_role(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest = {}
    for row in runs:
        latest[row.get("role_id", "")] = row
    return latest


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _public_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in schema.items() if key != "properties"} | {"properties": schema.get("properties", {})}
