# TargetCompass Lite Unfinished Development Tasks

This checklist tracks gaps between the implementation plan and the current demo.

## P0 Repository Skeleton

- [x] Minimal CLI exists.
- [x] Project workspace exists.
- [x] Add package metadata (`pyproject.toml`) when the project needs installable commands.

## P1 Schemas And Validation

- [x] Basic ResearchSpec and DatasetCard validation exists.
- [x] Add formal `schemas/*.schema.json`.
- [x] Add EvidenceItem schema validation before SQLite import.
- [x] Add invalid fixture tests for every required object.

## P2 Dataset Screening

- [x] A/B/C/D screening exists for basic bulk datasets.
- [x] Validate matrix and metadata files during screening.
- [x] Add explicit license-unknown blocking tests.

## P3 Analysis Planning

- [x] `analysis_plan.json` and WorkOrder generation exists.
- [x] Expand WorkOrder fields to match the plan template completely.
- [x] Add planning support for descriptive non-bulk evidence.

## P4 Bulk DEG

- [x] Implement formal R-based `scripts/r/bulk_limma_deg.R`.
- [x] Add design-matrix rank checks.
- [x] Add batch covariate handling.
- [x] Keep the current Python DEG only as a lightweight demo fallback.

## P5 Annotation

- [x] Local accessibility and safety annotation exists.
- [x] Replace tiny fixture annotation tables with broader curated local resources.
- [x] Add explicit UNKNOWN review workflow.

## P6 Evidence DB

- [x] SQLite evidence import exists.
- [x] Add schema versioning.
- [x] Add import summaries and rejected-row logs.

## P7 Scoring

- [x] Deterministic scoring exists.
- [x] Move scoring rules from Python into `knowledge_base/scoring_rules/vaccine_target_v0.yaml`.
- [x] Add hard-gate tests for missing lineage, route unknown, and safety excluded.

## P8 Report

- [x] HTML and DOCX reports exist.
- [x] Add per-candidate evidence-chain sections.
- [x] Add dataset summary and screening table to the report.
- [x] Add report wording checks for causal/clinical overclaims.

## User Input And Semantic Understanding

- [x] Implement rule-based ResearchSpec builder.
- [x] Show parsed ResearchSpec fields in the Web UI.
- [x] Add dataset/spec match warnings.
- [x] Later: optional GPT-backed parser with explicit user confirmation.

## UI / Local App

- [x] Minimal local Web input page exists.
- [x] Improve service lifecycle and port handling.
- [x] Add run status and error output in the browser.
- [x] Add dataset selection controls.

## Real Data

- [x] First real public GEO dataset (`GSE312006`) is integrated.
- [x] Add at least one human artery or vascular tissue dataset.
- [x] Add metadata quality scoring.
- [x] Separate fixture and real-data runs in the report.
