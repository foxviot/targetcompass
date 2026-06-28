# Codex Worker Protocol Stage 8 Summary

## Scope

This stage added a v5 controlled Codex Worker protocol for task packet export, approval, claim, release, completion, and failure state tracking.

It does not call Codex, does not run subprocesses, and does not execute external commands.

## Files Added

- `targetcompass_lite/canonical/codex_worker_protocol.py`
- `tests/test_codex_worker_protocol.py`

## Queue Directories

Task records are stored under:

- `project_dir/v5/codex/pending/`
- `project_dir/v5/codex/approved/`
- `project_dir/v5/codex/claimed/`
- `project_dir/v5/codex/completed/`
- `project_dir/v5/codex/failed/`

## Implemented Functions

- `export_task_packet(project_dir, packet)`
- `request_task_approval(project_dir, task_id)`
- `approve_task(project_dir, task_id, actor)`
- `reject_task(project_dir, task_id, actor, reason)`
- `claim_task(project_dir, worker_id, task_id="")`
- `release_task(project_dir, task_id, worker_id, reason)`
- `complete_task(project_dir, task_id, worker_id, output_manifest)`
- `fail_task(project_dir, task_id, worker_id, failure_reason)`
- `load_worker_queue(project_dir)`

## State Rules

Supported task statuses:

- `draft`
- `pending_approval`
- `approved`
- `claimed`
- `running`
- `completed`
- `failed`
- `released`
- `expired`
- `cancelled`

Implemented guardrails:

- exported tasks start as `pending_approval`
- pending tasks cannot be claimed
- only approved tasks can be claimed
- claimed tasks record `worker_id`, `claimed_at`, and `lease_expires_at`
- default lease is 30 minutes
- expired claimed tasks can be reclaimed
- worker mismatch blocks `complete_task` and `fail_task`
- `complete_task` requires a non-empty `output_manifest`
- no command execution is performed

## Engineering Task Safety

`EngineeringTaskPacket` must include `allowed_paths`.

It must also include these forbidden paths:

- `.git/`
- `secrets`
- `.env`
- `raw_data/`
- `external_agent_runs/*/mock_run/`

Missing required forbidden paths causes export rejection.

## Tests Run

```powershell
python -m unittest tests.test_codex_worker_protocol -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state tests.test_agent_protocol tests.test_canonical_mock_runner tests.test_external_agent_import tests.test_canonical_artifacts tests.test_question_alignment_auditor tests.test_codex_worker_protocol -v
```

Result: passed, 59 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_codex_worker_protocol.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This is consistent with prior stages and is not a failure of the new Stage 8 tests.

## Compatibility Notes

- Existing v4 Codex engineering queue behavior was not changed.
- This is a v5 protocol layer only.
- Future stages can attach a real worker/executor after approval and sandbox rules are reviewed.
