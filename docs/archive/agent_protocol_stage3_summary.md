# Agent Protocol Stage 3 Summary

## Scope

This stage added the v5 canonical agent communication protocol and JSON handoff contract. It does not implement LLM calls, does not connect real databases, and does not run real analysis.

## Files Added

- `targetcompass_lite/canonical/agent_protocol.py`
- `targetcompass_lite/canonical/agent_specs.py`
- `targetcompass_lite/canonical/handoff.py`
- `tests/test_agent_protocol.py`

## Agent Specs

The following seven agent specs are defined:

- `question_normalizer`
- `scope_resolver`
- `evidence_plan_builder`
- `resource_discovery_agent`
- `method_adapter_workorder_compiler`
- `result_auditor`
- `evidence_synthesizer_reporter`

Each spec includes:

- `agent_id`
- `display_name`
- `responsibility`
- `forbidden_actions`
- `input_schema_name`
- `output_schema_name`
- `allowed_tools`
- `required_input_refs`
- `required_output_refs`
- `max_claim_level`
- `handoff_contract`

## Handoff Contract

Handoffs are JSON objects using:

```json
{
  "schema_version": "v5.agent_handoff/0.1"
}
```

The implementation writes handoffs to:

- `project_dir/v5/handoffs.jsonl`

The file is append-only through `write_handoff`.

## Implemented Functions

- `build_agent_specs()`
- `validate_agent_handoff(handoff, from_agent, to_agent)`
- `write_handoff(project_dir, handoff)`
- `load_handoffs(project_dir)`
- `next_agent_for_stage(stage)`
- `enforce_claim_ceiling(previous_ceiling, next_ceiling)`

`build_handoff(...)` was also added as a small helper so tests and later stages can create valid JSON handoffs consistently.

## Guardrails Implemented

- Handoffs must contain all required fields.
- `blocking_issues` makes validation return `blocked`; downstream agents must not continue automatically.
- Claim ceiling cannot be loosened.
- Agent handoff must follow the declared upstream/downstream contract.
- Placeholder or unverified datasets cannot enter `DATASETS_LOCKED`.
- `method_adapter_workorder_compiler` cannot emit biological result objects.
- `result_auditor` cannot modify raw results.
- `evidence_synthesizer_reporter` can only consume audited evidence.

## Tests Run

```powershell
python -m unittest tests.test_agent_protocol -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state tests.test_agent_protocol -v
```

Result: passed, 22 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_agent_protocol.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This is consistent with the existing Prompt 0 and Prompt 2 full-suite behavior and is not a failure of the new Stage 3 tests.

## Compatibility Notes

- Existing v4 modules and entrypoints were not replaced.
- The new protocol is control-plane only.
- Agents exchange object refs, artifact refs, evidence refs, assumptions, open questions, blocking issues, audit notes, and claim ceiling metadata instead of free-text conclusions.
- No real API key, external network call, or real analysis execution was added in this stage.
