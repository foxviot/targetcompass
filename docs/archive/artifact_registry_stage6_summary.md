# Artifact Registry Stage 6 Summary

## Scope

This stage added a v5 Artifact Registry and stricter artifact manifest validation. It does not replace the old `work_order_dag` artifact reference behavior.

## Files Added

- `targetcompass_lite/canonical/artifacts.py`
- `tests/test_canonical_artifacts.py`

## Registry Output

Artifact manifests are appended to:

- `project_dir/v5/artifact_registry.jsonl`

## Manifest Fields

The v5 artifact manifest includes:

- `artifact_id`
- `project_id`
- `path`
- `artifact_type`
- `producer_agent_or_task`
- `producer_run_id`
- `created_at`
- `checksum_sha256`
- `size_bytes`
- `exists`
- `schema_name`
- `row_count` when available
- `column_names` when available
- `expected_by_task_ids`
- `supports_subquestion_ids`
- `evidence_item_refs`
- `qc_status`
- `limitations`
- `is_placeholder`

## Implemented Functions

- `compute_file_sha256(path)`
- `build_artifact_manifest(project_dir, relative_path, producer, artifact_type, expected_by_task_ids, supports_subquestion_ids)`
- `write_artifact_manifest(project_dir, manifest)`
- `load_artifact_registry(project_dir)`
- `register_artifact(project_dir, relative_path, ...)`
- `validate_artifact_for_evidence(manifest)`

## Guardrails Implemented

- `exists=false` artifacts cannot enter evidence synthesis.
- `is_placeholder=true` artifacts cannot enter evidence synthesis.
- Missing checksums cannot support evidence.
- Failed/rejected QC artifacts cannot support evidence.
- `artifact_id` includes checksum or placeholder status, not just path and existence.
- SHA256 checksum is computed with streaming reads.
- CSV/TSV profiling records `column_names` and optional `row_count` without loading the whole file into memory.

## Tests Run

```powershell
python -m unittest tests.test_canonical_artifacts -v
```

Result: passed, 7 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state tests.test_agent_protocol tests.test_canonical_mock_runner tests.test_external_agent_import tests.test_canonical_artifacts -v
```

Result: passed, 43 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_canonical_artifacts.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This is consistent with prior stages and is not a failure of the new Stage 6 tests.

## Compatibility Notes

- Existing v4 `work_order_dag` behavior was not modified.
- Existing v4 execution outputs are not rewritten.
- The registry is a new v5 control-plane layer for artifact trust and evidence readiness.
