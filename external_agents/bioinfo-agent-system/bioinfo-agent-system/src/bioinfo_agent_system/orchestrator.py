from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from .claim_audit import audit_project_claims
from .cross_agent_rules import run_cross_agent_validation
from .io_utils import ensure_dir, utc_now, write_json
from .mock_agents import build_mock_output_bundle
from .registry import PROJECT_ROOT, get_agent_record, get_agent_records
from .state import ResearchProjectState
from .validators import ValidationError, validate_data_against_schema, validate_schema_file


def run_pipeline(raw_user_question: str, output_dir: str) -> ResearchProjectState:
    created_at = utc_now()
    run_id = _build_run_id()
    output_root = Path(output_dir)
    run_dir = output_root / "mock_run" / run_id
    ensure_dir(run_dir / "agent_outputs")
    ensure_dir(run_dir / "handoffs")

    handoff_schema = validate_schema_file(
        PROJECT_ROOT / "schemas" / "shared" / "agent_handoff.schema.json"
    )
    project_state_schema = validate_schema_file(
        PROJECT_ROOT / "schemas" / "shared" / "project_state.schema.json"
    )

    state = ResearchProjectState(run_id=run_id, raw_user_question=raw_user_question.strip())
    output_bundle = build_mock_output_bundle(raw_user_question, created_at)

    for record in get_agent_records():
        output = output_bundle[record.agent_id]
        output_schema = validate_schema_file(record.output_schema_path)
        validate_data_against_schema(output, output_schema, record.agent_id)

        handoff = _build_handoff(record, state, output, created_at)
        validate_data_against_schema(handoff, handoff_schema, f"{record.agent_id}.handoff")

        state.set_agent_output(record, output)
        state.add_handoff(handoff)

        write_json(run_dir / "agent_outputs" / record.output_filename, output)
        write_json(
            run_dir / "handoffs" / f"{record.agent_id}.handoff.json",
            handoff,
        )

    state.current_step = "validation"
    pre_validation_state = state.to_dict()
    validate_data_against_schema(
        pre_validation_state, project_state_schema, "project_state.pre_validation"
    )

    audit_entries = []
    audit_entries.extend(run_cross_agent_validation(pre_validation_state))
    audit_entries.extend(audit_project_claims(pre_validation_state))
    state.add_audit_entries(audit_entries)
    state.current_step = "completed"

    final_state = state.to_dict()
    validate_data_against_schema(final_state, project_state_schema, "project_state")

    validation_report = {
        "run_id": run_id,
        "status": "passed",
        "generated_at": created_at,
        "output_dir": str(run_dir),
        "checks": {
            "schemas": "passed",
            "agent_outputs": "passed",
            "handoffs": "passed",
            "project_state": "passed",
            "cross_agent_rules": "passed",
            "claim_audit": "passed",
        },
    }
    write_json(run_dir / "project_state.json", final_state)
    write_json(run_dir / "validation_report.json", validation_report)
    return state


def _build_handoff(
    record: Any,
    state: ResearchProjectState,
    output: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    input_refs = ["raw_user_question"]
    if record.step_index > 1:
        previous_record = get_agent_records()[record.step_index - 2]
        input_refs = [previous_record.agent_id]
    return {
        "run_id": state.run_id,
        "agent_id": record.agent_id,
        "agent_name": record.agent_name,
        "step_index": record.step_index,
        "input_refs": input_refs,
        "output_schema": str(record.output_schema_path.relative_to(PROJECT_ROOT)),
        "output": output,
        "warnings": list(output["warnings"]),
        "blocking_failures": list(output["blocking_failures"]),
        "claim_ceiling": dict(output["claim_ceiling"]),
        "provenance": list(output["provenance"]),
        "created_at": created_at,
    }


def _build_run_id() -> str:
    return f"mock-run-{utc_now().replace(':', '').replace('-', '')}-{uuid4().hex[:8]}"
