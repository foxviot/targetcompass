from __future__ import annotations

from typing import Any

from .agent_specs import build_agent_specs
from .schemas import CLAIM_LEVELS


HANDOFF_SCHEMA_VERSION = "v5.agent_handoff/0.1"

REQUIRED_HANDOFF_FIELDS = [
    "handoff_id",
    "schema_version",
    "project_id",
    "from_agent",
    "to_agent",
    "created_at",
    "input_object_refs",
    "output_object_refs",
    "evidence_refs",
    "artifact_refs",
    "assumptions",
    "open_questions",
    "blocking_issues",
    "claim_ceiling",
    "audit_notes",
    "payload_hash",
]


def enforce_claim_ceiling(previous_ceiling: str, next_ceiling: str) -> list[str]:
    if previous_ceiling not in CLAIM_LEVELS:
        return [f"previous ceiling is invalid: {previous_ceiling}"]
    if next_ceiling not in CLAIM_LEVELS:
        return [f"next ceiling is invalid: {next_ceiling}"]
    if CLAIM_LEVELS.index(next_ceiling) > CLAIM_LEVELS.index(previous_ceiling):
        return [f"claim ceiling cannot be loosened: {previous_ceiling} -> {next_ceiling}"]
    return []


def validate_agent_handoff(handoff: dict[str, Any], from_agent: str, to_agent: str) -> dict[str, Any]:
    errors = _validate_handoff_required_fields(handoff)
    warnings: list[str] = []
    if errors:
        return {"status": "invalid", "errors": errors, "warnings": warnings}

    specs = build_agent_specs()
    if from_agent not in specs:
        errors.append(f"unknown from_agent: {from_agent}")
    if to_agent not in specs:
        errors.append(f"unknown to_agent: {to_agent}")
    if handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        errors.append(f"schema_version must be {HANDOFF_SCHEMA_VERSION}")
    if handoff.get("from_agent") != from_agent:
        errors.append(f"handoff from_agent mismatch: expected {from_agent}")
    if handoff.get("to_agent") != to_agent:
        errors.append(f"handoff to_agent mismatch: expected {to_agent}")
    if errors:
        return {"status": "invalid", "errors": errors, "warnings": warnings}

    expected_to = specs[from_agent]["handoff_contract"].get("to_agent")
    if expected_to != to_agent:
        errors.append(f"{from_agent} cannot hand off to {to_agent}; expected {expected_to}")

    ceiling = handoff.get("claim_ceiling") or {}
    if not isinstance(ceiling, dict):
        errors.append("claim_ceiling: expected object")
        max_allowed = None
    else:
        max_allowed = ceiling.get("max_allowed_claim")
    if max_allowed not in CLAIM_LEVELS:
        errors.append("claim_ceiling.max_allowed_claim is invalid")
    elif CLAIM_LEVELS.index(max_allowed) > CLAIM_LEVELS.index(specs[from_agent]["max_claim_level"]):
        errors.append(f"{from_agent} cannot emit claim ceiling above {specs[from_agent]['max_claim_level']}")

    ref_fields = ["input_object_refs", "output_object_refs", "evidence_refs", "artifact_refs"]
    text_fields = ["assumptions", "open_questions", "blocking_issues", "audit_notes"]
    for field in ref_fields + text_fields:
        if not isinstance(handoff.get(field), list):
            errors.append(f"{field}: expected list")
    for field in ref_fields:
        if isinstance(handoff.get(field), list):
            for idx, ref in enumerate(handoff.get(field) or []):
                if not isinstance(ref, dict):
                    errors.append(f"{field}[{idx}]: expected object ref")

    errors.extend(_validate_dataset_locking(handoff))
    errors.extend(_validate_agent_specific_rules(handoff, from_agent))

    if errors:
        return {"status": "invalid", "errors": errors, "warnings": warnings}
    if handoff.get("blocking_issues"):
        return {"status": "blocked", "errors": [], "warnings": ["blocking_issues present; downstream agent must not continue automatically"]}
    return {"status": "ok", "errors": [], "warnings": warnings}


def _validate_handoff_required_fields(handoff: dict[str, Any]) -> list[str]:
    errors = []
    for field in REQUIRED_HANDOFF_FIELDS:
        if field not in handoff or handoff[field] is None:
            errors.append(f"{field}: missing required field")
    return errors


def _validate_dataset_locking(handoff: dict[str, Any]) -> list[str]:
    errors = []
    refs = list(handoff.get("output_object_refs") or []) + list(handoff.get("input_object_refs") or [])
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        target_stage = ref.get("target_stage") or ref.get("stage")
        object_type = str(ref.get("object_type", "")).lower()
        if target_stage == "DATASETS_LOCKED" or object_type in {"datasetselectiondecision", "dataset_lock"}:
            if ref.get("verified") is False or str(ref.get("source_status", "")).lower() in {"mock", "mock_placeholder", "placeholder", "unknown"}:
                errors.append("dataset candidate cannot enter DATASETS_LOCKED when verified=false or source is placeholder")
    return errors


def _validate_agent_specific_rules(handoff: dict[str, Any], from_agent: str) -> list[str]:
    errors = []
    output_refs = handoff.get("output_object_refs") or []
    if from_agent == "method_adapter_workorder_compiler":
        for ref in output_refs:
            if isinstance(ref, dict) and str(ref.get("object_type", "")).lower() in {"claim", "evidenceitem", "biologicalresult"}:
                errors.append("method_adapter_workorder_compiler cannot generate biological results")
    if from_agent == "result_auditor":
        for ref in output_refs:
            if isinstance(ref, dict) and ref.get("modifies_raw_result") is True:
                errors.append("result_auditor cannot modify raw results")
    if from_agent == "evidence_synthesizer_reporter":
        unaudited = [ref for ref in handoff.get("evidence_refs", []) if isinstance(ref, dict) and ref.get("audit_status") != "audited"]
        if unaudited:
            errors.append("evidence_synthesizer_reporter can only consume audited evidence")
    return errors
