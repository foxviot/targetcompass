# TargetCompass Lite User Guide

## Start The App

Run:

```powershell
python scripts\check_install.py
python tc_lite.py serve --project vascular_aging_demo --port 8781
```

Or:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_app.ps1
```

Open:

```text
http://127.0.0.1:8781/
```

## OpenAI API Key

Set the API key before starting the app:

```powershell
$env:OPENAI_API_KEY="your_api_key"
```

Then choose:

- `GPT generator via OpenAI API` for ResearchSpec generation.
- `gpt_idea_query_v0` for GPT-assisted idea generation.

If the key is missing, `gpt_idea_query_v0` falls back to local deterministic idea generation and records the reason in the agent trace.

## Run Workflow

1. Enter a research request.
2. Choose generation engine.
3. Set idea volume.
4. Select datasets.
5. Click `Run GPT-guided agent`.
6. Review ideas using Approve / Review / Reject.
7. Build adapter audit if custom databases were added.
8. Export run package.

## Add Databases

Use the UI section `Knowledge / Database registry`.

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

CLI example:

```powershell
python tc_lite.py knowledge-add --project vascular_aging_demo --id sample_targets --type external_database --path examples\databases\sample_target_evidence.csv --adapter tabular_evidence_v0
python tc_lite.py knowledge-adapt --project vascular_aging_demo
python tc_lite.py adapter-audit --project vascular_aging_demo
```

## Export Delivery Package

```powershell
python tc_lite.py export-package --project vascular_aging_demo
```

The package includes:

- ResearchSpec
- method configuration
- knowledge registry
- agent trace
- review actions
- adapter audit
- idea batch
- experiment designs
- final reports

## Verification

```powershell
python -m unittest discover -s tests
python scripts\smoke_test.py
```
