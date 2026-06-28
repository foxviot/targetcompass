from __future__ import annotations

from typing import Any

from .claim_audit import assert_not_exceed
from .validators import ValidationError


def run_cross_agent_validation(project_state: dict[str, Any]) -> list[dict[str, str]]:
    outputs = project_state["agent_outputs"]
    evidence_output = outputs["evidence_dataset_scout"]
    method_output = outputs["method_extraction"]
    motif_output = outputs["method_motif_feasibility"]
    plan_output = outputs["executable_research_plan"]

    audit_entries: list[dict[str, str]] = []

    dataset_map = {
        dataset["dataset_id"]: dataset for dataset in evidence_output["dataset_candidates"]
    }
    for selected_dataset in plan_output["selected_datasets"]:
        if selected_dataset not in dataset_map:
            raise ValidationError(
                f"cross_agent_rules: selected dataset {selected_dataset!r} not found in Agent 3"
            )
        if dataset_map[selected_dataset]["recommendation"] == "reject":
            raise ValidationError(
                f"cross_agent_rules: rejected dataset {selected_dataset!r} selected by Agent 6"
            )
    audit_entries.append(
        {
            "event": "cross_agent_rules:datasets",
            "status": "ok",
            "detail": "Agent 6 datasets are present in Agent 3 and none are rejected.",
        }
    )

    contract_map = {
        contract["contract_id"]: contract for contract in motif_output["local_method_contracts"]
    }
    ready_contracts = set(motif_output["ready_contracts"])
    blocked_contracts = set(motif_output["blocked_contracts"])
    for selected_contract in plan_output["selected_method_contracts"]:
        if selected_contract not in contract_map:
            raise ValidationError(
                f"cross_agent_rules: selected contract {selected_contract!r} not found in Agent 5"
            )
        if selected_contract in blocked_contracts:
            raise ValidationError(
                f"cross_agent_rules: blocked contract {selected_contract!r} selected by Agent 6"
            )
        if contract_map[selected_contract]["status"] != "ready" or selected_contract not in ready_contracts:
            raise ValidationError(
                f"cross_agent_rules: contract {selected_contract!r} is not ready"
            )
    audit_entries.append(
        {
            "event": "cross_agent_rules:contracts",
            "status": "ok",
            "detail": "Agent 6 contracts are present in Agent 5 and all are ready.",
        }
    )

    missing_step_ids: set[str] = set()
    for extraction in method_output["study_method_extractions"]:
        for step in extraction["method_steps"]:
            if step["source_status"] not in {"extracted", "inferred", "missing"}:
                raise ValidationError(
                    f"cross_agent_rules: invalid method step status {step['source_status']!r}"
                )
            if step["source_status"] == "missing":
                missing_step_ids.add(step["step_id"])
    for ready_contract in ready_contracts:
        lowered_contract = ready_contract.lower()
        if any(missing_step.lower() in lowered_contract for missing_step in missing_step_ids):
            raise ValidationError(
                f"cross_agent_rules: missing method step leaked into ready contract {ready_contract!r}"
            )
    audit_entries.append(
        {
            "event": "cross_agent_rules:method_steps",
            "status": "ok",
            "detail": "Missing method steps did not become ready contracts.",
        }
    )

    final_claim = plan_output["claim_ceiling"]["max_allowed_claim"]
    for upstream_field in [
        "question_normalization",
        "evidence_dataset_scout",
        "method_motif_feasibility",
    ]:
        upstream_claim = outputs[upstream_field]["claim_ceiling"]["max_allowed_claim"]
        assert_not_exceed(final_claim, upstream_claim, f"cross_agent_rules:{upstream_field}")
    audit_entries.append(
        {
            "event": "cross_agent_rules:claim_ceiling",
            "status": "ok",
            "detail": "Agent 6 claim ceiling does not exceed upstream ceilings.",
        }
    )

    return audit_entries
