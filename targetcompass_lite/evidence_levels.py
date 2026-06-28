from __future__ import annotations

from typing import Any


EVIDENCE_LEVELS: dict[str, dict[str, Any]] = {
    "L0_abstract": {
        "label": "Abstract-only literature",
        "weight": 0.25,
        "basis": "Evidence extracted from title/abstract/MeSH only.",
    },
    "L1_fulltext": {
        "label": "Full-text literature",
        "weight": 0.55,
        "basis": "Evidence extracted from accessible full text or uploaded PDF/text.",
    },
    "L2_database": {
        "label": "Curated database",
        "weight": 0.65,
        "basis": "Evidence from curated online or local target/pathway/safety database.",
    },
    "L3_omics": {
        "label": "Omics analysis",
        "weight": 0.80,
        "basis": "Evidence from local expression, enrichment, meta-analysis, or single-cell analysis.",
    },
    "L4_genetic": {
        "label": "Genetic/causal evidence",
        "weight": 0.90,
        "basis": "Evidence from GWAS/QTL/coloc/MR or causal grading.",
    },
    "L5_experimental": {
        "label": "Experimental validation",
        "weight": 1.00,
        "basis": "Evidence from direct perturbation, validation experiment, or figure/table-supported result.",
    },
}


DATABASE_TYPES = {
    "accessibility",
    "uniprot_annotation",
    "opentargets_association",
    "disgenet_association",
    "gwas_association",
    "database_prior",
    "external_database",
    "surface_marker_annotation",
    "cell_type_expression",
}

OMICS_TYPES = {
    "bulk_deg",
    "deg_meta_analysis",
    "enrichment",
    "scrna_pseudobulk",
    "sasp_score",
}

GENETIC_TYPES = {
    "genetic_association",
    "qtl_colocalization",
    "mendelian_randomization",
    "causal_grade",
}


def classify_evidence_level(row: dict[str, Any]) -> tuple[str, float, str]:
    explicit_level = str(row.get("evidence_level") or "").strip()
    if explicit_level in EVIDENCE_LEVELS:
        meta = EVIDENCE_LEVELS[explicit_level]
        return explicit_level, float(meta["weight"]), str(row.get("evidence_basis") or meta["basis"])

    evidence_type = str(row.get("evidence_type") or "").strip()
    module_version = str(row.get("module_version") or "").lower()
    limitation = str(row.get("limitation") or "").lower()
    artifact_path = str(row.get("artifact_path") or "").lower()

    if evidence_type in GENETIC_TYPES or "coloc" in evidence_type or "mendelian" in evidence_type:
        return _level("L4_genetic", row)
    if evidence_type in OMICS_TYPES or module_version.startswith(("bulk_", "deg_", "scrna_")):
        return _level("L3_omics", row)
    if evidence_type == "fulltext_literature" or "fulltext" in module_version or artifact_path.endswith((".pdf", ".txt", ".xml", ".nxml")):
        return _level("L1_fulltext", row)
    if evidence_type in DATABASE_TYPES or "database" in module_version or "adapter" in module_version:
        return _level("L2_database", row)
    negated_experimental = "not experimental" in limitation or "no experimental" in limitation or "without experimental" in limitation
    if "experimental" in evidence_type or ((("experiment" in limitation or "figure" in limitation or "table" in limitation) and not negated_experimental)):
        return _level("L5_experimental", row)
    if evidence_type == "literature_validation":
        return _level("L0_abstract", row)
    return _level("L2_database", row)


def level_weight(level: str) -> float:
    return float(EVIDENCE_LEVELS.get(level, EVIDENCE_LEVELS["L2_database"])["weight"])


def _level(level: str, row: dict[str, Any]) -> tuple[str, float, str]:
    meta = EVIDENCE_LEVELS[level]
    return level, float(meta["weight"]), str(row.get("evidence_basis") or meta["basis"])
