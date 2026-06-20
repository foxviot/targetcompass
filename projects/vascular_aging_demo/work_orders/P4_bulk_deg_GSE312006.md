# WorkOrder: P4_bulk_deg_GSE312006
## Objective
Estimate differential expression for the declared case/control contrast.
## Dataset
- dataset_id: GSE312006
- module: bulk_deg
- status: planned
## Inputs
- dataset_card: dataset_cards/GSE312006.yaml
- expression_matrix: data/GSE312006/expression_matrix.tsv
- metadata: data/GSE312006/metadata.tsv
## Parameters
```json
{
  "case": "replicative_senescence",
  "control": "young",
  "method": "python_demo_welch_like_effect_screen",
  "p_adjustment": "benjamini_hochberg",
  "batch_covariates": [],
  "formal_method_available": "limma via scripts/r/bulk_limma_deg.R when local R dependencies are installed"
}
```
## Expected Outputs
- results/bulk_deg_GSE312006/deg_results.tsv
- results/bulk_deg_GSE312006/qc_summary.tsv
- results/bulk_deg_GSE312006/run_manifest.json
## QC Checks
- expression sample columns match metadata sample_id values
- case and control labels are present in metadata.group
- case_n >= 3 and control_n >= 3 preferred for MVP analysis
- run_manifest records input hashes
## Assumptions
- Rows are gene-level expression values keyed by gene_symbol.
- The MVP Python DEG is association-only and not a clinical/causal test.
## Limitations
- cell culture HUVEC model
- raw counts handled by MVP lightweight DEG
- donor metadata unavailable
- premature senescence samples excluded from first contrast
## Allowed Files
- targetcompass_lite/deg.py
- projects/vascular_aging_demo/results/**
## Command
python tc_lite.py run-deg --project vascular_aging_demo --dataset GSE312006
## Downstream
- annotation
- evidence_import
- scoring
- report
