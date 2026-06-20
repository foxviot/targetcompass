# WorkOrder: P4_bulk_deg_ds_fixture_vascular_aging
## Objective
Estimate differential expression for the declared case/control contrast.
## Dataset
- dataset_id: ds_fixture_vascular_aging
- module: bulk_deg
- status: planned
## Inputs
- dataset_card: dataset_cards/ds_fixture_vascular_aging.yaml
- expression_matrix: data/ds_fixture_vascular_aging/expression_matrix.tsv
- metadata: data/ds_fixture_vascular_aging/metadata.tsv
## Parameters
```json
{
  "case": "aged",
  "control": "young",
  "method": "python_demo_welch_like_effect_screen",
  "p_adjustment": "benjamini_hochberg",
  "batch_covariates": [],
  "formal_method_available": "limma via scripts/r/bulk_limma_deg.R when local R dependencies are installed"
}
```
## Expected Outputs
- results/bulk_deg_ds_fixture_vascular_aging/deg_results.tsv
- results/bulk_deg_ds_fixture_vascular_aging/qc_summary.tsv
- results/bulk_deg_ds_fixture_vascular_aging/run_manifest.json
- results/bulk_deg_ds_fixture_vascular_aging/executor_manifest.json
## QC Checks
- expression sample columns match metadata sample_id values
- case and control labels are present in metadata.group
- case_n >= 3 and control_n >= 3 preferred for MVP analysis
- run_manifest records input hashes
## Assumptions
- Rows are gene-level expression values keyed by gene_symbol.
- The MVP Python DEG is association-only and not a clinical/causal test.
## Limitations
- synthetic fixture
- small sample size
## Allowed Files
- targetcompass_lite/deg.py
- projects/vascular_aging_demo/results/**
## Command
python tc_lite.py run-deg --project vascular_aging_demo --dataset ds_fixture_vascular_aging
## Downstream
- annotation
- evidence_import
- scoring
- report
