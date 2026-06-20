# TargetCompass Lite MVP Development List

This list is ordered by what most improves the local demo's credibility and usability.

## Now

1. [x] P3 Analysis planning
   - [x] Expand `analysis_plan.json` with executable module metadata, inputs, outputs, QC checks, assumptions, limitations, and downstream dependencies.
   - [x] Expand WorkOrder Markdown so every runnable/descriptive module is auditable by a user.
   - [x] Add descriptive non-bulk evidence planning so C-grade datasets are not silently ignored.

2. [x] P6 Evidence DB
   - [x] Add evidence schema version metadata.
   - [x] Validate evidence rows before SQLite insert.
   - [x] Write import summaries and rejected-row logs.

3. [x] UI / Local App
   - [x] Show run status and workflow error output in the browser.
   - [x] Add dataset selection controls.
   - [x] Improve local service lifecycle and port handling.

## Next

4. [x] P1 Schemas and validation
   - [x] Add formal JSON schemas for `ResearchSpec`, `DatasetCard`, and `EvidenceItem`.
   - [x] Add invalid fixture tests for every required object.

5. [x] P4 Bulk DEG
   - [x] Add design-matrix rank checks.
   - [x] Add optional batch covariate handling.
   - [x] Add formal R/limma runner when local R dependencies are available.
   - [x] Keep Python DEG as the local demo fallback.

6. [x] P5 Annotation
   - [x] Replace tiny fixture annotation tables with broader curated local resources.
   - [x] Add an explicit UNKNOWN review workflow.

## Later

7. [x] Real data expansion
   - [x] Add at least one human artery or vascular tissue dataset.
   - [x] Add metadata quality scoring.
   - [x] Separate fixture and real-data runs in the report.

8. [x] Optional semantic parser upgrade
   - [x] Add GPT-backed parsing only with explicit user confirmation.
   - [x] Keep rule-based parser as a deterministic fallback.

9. [x] Packaging
   - [x] Add `pyproject.toml` once the command surface stabilizes.
