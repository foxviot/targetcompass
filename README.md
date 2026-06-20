# TargetCompass Lite

TargetCompass Lite is a local-first target discovery MVP. It turns a biomedical research request into a traceable workflow with structured ResearchSpec generation, dataset audit, DEG analysis, enrichment, evidence import, candidate scoring, review actions, adapter audit, and delivery package export.

The default demo focuses on vascular aging and endothelial senescence.

## Quick Start

Beginner one-click launch:

```text
Double-click START_TARGETCOMPASS.bat
```

The launcher checks the local install, starts the web app at `http://127.0.0.1:8781/`, and opens the browser automatically. If the app is already running, it only opens the browser.

Manual launch:

```powershell
python scripts\check_install.py
python tc_lite.py serve --project vascular_aging_demo --port 8781
```

Open:

```text
http://127.0.0.1:8781/
```

PowerShell helper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_app.ps1
powershell -ExecutionPolicy Bypass -File scripts\start_app_one_click.ps1
```

Run the demo and export a package:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

## GPT Mode

Set an OpenAI API key before starting the app:

```powershell
$env:OPENAI_API_KEY="your_api_key"
```

Supported GPT-backed modes:

- `GPT generator via OpenAI API` for ResearchSpec generation.
- `gpt_idea_query_v0` for GPT-assisted idea generation.

If the API key is missing, `gpt_idea_query_v0` falls back to local deterministic idea generation and records the reason in `results/agent_trace.json`.

## Replaceable Agent Methods

Query, audit, and experiment-design methods are configurable:

```powershell
python tc_lite.py methods --project vascular_aging_demo

python tc_lite.py methods --project vascular_aging_demo `
  --set-query gpt_idea_query_v0 `
  --set-audit strict_feasibility_audit_v0 `
  --set-experiment review_first_experiment_design_v0
```

Current methods:

- `local_idea_query_v0`
- `gpt_review_ready_query_v0`
- `gpt_idea_query_v0`
- `local_feasibility_audit_v0`
- `strict_feasibility_audit_v0`
- `local_experiment_design_v0`
- `review_first_experiment_design_v0`

## Agent State Machine

The web agent now runs as an explicit six-step state machine:

```text
generation -> initial_review -> verification -> execution -> final_review -> report
```

Stage mapping:

- `generation`: generate ResearchSpec and candidate ideas. Replaceable method stage: `query`.
- `initial_review`: run ResearchSpec readiness gates and feasibility audit. Replaceable method stage: `audit`.
- `verification`: validate selected datasets, screen eligibility, match ResearchSpec, and compile AnalysisPlan.
- `execution`: run DEG, enrichment, annotation, evidence import, and scoring.
- `final_review`: draft experiments and summarize remaining review gates. Replaceable method stage: `experiment`.
- `report`: generate final HTML / Word-compatible report artifacts.

Each run writes the full state trace to:

```text
projects/vascular_aging_demo/results/agent_trace.json
```

## Database Adapters

Register custom resources from the UI or CLI.

Supported resource types:

- `dataset_card`
- `annotation_table`
- `gene_set`
- `literature_card`
- `external_database`

Supported database adapters:

- `auto`
- `tabular_evidence_v0`
- `sqlite_evidence_v0`
- `uniprot_target_v0`
- `hpa_safety_accessibility_v0`
- `opentargets_evidence_v0`
- `disgenet_evidence_v0`
- `gwas_catalog_evidence_v0`
- `msigdb_gene_sets_v0`
- `reactome_gene_sets_v0`

Examples:

```powershell
python scripts\create_example_sqlite.py

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id sample_csv_targets `
  --type external_database `
  --path examples\databases\sample_target_evidence.csv `
  --adapter tabular_evidence_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id sample_sqlite_targets `
  --type external_database `
  --path examples\databases\sample_target_evidence.sqlite `
  --adapter sqlite_evidence_v0

python tc_lite.py knowledge-adapt --project vascular_aging_demo
python tc_lite.py adapter-audit --project vascular_aging_demo
```

Standard source examples:

```powershell
python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id uniprot_demo --type external_database `
  --path examples\databases\sample_uniprot_targets.tsv `
  --adapter uniprot_target_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id hpa_demo --type external_database `
  --path examples\databases\sample_hpa_safety.tsv `
  --adapter hpa_safety_accessibility_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id opentargets_demo --type external_database `
  --path examples\databases\sample_opentargets.csv `
  --adapter opentargets_evidence_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id disgenet_demo --type external_database `
  --path examples\databases\sample_disgenet.tsv `
  --adapter disgenet_evidence_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id gwas_demo --type external_database `
  --path examples\databases\sample_gwas_catalog.tsv `
  --adapter gwas_catalog_evidence_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id msigdb_demo --type external_database `
  --path examples\databases\sample_msigdb.gmt `
  --adapter msigdb_gene_sets_v0

python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id reactome_demo --type external_database `
  --path examples\databases\sample_reactome.tsv `
  --adapter reactome_gene_sets_v0

python tc_lite.py knowledge-adapt --project vascular_aging_demo
```

Adapter audit outputs:

```text
projects/vascular_aging_demo/results/adapter_audit/adapter_audit.tsv
projects/vascular_aging_demo/results/adapter_audit/adapter_audit.json
```

The audit records input rows, normalized rows, dropped rows, adapter messages, and field mappings.

## GEO / GSE Dataset Import

The MVP can import a GEO series matrix into the runnable dataset flow:

```powershell
python tc_lite.py geo-import --project vascular_aging_demo `
  --accession GSE43292 `
  --case-label atheroma_plaque `
  --control-label intact_carotid `
  --case-pattern "tissue: Atheroma plaque" `
  --control-pattern "tissue: Macroscopically intact" `
  --tissue "carotid artery" `
  --organism human `
  --platform-annotation projects\vascular_aging_demo\data\GSE43292\GPL6244.annot.gz `
  --symbol-column "Gene symbol"
```

The importer downloads or reuses the GEO series matrix, builds:

```text
projects/vascular_aging_demo/data/<GSE>/expression_matrix.tsv
projects/vascular_aging_demo/data/<GSE>/metadata.tsv
projects/vascular_aging_demo/dataset_cards/<GSE>.yaml
```

Then the dataset can enter the existing DEG workflow:

```powershell
python tc_lite.py demo --project vascular_aging_demo --dataset GSE43292
```

Case/control patterns should be specific. If a sample matches both case and control patterns, it is skipped and reported as a warning.

## Preloaded GEO Cards

The default project includes runnable bulk RNA dataset cards and reference GSE cards:

- `GSE312006` and `GSE43292`: runnable bulk-expression demo cards.
- `GSE40279`, `GSE87571`, and `GSE113957`: reference cards for aging/senescence context. Their matrices are not bundled, so they are blocked from MVP bulk DEG modules until a compatible matrix is registered.

## Manual Review

Ideas can be approved, marked for review, or rejected in the UI. CLI:

```powershell
python tc_lite.py review --project vascular_aging_demo --item-type idea --item-id <idea_id> --action approve --note "looks feasible"
```

Review actions are stored in:

```text
projects/vascular_aging_demo/results/review_actions.tsv
projects/vascular_aging_demo/results/review_actions.jsonl
```

Each review action records a review id, reason, note, report reference, before/after hashes, and a small diff of changed review fields.

## Run Status And Recovery

The web app persists run status in:

```text
projects/vascular_aging_demo/results/run_status.json
```

The status file records `run_id`, current status, active stage, failure reason, stdout/stderr, stage trace, and the last request. The UI supports:

- cancel request, checked between Agent stages;
- rerun last request;
- partial recompute for annotation, enrichment, evidence import, scoring, and report rebuild;
- log viewing from the run status panel.

## Language Switch

The web UI supports Chinese and English. The selected language is saved in:

```text
projects/vascular_aging_demo/configs/ui_language.json
```

## Dark Mode And Markdown Methods

The web UI includes a light/dark toggle. The selected theme is saved in:

```text
projects/vascular_aging_demo/configs/ui_theme.json
```

Markdown skill / agent method files can be uploaded from `Advanced workspace -> Method configuration`.
Supported stages:

- Query / idea generation
- Audit / feasibility review
- Experiment design

Uploaded files are stored under:

```text
projects/<project>/agent_methods/
```

For MVP safety, Markdown methods are attached as method guidance around stable built-in runners rather than executing arbitrary code directly.

## Delivery Package

Export a reviewable run package:

```powershell
python tc_lite.py export-package --project vascular_aging_demo
```

Packages are written under:

```text
projects/vascular_aging_demo/exports/
```

The package includes ResearchSpec, method config, knowledge registry, agent trace, review actions, adapter audit, idea batch, experiment designs, and reports.

## v4.0 Compatibility Layer

The `codex/v4-development` branch now starts the v4.0 backend-engine migration without replacing the MVP runner. Generate v4 objects with:

```powershell
python tc_lite.py v4-manifest --project vascular_aging_demo
```

This writes a v4 state machine, object manifest, DiseaseSpec, WorkOrder index, MCP resource manifest, evidence snapshot, and Codex task packets when an unregistered adapter/module is required.

Development backlog:

```text
docs/v4_development_backlog.md
```

## Verification

```powershell
python -m unittest discover -s tests -p "test*.py" -v
python scripts\smoke_test.py
```

Current validation snapshot:

- Unit tests: `105 tests OK`.
- Smoke test: passing.
- Real online GEO import: `GSE43292`, 64 samples, 19033 genes.
- Real-data stress test: 100 independent Agent runs on random subsets from downloaded GSE43292, `100/100` passing.
- Stress summary: `projects/real100final_summary_1781942796.json`.

## Main Outputs

```text
analysis_module_registry.json
research_spec.json
screening_report.md
eligible_datasets.csv
dataset_match_report.csv
analysis_plan.json
work_orders/*.md
results/bulk_deg_*/deg_results.tsv
results/bulk_deg_*/qc_summary.json
results/bulk_deg_*/run_manifest.json
results/enrichment/enrichment_results.tsv
results/annotation/accessibility_annotation.tsv
results/annotation/safety_flags.tsv
results/annotation/unknown_review.tsv
results/evidence_import/import_summary.json
results/adapter_audit/adapter_audit.tsv
results/agent_trace.json
evidence.sqlite
candidate_scores.csv
reports/target_report.html
reports/target_report.docx
exports/*.zip
```

## Analysis Modules And Report Structure

Implemented MVP modules:

- `bulk_deg_v1`: bulk RNA / microarray DEG with `qc_summary.json`, matrix-type detection, design-rank checks, runner metadata, and run manifest.
- `enrichment_v1`: local plus adapter-imported gene sets.
- `accessibility_annotation_v1`: local, UniProt, HPA, and custom accessibility annotations.
- `safety_annotation_v1`: safety flags plus UNKNOWN review preservation.

Reserved interfaces:

- `scrna_pseudobulk_v0`
- `genetic_coloc_mr_v0`

The report is structured for research review: executive summary, research scope, methods/modules, data source/QC, candidate ranking, evidence chains, limitations, experiment designs, and audit/review records.

## Notes

This is a research workflow MVP. It produces association-level evidence and review artifacts. It does not provide medical, diagnostic, treatment, or causal claims.
