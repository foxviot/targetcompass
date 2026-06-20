from pathlib import Path
import json


ANALYSIS_MODULES = [
    {
        "module_id": "bulk_deg_v1",
        "status": "implemented",
        "input_modality": "bulk_expression",
        "runner": "targetcompass_lite.deg.run_deg",
        "outputs": ["deg_results.tsv", "qc_summary.tsv", "qc_summary.json", "run_manifest.json", "executor_manifest.json"],
        "notes": "Supports RNA-seq count-like and microarray/log-expression-like matrices after gene-symbol normalization.",
    },
    {
        "module_id": "enrichment_v2",
        "status": "implemented",
        "input_modality": "deg_results",
        "runner": "targetcompass_lite.enrichment.run_enrichment",
        "outputs": ["results/enrichment/enrichment_results.tsv", "results/enrichment/run_manifest.json", "results/enrichment/qc_summary.json"],
        "notes": "Consumes local and adapter-imported gene sets, including MSigDB/Reactome normalized files; writes QC and manifest.",
    },
    {
        "module_id": "accessibility_annotation_v1",
        "status": "implemented",
        "input_modality": "gene_symbols",
        "runner": "targetcompass_lite.annotation.annotate_project",
        "outputs": ["accessibility_annotation.tsv"],
        "notes": "Consumes local annotation plus UniProt/HPA/custom normalized accessibility tables.",
    },
    {
        "module_id": "safety_annotation_v1",
        "status": "implemented",
        "input_modality": "gene_symbols",
        "runner": "targetcompass_lite.annotation.annotate_project",
        "outputs": ["safety_flags.tsv", "unknown_review.tsv"],
        "notes": "Preserves UNKNOWN values for manual review rather than silently passing candidates.",
    },
    {
        "module_id": "scrna_pseudobulk_v1",
        "status": "implemented",
        "input_modality": "single_cell_expression",
        "runner": "targetcompass_lite.scrna.run_scrna_pseudobulk",
        "outputs": [
            "results/scrna_pseudobulk_{dataset_id}/pseudobulk_matrix.tsv",
            "results/scrna_pseudobulk_{dataset_id}/pseudobulk_metadata.tsv",
            "results/scrna_pseudobulk_{dataset_id}/donor_group_qc.tsv",
            "results/scrna_pseudobulk_{dataset_id}/group_qc.tsv",
            "qc_summary.json",
            "run_manifest.json",
        ],
        "notes": "Donor-aware pseudobulk aggregation with group-level donor QC, contrast declaration, and input hashes. Cells are never treated as biological replicates. Legacy interface alias: scrna_pseudobulk_v0.",
    },
    {
        "module_id": "deg_meta_analysis_v1",
        "status": "implemented",
        "input_modality": "bulk_deg_results",
        "runner": "targetcompass_lite.meta_analysis.run_meta_analysis",
        "outputs": ["results/meta_analysis/deg_meta_analysis.tsv", "results/meta_analysis/run_manifest.json"],
        "notes": "Lightweight cross-dataset DEG summary for triage; publication-grade meta-analysis still requires formal model review.",
    },
    {
        "module_id": "genetic_coloc_mr_v1",
        "status": "implemented",
        "input_modality": "gwas_qtl_summary",
        "runner": "targetcompass_lite.genetic.run_genetic_coloc_mr",
        "outputs": ["results/genetic_coloc_mr/harmonized_gwas_qtl.tsv", "results/genetic_coloc_mr/genetic_evidence.tsv", "results/genetic_coloc_mr/sensitivity_summary.tsv", "results/genetic_coloc_mr/run_manifest.json"],
        "notes": "Minimal harmonized GWAS/QTL contract with coloc and single-variant MR proxy outputs. Legacy interface alias: genetic_coloc_mr_v0.",
    },
    {
        "module_id": "causal_evidence_grading_v1",
        "status": "implemented",
        "input_modality": "genetic_evidence",
        "runner": "targetcompass_lite.causal_evidence.grade_causal_evidence",
        "outputs": ["results/causal_evidence/causal_evidence_grades.tsv", "results/causal_evidence/run_manifest.json"],
        "notes": "Grades GWAS/QTL/coloc/MR-like evidence for triage while preserving limitations for human review.",
    },
]


def module_registry_path(project_dir: Path) -> Path:
    return project_dir / "analysis_module_registry.json"


def write_module_registry(project_dir: Path) -> Path:
    path = module_registry_path(project_dir)
    path.write_text(json.dumps({"modules": ANALYSIS_MODULES}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
