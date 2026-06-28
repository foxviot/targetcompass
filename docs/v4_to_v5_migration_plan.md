# v4 To v5 Migration Plan

## Migration Principle

v5 should not replace v4 in one large rewrite. The safe path is to keep v4 scientific execution working while v5 becomes the canonical control plane around it.

## What Stays Reusable From v4

Reusable v4 components:

- `deg.py`
- `scrna.py`
- `sasp_score.py`
- `enrichment.py`
- `meta_analysis.py`
- `geo_discovery.py`
- `geo_importer.py`
- `geo_raw.py`
- `evidence_planning.py`
- `evidence_db.py`
- `qc.py`
- `qc_review.py`
- `work_order_dag.py`
- `review.py`
- `reporting.py`
- `codex_task_queue.py`
- MCP gateway/server contracts

## What v5 Controls First

v5 should own:

- canonical user question representation
- stable object IDs
- project state and event log
- agent handoff contracts
- task packet shape
- artifact trust metadata
- question alignment auditing
- approval-before-worker protocol

## Phase 1: Additive Control Plane

Already implemented in early v5 stages:

- canonical schemas
- `ProjectState` and `EventLog`
- agent protocol
- mock runner to task packets
- external agent reference import
- artifact registry
- question alignment auditor
- Codex worker protocol

No v4 execution logic is replaced in this phase.

## Phase 2: Adapter Execution

Next migration step:

1. Convert v5 `AnalysisTaskPacket` into v4 module calls.
2. Register all v4 outputs in v5 `ArtifactRegistry`.
3. Attach QC reports to artifacts.
4. Import only QC-approved outputs into Evidence DB.
5. Run Question Alignment Auditor before report promotion.

The adapter must keep v4 output paths stable while adding v5 trace metadata.

## Phase 3: Orchestrator Consolidation

After adapters are stable:

1. v5 `ProjectState` becomes the top-level lifecycle source of truth.
2. v4 WorkOrder DAG becomes an execution backend rather than the main control plane.
3. old agent shells are deprecated behind compatibility wrappers.
4. report generation consumes v5 evidence and alignment reports.

## Phase 4: Production Hardening

Required before production:

- real dataset verification gates
- real LLM role execution with schema validation
- sandboxed Codex worker execution
- artifact storage policy
- evidence DB migration policy
- service identity and audit
- quick/full/e2e test split
- release packaging and rollback

## What Must Not Happen

- Do not delete the v4 demo path during migration.
- Do not import external mock outputs as evidence.
- Do not mark `AUTO_*` or placeholder datasets as verified.
- Do not treat file existence as scientific success.
- Do not let a worker claim a task before approval.
- Do not promote claims above their evidence ceiling.

## Migration Acceptance Criteria

A migrated v5 path is acceptable only when:

- user question is represented as `ResearchSpec`
- every task has a typed packet
- every artifact has checksum and QC metadata
- every claim references evidence
- every claim passes scope and claim-ceiling audit
- human review gates are recorded
- v4 output remains reproducible through adapter logs
