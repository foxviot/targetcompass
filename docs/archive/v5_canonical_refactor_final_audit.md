# v5 Canonical Refactor Final Audit

## Summary

The v5 canonical refactor stages were implemented as an additive control-plane layer. The new code lives under `targetcompass_lite/canonical/`, with tests under `tests/` and documentation under `docs/`.

The implemented v5 scope includes:

- canonical schemas and stable IDs
- ProjectState and append-only EventLog
- AgentSpec and JSON handoff protocol
- mock orchestration runner that stops at task packets
- external six-agent contract import as reference-only material
- Artifact Registry with checksum and placeholder/QC checks
- Question Alignment Auditor
- controlled Codex Worker protocol with approval and lease gates
- architecture and migration documentation

No real LLM calls, real database discovery, real subprocess execution, or real Codex execution were added in these v5 stages.

## Files changed

New v5 modules were added under:

- `targetcompass_lite/canonical/`

New v5 tests were added under:

- `tests/test_canonical_schemas.py`
- `tests/test_canonical_state.py`
- `tests/test_agent_protocol.py`
- `tests/test_canonical_mock_runner.py`
- `tests/test_external_agent_import.py`
- `tests/test_canonical_artifacts.py`
- `tests/test_question_alignment_auditor.py`
- `tests/test_codex_worker_protocol.py`
- existing `tests/test_canonical_memory_palace.py` from the PilotDeck memory stage

New documentation was added under:

- `docs/canonical_agent_refactor_audit.md`
- `docs/canonical_schema_stage1_summary.md`
- `docs/canonical_state_stage2_summary.md`
- `docs/agent_protocol_stage3_summary.md`
- `docs/canonical_mock_runner_stage4_summary.md`
- `docs/external_agent_import_stage5_summary.md`
- `docs/artifact_registry_stage6_summary.md`
- `docs/question_alignment_stage7_summary.md`
- `docs/codex_worker_protocol_stage8_summary.md`
- `docs/canonical_agent_architecture_v5.md`
- `docs/v4_to_v5_migration_plan.md`
- `docs/agent_communication_contract.md`
- `docs/v5_documentation_stage9_summary.md`
- `docs/v5_canonical_refactor_final_audit.md`

## New modules

New canonical modules:

- `ids.py`
- `schemas.py`
- `validation.py`
- `state.py`
- `events.py`
- `store.py`
- `agent_specs.py`
- `agent_protocol.py`
- `handoff.py`
- `mock_runner.py`
- `workflow_compiler.py`
- `task_packets.py`
- `external_agent_import.py`
- `artifacts.py`
- `alignment_auditor.py`
- `codex_worker_protocol.py`
- `memory_palace.py`

These modules write v5 project outputs under `project_dir/v5/`.

## Old behavior compatibility

Prompt 10 checked these old core entrypoints:

- `targetcompass_lite/agent.py`
- `targetcompass_lite/orchestrator.py`
- `targetcompass_lite/orchestration_graph.py`
- `targetcompass_lite/external_agent_adapter.py`

Observed working-tree state:

- `targetcompass_lite/agent.py` was not shown as modified in the targeted git status check.
- `targetcompass_lite/orchestration_graph.py` is modified in the existing dirty worktree. Its diff is v4 typed orchestration work and was not introduced by the v5 canonical prompt stages.
- `targetcompass_lite/orchestrator.py` and `targetcompass_lite/external_agent_adapter.py` appear as untracked in the current git status, although Prompt 0 confirmed they exist in the working tree. These are part of the pre-existing v4 development state, not files changed by the final audit.

The v5 canonical stages did not replace old v4 entrypoints, did not change default CLI behavior, and did not delete old demo outputs.

## Test results

The required Prompt 10 test commands were run.

```powershell
python -m unittest tests.test_canonical_schemas -v
```

Result: passed, 6 tests.

```powershell
python -m unittest tests.test_canonical_state -v
```

Result: passed, 6 tests.

```powershell
python -m unittest tests.test_agent_protocol -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_canonical_mock_runner -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_external_agent_import -v
```

Result: passed, 6 tests.

```powershell
python -m unittest tests.test_canonical_artifacts -v
```

Result: passed, 7 tests.

```powershell
python -m unittest tests.test_question_alignment_auditor -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_codex_worker_protocol -v
```

Result: passed, 8 tests.

Total required v5 test count from the listed commands: 57 passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds.

## Known failures

The only observed failure is the full-suite timeout:

- Command: `python -m unittest discover tests -v`
- Result: timeout after approximately 240 seconds
- Repeated across multiple v5 stages
- No failure traceback was produced before timeout

Recommended fix:

1. Split tests into `quick`, `full`, and `e2e` suites.
2. Mark real-network, GEO, Nextflow, Docker, and long-running tests separately.
3. Keep v5 canonical contract tests in a fast quick suite.
4. Run full/e2e suites in CI with longer timeout and dependency setup.

## Security review

No v5 canonical module introduced:

- API keys
- embedded real credentials
- OpenAI/DeepSeek calls
- real network calls
- `subprocess`
- shell command execution
- automatic Codex execution

The Codex Worker protocol is state-only. It exports, approves, claims, releases, completes, and fails task records, but it does not run tasks.

Engineering task packets require forbidden paths including:

- `.git/`
- `secrets`
- `.env`
- `raw_data/`
- `external_agent_runs/*/mock_run/`

## Hidden side effects review

v5 project outputs are isolated under:

```text
project_dir/v5/
```

The mock runner test verifies it does not create:

- `project_dir/v4`
- `project_dir/results`

Tests use temporary directories for generated outputs. No persistent large temporary output was intentionally generated by the v5 canonical tests.

The external six-agent package is imported only as reference contracts. The external mock runtime and `outputs/mock_run` are explicitly excluded.

## Performance review

Positive:

- v5 canonical tests are small and run quickly.
- Artifact checksum uses streaming reads.
- CSV/TSV profiling records headers and row counts without loading the whole table into memory.

Risk:

- Full `unittest discover` exceeds the 240 second tool timeout in this environment.
- Large real matrices still need production-grade profiling limits and background execution policies before real database integration.

## Scientific validity review

The v5 canonical layer adds scientific safety controls:

- placeholder datasets cannot be marked verified by canonical validation
- mock runner resource candidates are `verified=false` and `source_status="mock_placeholder"`
- claim ceilings prevent association evidence from becoming causal claims
- Artifact Registry rejects missing, placeholder, failed-QC, or checksumless artifacts for evidence use
- Question Alignment Auditor detects:
  - unsupported claims
  - scope drift
  - claim ceiling violations
  - placeholder artifact support
  - failed QC evidence
  - omitted negative or failed evidence
- Codex Worker tasks require approval before claim

Boundary:

v5 does not yet prove real scientific discovery. It proves the control-plane contracts and guardrails required before real database and real execution integration.

## Remaining work before real database integration

- Add v5 adapters for real GEO/GSE, PubMed/PMC, HPA, Open Targets, DisGeNET, GWAS Catalog, Reactome/MSigDB, and cell-type reference sources.
- Enforce real dataset verification before `DATASETS_LOCKED`.
- Convert v5 `ResourceCandidate` and `DatasetProfile` into real v4/v5 dataset import flows.
- Register real downloaded/parsed artifacts in Artifact Registry.
- Attach real QC reports and evidence items to artifacts.
- Run Question Alignment Auditor before report generation.
- Add quick/full/e2e test tiers for network and large-data workflows.

## Remaining work before real Codex execution

- Add an isolated worker runtime.
- Add patch/test/result registry integration.
- Require human approval before executing EngineeringTaskPacket.
- Enforce allowed/forbidden path policies at execution time.
- Add lease renewal and stale worker recovery.
- Add signed output manifests and artifact registration after worker completion.
- Add merge approval for engineering changes.
- Keep subprocess execution out of the protocol layer.

## Recommendation: merge / hold / revise

Recommendation: merge the v5 canonical control-plane changes as an isolated foundation, but hold real database integration and real Codex execution for later prompts.

Reasoning:

- Required v5 contract tests pass individually.
- v5 code is additive and isolated under canonical modules.
- Scientific guardrails are explicit.
- Mock and external pipelines are clearly marked as non-production.
- Full-suite timeout remains unresolved and should be handled by test-suite split before production release.
