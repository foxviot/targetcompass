# Canonical Agent Refactor Audit

Generated: 2026-06-23

## Repository Identity

- Current working directory: `C:\Users\ASUS\Documents\target`
- Top-level entrypoint exists: `tc_lite.py`
- Main package exists: `targetcompass_lite/`
- Test directory exists: `tests/`
- External reference package exists: `external_agents/bioinfo-agent-system/`
- Git repository exists, but the worktree is already dirty with many v4/demo/generated changes. Future changes must avoid reverting unrelated files.

Required file existence check:

| Path | Exists | Notes |
|---|---:|---|
| `tc_lite.py` | yes | Thin CLI entrypoint. |
| `targetcompass_lite/agent.py` | yes | v4 application-owned workflow agent. |
| `targetcompass_lite/orchestrator.py` | yes | v4 run/attempt orchestrator. |
| `targetcompass_lite/orchestration_graph.py` | yes | v4 typed role graph. |
| `targetcompass_lite/role_execution_dispatcher.py` | yes | v4 local/LLM role execution dispatcher. |
| `targetcompass_lite/codex_task_queue.py` | yes | v4 Codex queue and execution bridge. |
| `targetcompass_lite/work_order_dag.py` | yes | v4 WorkOrder DAG/index. |
| `targetcompass_lite/external_agent_adapter.py` | yes | Adapter for external bioinfo-agent-system. |
| `targetcompass_lite/evidence_db.py` | yes | SQLite Evidence DB import/query/snapshot layer. |
| `targetcompass_lite/qc.py` | yes | Task QC report writer/index. |
| `external_agents/bioinfo-agent-system/bioinfo-agent-system/README.md` | yes | External six-agent mock pipeline documentation. |
| `external_agents/bioinfo-agent-system/bioinfo-agent-system/src/bioinfo_agent_system/orchestrator.py` | yes | External mock pipeline orchestrator module. |

## Existing Agent Systems

There are multiple agent/control-plane layers already present:

- `targetcompass_lite/agent.py` implements a six-stage v4 application agent: generation, initial review, verification, execution, final review, and report. It directly calls v4 modules such as screening, matching, planning, DEG, enrichment, evidence import, scoring, and reporting.
- `targetcompass_lite/orchestration_graph.py` implements a typed role graph over v4 roles such as disease normalizer, dataset scout, planner, method reviewer, result reviewer, causal reviewer, and report writer. It validates role output packets with local schemas and enforces reviewer-role review items.
- `targetcompass_lite/role_execution_dispatcher.py` chooses local or LLM execution for a role. It can fall back to local execution when LLM auto mode fails. A `codex` backend is explicitly reserved and not used for direct role execution.
- `targetcompass_lite/orchestrator.py` records v4 orchestrator runs, idempotency keys, cancellation/resume requests, partial reruns, and WorkOrder DAG execution attempts.
- `external_agents/bioinfo-agent-system` is a separate deterministic six-agent mock planning pipeline. Its README explicitly says it is not a real PubMed/GEO/cellxgene/OpenAI integrated system and not a source of verified biological conclusions.

Risk: these layers overlap. v5 should not add another implicit state machine without clear directory isolation and migration boundaries.

## Reusable Components

The following v4 components should be reused through adapters instead of rewritten:

- `targetcompass_lite/deg.py`, `scrna.py`, `sasp_score.py`, `enrichment.py`, `meta_analysis.py`: analysis modules.
- `targetcompass_lite/geo_discovery.py`, `geo_importer.py`, `geo_raw.py`: dataset discovery/import and recovery.
- `targetcompass_lite/evidence_planning.py`: EvidencePlan, DatasetProfile, MethodContract, and CompatibilityDecision objects already exist in v4 form.
- `targetcompass_lite/evidence_db.py`: Evidence DB import, migration, snapshot, query, and QC gate behavior.
- `targetcompass_lite/qc.py`, `qc_review.py`: four-layer QC and human review gate.
- `targetcompass_lite/work_order_dag.py`: v4 DAG view of WorkOrders, IO resolution, and evidence writes.
- `targetcompass_lite/codex_task_queue.py`: v4 queue with claim/release/execute/result registries.
- `targetcompass_lite/review.py`: approval records, version snapshots, final signoff gate, and review queue.
- `targetcompass_lite/mcp_gateway.py`, `mcp_server.py`, `mcp_http_server.py`: local MCP/gateway contracts and audit.
- `external_agents/bioinfo-agent-system/**`: useful schemas, forbidden inference rules, claim ceiling taxonomy, eval ideas, and agent contracts as reference material only.

## Do-Not-Touch Components

Do not delete or replace these during early v5 stages:

- `targetcompass_lite/agent.py`
- `targetcompass_lite/orchestrator.py`
- `targetcompass_lite/orchestration_graph.py`
- `targetcompass_lite/role_execution_dispatcher.py`
- `targetcompass_lite/external_agent_adapter.py`
- existing v4 CLI behavior in `targetcompass_lite/cli.py`
- existing demo/project outputs under `projects/`

The v5 canonical core should be additive under `targetcompass_lite/canonical/` and should write project outputs only under `project_dir/v5/`.

## High-Risk Issues

1. Multiple state machines exist in parallel: v4 agent stages, typed orchestration graph, orchestrator runs, WorkOrder attempts, Task Registry, review queue, and now early v5 memory palace. v5 must define one canonical ProjectState without immediately taking over v4.
2. Some v4 statuses can still be artifact-oriented. `work_order_dag._artifact_ref()` currently derives an artifact id from path and existence, not content checksum. v5 ArtifactManifest must add checksum, placeholder, QC, schema, and provenance checks.
3. The external bioinfo-agent-system includes generated `outputs/mock_run/**`. Those outputs must never be imported as real scientific evidence.
4. `external_agent_adapter.py` has hardcoded sarcopenia/SASP synthesis fallback and `AUTO_*` style dataset placeholders for compatibility. That is useful for demo planning but unsafe as verified production evidence.
5. v4 has real-network and real-LLM paths (`urllib.request.urlopen`, `OPENAI_API_KEY`, GEO/literature/LLM modules). Prompt 0-10 v5 canonical stages must not introduce new real network/API execution.
6. Existing custom JSON schema validation is intentionally lightweight. It covers required fields, basic types, arrays, strings, enum, and numeric minimums, but does not fully implement JSON Schema features such as `additionalProperties`, `oneOf`, formats, pattern validation, or cross-field rules.
7. Codex queue behavior is better than before, but it remains v4 behavior. `codex_task_queue.py` still lets pending/failed/released tasks be claimable and can execute analysis or engineering flows. v5 requires a stricter approved-only worker protocol.

## Compatibility Risks

- Replacing `schema_validation.py` would risk many existing tests and schemas. v5 should add canonical validators instead.
- Modifying default CLI commands could break the local demo and one-click startup scripts.
- Deleting generated project files would make current UI/report demonstrations harder to inspect.
- Directly wiring v5 mock runner into v4 reporting could make mock artifacts look like real evidence.
- Any v5 adapter calling v4 modules must preserve v4 output paths and review gates until migration is explicit.

## Security Risks

- LLM and network code exists and reads environment/project secrets. v5 P0/P1 should not introduce new API keys or live network calls.
- Codex engineering code uses git worktrees and allowlisted unittest commands. This is controlled but must remain gated by review before merge.
- External agent mock outputs and `AUTO_*` placeholders must be treated as unverified references only.
- MCP tools and HTTP server code exist; project-token scope and audit should remain enforced before any v5 external exposure.

## Performance Risks

- Full unittest discovery can be slow on this Windows environment. Quick suite may hit suite-level timeout depending on machine load.
- Large expression matrices and GEO imports must not be read fully into memory for v5 artifact registry. v5 should stream checksums and sample tabular metadata.
- Running all v4/e2e workflows during every canonical schema edit is expensive; stage tests should be fast and full tests recorded separately.

## Testing Gaps

- No current `tests/test_canonical_schemas.py`, `tests/test_canonical_state.py`, `tests/test_agent_protocol.py`, `tests/test_canonical_mock_runner.py`, `tests/test_external_agent_import.py`, `tests/test_canonical_artifacts.py`, `tests/test_question_alignment_auditor.py`, or `tests/test_codex_worker_protocol.py` yet.
- v4 has many tests for real modules, but v5 canonical object-level tests are mostly absent except the newly added memory palace test.
- Current custom schema validator lacks tests for many JSON Schema constructs because it does not implement them.
- The external mock pipeline should be tested only as reference import, not production execution.

## Recommended Refactor Direction

Use a control-plane rewrite and data-plane reuse strategy:

1. Add `targetcompass_lite/canonical/` modules without changing v4 execution modules.
2. Write v5 runtime artifacts under `project_dir/v5/` only.
3. Implement canonical schemas and validators using dataclasses/dicts plus explicit validation. Do not add new dependencies unless documented.
4. Add ProjectState and append-only EventLog as v5 control-plane source of truth, but do not replace v4 orchestrator yet.
5. Define JSON-only AgentSpec/Handoff protocols before any real LLM or database execution.
6. Implement mock runner that stops at task packets and never marks mock datasets as verified.
7. Add ArtifactManifest and Question Alignment Auditor before real evidence synthesis.
8. Add strict v5 Codex Worker protocol that requires approval before claim and does not execute subprocesses.
9. Only after v5 control plane is tested should real GEO/PubMed/LLM/Codex/Nextflow integrations be adapted into it.

## Files That Should Be Changed In Later Steps

- `targetcompass_lite/canonical/schemas.py`
- `targetcompass_lite/canonical/validation.py`
- `targetcompass_lite/canonical/ids.py`
- `targetcompass_lite/canonical/state.py`
- `targetcompass_lite/canonical/events.py`
- `targetcompass_lite/canonical/store.py`
- `targetcompass_lite/canonical/agent_protocol.py`
- `targetcompass_lite/canonical/agent_specs.py`
- `targetcompass_lite/canonical/handoff.py`
- `targetcompass_lite/canonical/mock_runner.py`
- `targetcompass_lite/canonical/workflow_compiler.py`
- `targetcompass_lite/canonical/task_packets.py`
- `targetcompass_lite/canonical/external_agent_import.py`
- `targetcompass_lite/canonical/artifacts.py`
- `targetcompass_lite/canonical/alignment_auditor.py`
- `targetcompass_lite/canonical/codex_worker_protocol.py`
- stage-specific tests under `tests/`
- stage-specific docs under `docs/`

## Files That Should Not Be Changed Yet

- `targetcompass_lite/agent.py`
- `targetcompass_lite/orchestrator.py`
- `targetcompass_lite/orchestration_graph.py`
- `targetcompass_lite/role_execution_dispatcher.py`
- `targetcompass_lite/external_agent_adapter.py`
- v4 analysis modules such as `deg.py`, `scrna.py`, `sasp_score.py`, `enrichment.py`, `meta_analysis.py`
- v4 Evidence DB, QC, WorkOrder DAG, and Codex queue internals unless a later prompt explicitly asks for an adapter or bug fix
- existing demo results under `projects/`

## Prompt 0 Test Record

- Command: `python -m unittest discover tests -v`
- Result: timeout
- Timeout limit used by Codex: 240 seconds
- Observation: the command did not finish before the tool timeout, so no pass/fail count can be claimed from this run.
- Interpretation: this is a test-duration/environment issue for the full discovery command in this session. It is not evidence that Prompt 0 audit failed, and it is not evidence that the full suite passed.

This audit stage intentionally did not change v4 source behavior. The repository already had a preliminary v5 memory palace module from the immediately preceding request; it is separate from this Prompt 0 audit and writes only under `project_dir/v5/memory_palace/`.
