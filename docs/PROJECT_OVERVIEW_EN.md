# TargetCompass v5 Project Overview

## Positioning

TargetCompass is a local-first biomedical target discovery platform. It turns a natural-language research question into a traceable evidence workflow: canonical agent handoffs, resource discovery, dataset gating, task packets, analysis execution contracts, QC reports, artifact registration, evidence scoring, claim alignment, and report delivery.

The current repository is a **v5 local developer / demonstration release**. It is suitable for local review, professor demos, GPT/Codex acceptance, and continued engineering. It is not yet a hosted multi-tenant production service.

## What Problem It Solves

Biomedical target discovery often fails because analysis results, dataset choices, assumptions, failed steps, and report claims are scattered across scripts and manual notes. TargetCompass makes these parts explicit:

- What question was asked.
- Which evidence axes are needed.
- Which datasets were considered, rejected, or locked.
- Which methods were allowed by the data.
- Which task packet produced each output.
- Which QC checks passed or required review.
- Which artifacts and evidence items support each report claim.
- Which claims exceed the allowed evidence level.

This makes the workflow easier to audit, rerun, correct, and hand over.

## v5 Canonical Workflow

The v5 control plane uses seven canonical agents. Agents do not pass free-form conclusions to each other; they pass structured object references, artifact references, evidence references, assumptions, blocking issues, and claim ceilings.

1. `question_normalizer`
   - Converts the user question into `ResearchSpec` and `SubQuestion` objects.
   - Does not recommend datasets or produce scientific results.

2. `scope_resolver`
   - Resolves disease, organism, tissue, cell type, condition, and claim boundaries.
   - Keeps human evidence, model-organism evidence, tissue evidence, and cell-type evidence separate.

3. `evidence_plan_builder`
   - Builds the evidence axes needed to answer the question.
   - Defines what would count as expression, cell-type, surface, secreted, SASP, enrichment, genetic, or causal evidence.

4. `resource_discovery_agent`
   - Searches candidate data resources and literature resources.
   - Produces `ResourceCandidate`, `DatasetProfile`, and `DatasetSelectionDecision` objects.
   - Does not lock a dataset unless metadata, organism, tissue, grouping, sample size, platform, and matrix readiness pass the gate.

5. `method_adapter_workorder_compiler`
   - Converts evidence needs and verified data profiles into workflow plans and task packets.
   - Uses data compatibility rules so methods are constrained by the available data.

6. `result_auditor`
   - Reviews task runs, artifacts, and QC reports.
   - Writes audit records and evidence references.
   - Does not modify raw results.

7. `evidence_synthesizer_reporter`
   - Consumes audited evidence only.
   - Builds claims and reports within the current claim ceiling.
   - Must include limitations, failed evidence, and unresolved questions.

## Core Objects

- `ResearchSpec`: normalized research question and intended scope.
- `SubQuestion`: smaller answerable questions derived from the main question.
- `ScopeBundle`: structured disease, species, tissue, cell type, condition, and claim boundaries.
- `EvidencePlan`: evidence axes and minimum requirements.
- `DatasetProfile`: dataset metadata, readiness, limitations, and lock status.
- `MethodContract`: method requirements, allowed inputs, expected outputs, and QC requirements.
- `CompatibilityDecision`: decision that a dataset can or cannot support a method.
- `AnalysisTaskPacket`: analysis task with expected inputs, outputs, QC, and failure conditions.
- `EngineeringTaskPacket`: controlled engineering task with allowed paths, forbidden paths, and tests.
- `ReviewTaskPacket`: review task with audit scope and claim ceiling.
- `TaskRun`: execution record for a task packet.
- `QCReport`: execution, data, statistical, and biological QC result.
- `ArtifactManifest`: file/object manifest with checksum, producer, schema, status, limitations, and evidence links.
- `EvidenceItem`: normalized evidence unit used for scoring and reporting.
- `QuestionAlignmentReport`: check that claims answer the original question and do not exceed the evidence ceiling.
- `CanonicalReportManifest`: report package index that references evidence, artifacts, QC, and alignment outputs.

## Current Capabilities

The v5 local release currently includes:

- Canonical agent protocol, handoff format, and claim-ceiling validation.
- v5 mock runner for safe end-to-end control-plane testing.
- Resource discovery adapters and gates for GEO/SRA/ArrayExpress/cellxgene-style candidates.
- PubMed / Europe PMC-oriented literature discovery paths.
- Dataset lock and human correction UI for incomplete metadata.
- Matrix path readiness checks so metadata-only datasets are not treated as analyzable matrices.
- v5 task packets, worker protocol, approval, claim, completion, and failure states.
- Local execution contracts for analysis, Nextflow, and controlled Codex worker paths.
- Artifact Registry and ArtifactStore abstraction.
- EvidenceRepository abstraction with SQLite fallback and PostgreSQL primary-path support.
- MinIO/S3 object-store support for v5 JSON and artifact paths.
- QC with execution, data, statistical, and biological layers.
- Question Alignment Auditor for unsupported claims, scope drift, claim ceiling violations, placeholder artifacts, and failed-QC evidence.
- PilotDeck local web UI with Chinese, Japanese, and English language switching.
- v5 doctor, release acceptance pages, storage/backend pages, resource gate pages, and product report pages.
- Windows packaging scripts and Inno Setup installer assets.

## Important Limitations

This repository should not be described as fully production-hosted yet.

- OIDC/Vault and full multi-user login sessions are not part of the local v5 delivery.
- PostgreSQL/MinIO can be active backends, but some legacy analysis/report writers may still write local files before registration or synchronization.
- SRA and cellxgene true large-scale matrix download/quantification/analysis paths need more real-data validation.
- Nextflow and Codex worker contracts exist, but large-sample production validation still needs more test runs.
- Windows installer scripts exist, but signed installer and clean-machine acceptance records must be produced for formal release.
- Wet-lab protocol output is currently an auditable recommendation/signoff surface, not a production SOP generator.
- Literature can be used for validation and context, but the target discovery workflow should not depend on literature alone when the research question requires omics evidence.

## Quick Start

From the repository root:

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8831
```

Open:

```text
http://127.0.0.1:8831/
```

Run v5 doctor:

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
```

Run release acceptance checks:

```powershell
python tc_lite.py v5-release-acceptance --project vascular_aging_demo --question-count 10
python tc_lite.py v5-release-acceptance --project vascular_aging_demo --question-count 50
```

Run matrix path validation:

```powershell
python tc_lite.py v5-matrix-path-validation --project vascular_aging_demo
```

Useful focused tests:

```powershell
python -m unittest tests.test_canonical_matrix_path_validation tests.test_canonical_storage_primary_gate tests.test_v5_doctor tests.test_release_acceptance -v
```

## Recommended Acceptance Path

1. Start the local web UI.
2. Run `v5-doctor` and record warnings.
3. Run a 10-question online validation batch.
4. Run resource discovery on several independent questions.
5. Lock at least one dataset through metadata correction.
6. Validate matrix readiness before analysis routing.
7. Run the local analysis path or Nextflow path where dependencies are available.
8. Confirm TaskRun, QCReport, Artifact Registry, EvidenceRepository, and report outputs are linked.
9. Open the PilotDeck pages and verify that agent handoff, resource gate, storage backend, report, and release acceptance pages are readable.
10. Package or install on a clean Windows machine before external delivery.

## Delivery Notes

- License: Apache License 2.0.
- Runtime outputs, secrets, downloaded raw data, and local cache files should not be committed.
- The repository is intended to be handed to another Codex/GPT reviewer with this documentation plus the Chinese overview.

