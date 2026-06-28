# Canonical State Stage 2 Summary

## Scope

This stage added a v5 canonical `ProjectState` and append-only `EventLog` layer. It does not replace the existing v4 orchestrator, v4 project output logic, or default CLI behavior.

## Files Added

- `targetcompass_lite/canonical/state.py`
- `targetcompass_lite/canonical/events.py`
- `targetcompass_lite/canonical/store.py`
- `tests/test_canonical_state.py`

## Output Locations

The new state layer writes only under:

- `project_dir/v5/project_state.json`
- `project_dir/v5/events.jsonl`

It does not write to v4 output paths.

## Fixed Stages

The canonical state machine defines these stages:

- `INTAKE`
- `QUESTION_RESOLVED`
- `SCOPE_RESOLVED`
- `EVIDENCE_PLANNED`
- `RESOURCES_DISCOVERED`
- `DATASETS_LOCKED`
- `WORKFLOW_COMPILED`
- `TASKS_READY`
- `TASKS_RUNNING`
- `QC_COMPLETED`
- `EVIDENCE_SYNTHESIZED`
- `ALIGNMENT_AUDITED`
- `REPORT_READY`
- `HUMAN_REVIEW_REQUIRED`
- `FAILED`
- `CANCELLED`

## Store API Implemented

- `init_project_state(project_dir, user_question)`
- `append_event(project_dir, event)`
- `transition_state(project_dir, next_stage, event_type, actor, object_refs, message)`
- `load_project_state(project_dir)`
- `load_events(project_dir)`
- `validate_transition(previous, next)`

`transition_state` also supports an explicit `resume=True` flag for future recovery flows from terminal states.

## Guardrails Implemented

- `INTAKE -> REPORT_READY` and other skipped transitions are rejected.
- `FAILED` and `CANCELLED` are terminal unless `resume=True` is explicitly passed.
- `TASKS_RUNNING -> EVIDENCE_SYNTHESIZED` is rejected unless a `QC_COMPLETED` event already exists.
- `events.jsonl` is append-only through `append_event`.

## Tests Run

```powershell
python -m unittest tests.test_canonical_state -v
```

Result: passed, 6 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state -v
```

Result: passed, 14 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_canonical_state.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This matches the Prompt 0 full-suite behavior and is recorded as an existing full-suite/runtime issue, not a failure of the new canonical state tests.

## Known Limitations

- Concurrent event writes are not locked yet. A future stage should add a file lock or move the event stream into a transactional backend.
- The state machine records lifecycle transitions only. It does not drive real analysis modules yet.
- The v4 orchestrator remains the active demo path until a later adapter stage deliberately connects canonical state to execution.
