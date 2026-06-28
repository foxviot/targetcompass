# Canonical Mock Runner Stage 4 Summary

## Scope

This stage added a v5 mock orchestration runner that moves one natural-language question through the canonical agent handoff chain and stops at task packets.

It does not call an LLM, does not connect real databases, does not run real analysis, and does not write v4 project outputs.

## Files Added

- `targetcompass_lite/canonical/mock_runner.py`
- `targetcompass_lite/canonical/workflow_compiler.py`
- `targetcompass_lite/canonical/task_packets.py`
- `tests/test_canonical_mock_runner.py`

## Files Updated

- `targetcompass_lite/canonical/state.py`

The only state-machine change was allowing `RESOURCES_DISCOVERED -> WORKFLOW_COMPILED`. This is required for the mock and pre-approval path where datasets are discovered but not verified or locked. The runner still stops at `TASKS_READY` and does not enter `TASKS_RUNNING` or `REPORT_READY`.

## Implemented API

```python
run_mock_canonical_pipeline(project_dir, user_question)
```

## Pipeline Behavior

The mock runner performs:

1. Initialize project state at `INTAKE`.
2. `question_normalizer` writes `ResearchSpec` and `SubQuestion`.
3. `scope_resolver` writes `ScopeBundle`.
4. `evidence_plan_builder` writes `EvidencePlan`.
5. `resource_discovery_agent` writes `ResourceCandidate[]`.
6. All resource candidates are marked:
   - `verified=false`
   - `source_status="mock_placeholder"`
7. `method_adapter_workorder_compiler` writes:
   - `WorkflowPlan`
   - `AnalysisTaskPacket[]`
   - `ReviewTaskPacket[]`
8. Final state is `TASKS_READY`.

## Output Locations

The runner writes only under:

- `project_dir/v5/project_state.json`
- `project_dir/v5/events.jsonl`
- `project_dir/v5/objects/*.json`
- `project_dir/v5/handoffs/*.json`

It does not create `project_dir/v4`, `project_dir/results`, or other legacy execution outputs.

## Task Packet Rules

`AnalysisTaskPacket` includes:

- `subquestion_id`
- `expected_inputs`
- `expected_outputs`
- `qc_requirements`
- `failure_conditions`

It does not contain code-change instructions.

`ReviewTaskPacket` includes:

- `audit_scope`
- `claim_ceiling`
- `required_checks`

`EngineeringTaskPacket` support is validated in `task_packets.py`, but the mock runner does not emit engineering packets by default.

## Guardrails Verified

- No verified dataset is produced by mock resource discovery.
- State does not advance beyond `TASKS_READY`.
- Handoff chain is complete through `method_adapter_workorder_compiler -> result_auditor`.
- Claim ceiling is not loosened.
- Events cover all agent steps.
- Old v4 directories are not written.

## Tests Run

```powershell
python -m unittest tests.test_canonical_mock_runner -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_canonical_state tests.test_agent_protocol tests.test_canonical_mock_runner -v
```

Result: passed, 22 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state tests.test_agent_protocol tests.test_canonical_mock_runner -v
```

Result: passed, 30 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_canonical_mock_runner.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This matches the existing full-suite timeout seen in prior stages and is not a failure of the new Stage 4 tests.

## Compatibility Notes

- Existing v4 modules and CLI behavior were not replaced.
- No CLI subcommand was added in this stage because the Python API and tests are sufficient and avoid risking old CLI behavior.
- The mock runner demonstrates vertical control-plane closure only. It must not be presented as real biological analysis.
