# WorkOrder: P4_bulk_deg_GSE43292
## Objective
Estimate differential expression for the declared case/control contrast.
## Dataset
- dataset_id: GSE43292
- module: bulk_deg
- status: planned
## Inputs
- dataset_card: dataset_cards/GSE43292.yaml
- expression_matrix: data/GSE43292/expression_matrix.tsv
- metadata: data/GSE43292/metadata.tsv
## Parameters
```json
{
  "case": "atheroma_plaque",
  "control": "intact_carotid",
  "method": "python_demo_welch_like_effect_screen",
  "p_adjustment": "benjamini_hochberg",
  "batch_covariates": [],
  "formal_method_available": "limma via scripts/r/bulk_limma_deg.R when local R dependencies are installed"
}
```
## Expected Outputs
- results/bulk_deg_GSE43292/deg_results.tsv
- results/bulk_deg_GSE43292/qc_summary.tsv
- results/bulk_deg_GSE43292/run_manifest.json
- results/bulk_deg_GSE43292/executor_manifest.json
## QC Checks
- expression sample columns match metadata sample_id values
- case and control labels are present in metadata.group
- case_n >= 3 and control_n >= 3 preferred for MVP analysis
- run_manifest records input hashes
## Assumptions
- Rows are gene-level expression values keyed by gene_symbol.
- The MVP Python DEG is association-only and not a clinical/causal test.
## Limitations
- auto-imported from GEO series matrix
- case/control assignment inferred from metadata column source_name_ch1 with confidence 80
- MVP lightweight DEG; inspect metadata_profile.json and group_inference.json before interpreting
## Allowed Files
- targetcompass_lite/deg.py
- projects/vascular_aging_demo/results/**
## Command
python tc_lite.py run-deg --project vascular_aging_demo --dataset GSE43292
## Downstream
- annotation
- evidence_import
- scoring
- report
