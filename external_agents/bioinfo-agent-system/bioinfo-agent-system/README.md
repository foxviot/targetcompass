# Bioinfo Agent System

`bioinfo-agent-system` is a minimal, deterministic, six-agent bioinformatics research planning pipeline.

It converts a vague natural-language research question into a schema-bound, provenance-aware, executable research plan while preserving uncertainty and claim limits.

## What This Is

- A local mock pipeline that proves the six-agent architecture works end to end.
- A schema-driven handoff system with explicit validation gates.
- A conservative planner that keeps final claims at association or co-expression level for the bundled example.

## What This Is Not

- Not a web application.
- Not a real PubMed, GEO, cellxgene, or OpenAI integrated system.
- Not a wet-lab automation platform or a workflow engine.
- Not a source of verified biological conclusions.

## Six-Agent Architecture

1. `01_scientific_question_normalizer`
2. `02_scope_ontology_resolver`
3. `03_evidence_dataset_scout`
4. `04_method_extraction_agent`
5. `05_method_motif_feasibility_synthesizer`
6. `06_research_plan_compiler`

Every agent has:

- a fixed role
- a fixed input schema
- a fixed output schema
- explicit write ownership
- forbidden actions
- validation gates
- failure behavior
- provenance requirements
- a claim ceiling

## Claim Ceiling

The pipeline tracks a maximum allowed claim at every handoff.

- Expression-level evidence can support association or co-expression claims.
- Expression-only evidence cannot support causal support.
- Surface or secretome annotation remains candidate-level only.
- Agent 6 cannot exceed the lowest relevant upstream claim ceiling.

See [references/claim_strength_taxonomy.md](./references/claim_strength_taxonomy.md) and [references/forbidden_inference_rules.md](./references/forbidden_inference_rules.md).

## Run The Mock Pipeline

```bash
python scripts/run_mock_pipeline.py examples/input_question_t2d_adipose_secretome.txt
```

This writes outputs under:

```text
outputs/mock_run/<run_id>/
```

## Validate Outputs

```bash
python scripts/validate_all.py outputs/mock_run/<run_id>/
python scripts/audit_claim_ceiling.py outputs/mock_run/<run_id>/
python scripts/run_evals.py
```

## Project Layout

```text
agents/                  Agent contracts, schemas, examples, eval fixtures
references/              Shared scientific and planning rules
schemas/shared/          Shared schema contracts
src/bioinfo_agent_system Runtime package
scripts/                 CLI entry points
examples/                Example input and expected outputs
evals/                   Simple deterministic eval fixtures
outputs/                 Generated mock runs
```

## Extension Points

Future versions can replace mock modules with real integrations:

- Agent 3 with PubMed, GEO, and cellxgene search.
- Agent 4 with literature method extraction from full text.
- Agent 5 with a real local method-contract library.
- Agent 6 task packets with downstream workflow execution.

The current version intentionally leaves those as documented extension points only.
