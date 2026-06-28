# Canonical Schema Stage 1 Summary

## Scope

This stage added the v5 canonical schema layer without replacing the existing v4 agent, orchestrator, orchestration graph, external agent adapter, or CLI behavior.

## Files Added

- `targetcompass_lite/canonical/ids.py`
- `targetcompass_lite/canonical/schemas.py`
- `targetcompass_lite/canonical/validation.py`
- `tests/test_canonical_schemas.py`

## Existing Files Intentionally Not Changed

- `targetcompass_lite/agent.py`
- `targetcompass_lite/orchestrator.py`
- `targetcompass_lite/orchestration_graph.py`
- `targetcompass_lite/role_execution_dispatcher.py`
- `targetcompass_lite/external_agent_adapter.py`
- existing CLI command behavior

## Implemented Objects

The canonical schema layer defines the required control-plane objects:

- `ResearchSpec`
- `SubQuestion`
- `ScopeBundle`
- `EvidencePlan`
- `ResourceCandidate`
- `DatasetProfile`
- `DatasetSelectionDecision`
- `MethodContractRef`
- `CompatibilityDecision`
- `WorkflowPlan`
- `AnalysisTaskPacket`
- `EngineeringTaskPacket`
- `ReviewTaskPacket`
- `TaskRun`
- `ArtifactManifest`
- `QCReport`
- `EvidenceItemRef`
- `Claim`
- `QuestionAlignmentReport`
- `FinalReportManifest`
- `ProjectEvent`
- `ProjectState`

## Validation Implemented

- required field validation
- enum validation
- placeholder or unknown resource guard for verified datasets
- claim ceiling validation
- artifact manifest validation
- project state validation

## Stable ID Contract

`ids.py` implements deterministic stable ID generation through sorted JSON hashing. Stable IDs do not depend on current time.

## Tests Run

```powershell
python -m unittest tests.test_canonical_schemas -v
```

Result: passed, 6 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas -v
```

Result: passed, 8 tests.

## Full Test Status

During Prompt 0, the full command below was run and timed out after 240 seconds:

```powershell
python -m unittest discover tests -v
```

This is recorded as an existing full-suite/runtime issue, not a failure of the new canonical schema tests.

## Compatibility Notes

- The new files are additive.
- No legacy v4 entrypoint was replaced.
- No mock dataset is marked as verified by the new validation layer.
- Claim validation can prevent association-level evidence from being promoted into causal claims.
