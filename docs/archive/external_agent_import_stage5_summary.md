# External Agent Import Stage 5 Summary

## Scope

This stage imports the six external `bioinfo-agent-system` agent contracts as v5 reference resources only.

It does not run the external mock pipeline, does not copy external mock outputs into project evidence, does not import `AUTO_*` datasets as verified, and does not overwrite canonical v5 agent specs.

## Files Added

- `targetcompass_lite/canonical/external_agent_import.py`
- `tests/test_external_agent_import.py`

## Implemented Functions

- `discover_external_agent_specs(agent_root)`
- `import_external_agent_contracts(project_dir, agent_root)`
- `map_external_agent_to_v5_agent(external_agent_id)`
- `validate_external_agent_contract_import(import_result)`

## Output

The import manifest is written to:

- `project_dir/v5/imported_external_agents.json`

The manifest is marked:

- `import_mode="contract_reference_only"`
- `reference_only=true`
- `imported_as_evidence=false`
- `external_mock_runtime_called=false`
- `mock_outputs_imported=false`
- `canonical_specs_overwritten=false`

## Mapping

External to v5 mapping:

- `01_scientific_question_normalizer` -> `question_normalizer`
- `02_scope_ontology_resolver` -> `scope_resolver`
- `03_evidence_dataset_scout` -> `resource_discovery_agent`
- `04_method_extraction_agent` -> `evidence_plan_builder`
- `05_method_motif_feasibility_synthesizer` -> `method_adapter_workorder_compiler`
- `06_research_plan_compiler` -> `method_adapter_workorder_compiler`

### Agent 04 Mapping Rationale

`04_method_extraction_agent` overlaps both v5 `evidence_plan_builder` and `method_adapter_workorder_compiler`.

For this stage it is mapped to `evidence_plan_builder` because its main value as a reference contract is extracting and structuring method requirements from scientific context. Actual WorkOrder/task compilation remains owned by `method_adapter_workorder_compiler`, which is better represented by external agents 05 and 06.

## Explicitly Excluded

The import records forbidden paths but does not consume them:

- `scripts/run_mock_pipeline.py`
- `outputs/mock_run`

External mock run files are not imported as evidence or project results.

## Tests Run

```powershell
python -m unittest tests.test_external_agent_import -v
```

Result: passed, 6 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state tests.test_agent_protocol tests.test_canonical_mock_runner tests.test_external_agent_import -v
```

Result: passed, 36 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_external_agent_import.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This is consistent with the existing full-suite timeout from earlier stages and is not a failure of the new Stage 5 tests.

## Compatibility Notes

- Existing v4 runtime behavior was not changed.
- Canonical v5 agent specs remain the source of truth.
- External agent contracts are imported only as `imported_reference`.
- No external mock runtime is invoked.
