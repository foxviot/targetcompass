from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .schema_validation import load_schema, validate_object
from .screening import metadata_quality, validate_bulk_files
from .validators import load_dataset_card


EVIDENCE_PLAN_SCHEMA_VERSION = "v0.1.evidence_plan"
DATASET_PROFILE_SCHEMA_VERSION = "v0.1.dataset_profile"
DATASET_FEASIBILITY_SCHEMA_VERSION = "v0.1.dataset_feasibility_report"
METHOD_CONTRACT_SCHEMA_VERSION = "v0.1.method_contract"
COMPATIBILITY_SCHEMA_VERSION = "v0.1.compatibility_decision"


METHOD_CONTRACTS: list[dict[str, Any]] = [
    {
        "schema_version": METHOD_CONTRACT_SCHEMA_VERSION,
        "method_id": "bulk_deg_limma_or_countlike_v1",
        "method_name": "bulk DEG with matrix-aware runner",
        "data_modality": "bulk_expression",
        "purpose": "Estimate case-control expression changes from bulk RNA-seq or microarray-like matrices.",
        "requires": {"matrix_available": True, "metadata_columns": ["sample_id", "group"], "min_case_n": 2, "min_control_n": 2},
        "reject_if": ["no expression matrix", "no metadata", "no case/control labels", "sample IDs do not align"],
        "outputs": ["deg_results.tsv", "qc_summary.json", "run_manifest.json", "executor_manifest.json"],
        "qc_checks": ["sample alignment", "case/control labels", "gene identity", "design rank", "p-value distribution"],
        "evidence_type": "bulk_deg",
        "claim_limit": "association-level differential expression evidence",
        "runner": "targetcompass_lite.deg.run_deg",
    },
    {
        "schema_version": METHOD_CONTRACT_SCHEMA_VERSION,
        "method_id": "scrna_pseudobulk_deg_v1",
        "method_name": "donor-aware scRNA/snRNA pseudobulk DEG",
        "data_modality": "single_cell_expression",
        "purpose": "Aggregate cells by biological replicate and cell type before differential expression.",
        "requires": {"matrix_available": True, "metadata_columns": ["cell_id", "donor_id", "group", "cell_type"], "min_case_n": 2, "min_control_n": 2},
        "reject_if": ["no donor/sample identifier", "no group labels", "no cell type annotation", "cells treated as replicates"],
        "outputs": ["pseudobulk_matrix.tsv", "pseudobulk_metadata.tsv", "donor_group_qc.tsv", "group_qc.tsv", "run_manifest.json"],
        "qc_checks": ["cell-to-donor mapping", "donor counts per group", "cell counts per donor", "no cell-level pseudoreplication"],
        "evidence_type": "scrna_pseudobulk",
        "claim_limit": "cell-type resolved expression evidence if donor-level replication is adequate",
        "runner": "targetcompass_lite.scrna.run_scrna_pseudobulk",
    },
    {
        "schema_version": METHOD_CONTRACT_SCHEMA_VERSION,
        "method_id": "sasp_score_from_deg_v1",
        "method_name": "SASP score from reviewed DEG outputs",
        "data_modality": "deg_results",
        "purpose": "Score overlap and directionality against a configurable SASP/senescence program.",
        "requires": {"upstream_evidence": ["bulk_deg", "scrna_pseudobulk"], "columns": ["gene_symbol", "logFC", "adj_p_value", "direction"]},
        "reject_if": ["no DEG artifact", "missing gene symbols", "no direction/effect column"],
        "outputs": ["sasp_gene_scores.tsv", "sasp_dataset_scores.tsv", "run_manifest.json"],
        "qc_checks": ["SASP gene set snapshot", "directionality", "source DEG artifact present"],
        "evidence_type": "sasp_score",
        "claim_limit": "SASP phenotype/program evidence, not causal proof",
        "runner": "targetcompass_lite.sasp_score.run_sasp_score",
    },
    {
        "schema_version": METHOD_CONTRACT_SCHEMA_VERSION,
        "method_id": "surface_secretome_annotation_v1",
        "method_name": "surface/secretome accessibility annotation",
        "data_modality": "gene_symbols",
        "purpose": "Map candidate genes to surface, secreted, ECD, plasma membrane, or accessibility annotations.",
        "requires": {"gene_symbols": True, "knowledge_sources": ["UniProt", "HPA", "surfaceome", "custom tables"]},
        "reject_if": ["no candidate genes", "no annotation source"],
        "outputs": ["accessibility_annotation.tsv", "unknown_review.tsv"],
        "qc_checks": ["source snapshot", "unknown values preserved", "annotation limitations recorded"],
        "evidence_type": "surface_marker_annotation",
        "claim_limit": "annotation-level target accessibility evidence",
        "runner": "targetcompass_lite.annotation.annotate_project",
    },
    {
        "schema_version": METHOD_CONTRACT_SCHEMA_VERSION,
        "method_id": "cell_type_evidence_v1",
        "method_name": "cell-type evidence integration",
        "data_modality": "gene_symbols_or_single_cell",
        "purpose": "Link candidate genes to cell/tissue context using HPA, marker databases, scRNA outputs, or full-text extraction.",
        "requires": {"gene_symbols": True, "sources": ["HPA", "PanglaoDB", "CellMarker", "scRNA pseudobulk", "fulltext"]},
        "reject_if": ["no candidate genes", "no cell-type source"],
        "outputs": ["cell_type_evidence.tsv", "cell_type_summary.json"],
        "qc_checks": ["source provenance", "cell type normalization", "claim limitation retained"],
        "evidence_type": "cell_type_expression",
        "claim_limit": "cell/tissue context evidence, source-dependent confidence",
        "runner": "targetcompass_lite.cell_type_evidence.build_cell_type_evidence",
    },
]


def build_evidence_plan(project_dir: Path) -> dict[str, Any]:
    spec = json.loads((project_dir / "research_spec.json").read_text(encoding="utf-8"))
    routes = {str(item).lower() for item in spec.get("target_routes", [])}
    required = {str(item).lower() for item in spec.get("modalities_mvp", {}).get("required", [])}
    optional = {str(item).lower() for item in spec.get("modalities_mvp", {}).get("optional", [])}
    cells = [str(item).lower() for item in spec.get("priority_cells", [])]
    theme = " ".join(
        [
            spec.get("research_theme", ""),
            spec.get("disease_scope", {}).get("canonical", ""),
            " ".join(spec.get("priority_tissues", [])),
            " ".join(spec.get("target_routes", [])),
        ]
    ).lower()
    needs_cell_type = bool(cells) or "single_cell" in required or "scrna" in theme or "cell" in theme or "细胞" in theme
    needs_sasp = "sasp" in theme or "senescence" in theme or "衰老" in theme
    needs_surface = bool(routes & {"surface", "secreted", "ecd", "plasma_membrane", "cell_surface"}) or "secret" in theme or "surface" in theme
    needs_cross_dataset = "meta" in optional or "bulk_expression" in required or "cross" in theme
    needs_literature = True
    needs_pathway = "enrichment" in optional or "pathway" in theme
    causal_requirement = str(spec.get("constraints", {}).get("causal_requirement", "")).lower()
    needs_causal = causal_requirement in {"required", "mandatory"} or "genetic" in required

    plan = {
        "schema_version": EVIDENCE_PLAN_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "research_question": spec.get("research_theme") or spec.get("goal", ""),
        "evidence_axes": {
            "disease_relevant_expression": True,
            "cell_type_specificity": needs_cell_type,
            "condition_upregulation": True,
            "SASP_annotation": needs_sasp,
            "secreted_or_surface_annotation": needs_surface,
            "cross_dataset_validation": needs_cross_dataset,
            "literature_support": needs_literature,
            "pathway_enrichment": needs_pathway,
            "causal_or_genetic_support": needs_causal,
        },
        "preferred_data": _preferred_data(spec, needs_cell_type, needs_surface),
        "minimum_evidence_for_candidate": _minimum_evidence(needs_sasp, needs_surface, needs_cell_type),
        "not_required_now": _not_required_now(needs_causal),
        "generated_by": "rule_based_evidence_plan_v0",
    }
    _validate_payload(plan, "evidence_plan.schema.json", "EvidencePlan")
    out_dir = _out_dir(project_dir)
    (out_dir / "evidence_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return plan


def build_dataset_profiles(project_dir: Path, selected_ids: set[str] | None = None) -> dict[str, Any]:
    profiles = []
    feasibility_reports = []
    for card_path in sorted((project_dir / "dataset_cards").glob("*.yaml")):
        if selected_ids is not None and card_path.stem not in selected_ids:
            continue
        card = load_dataset_card(card_path)
        profile = profile_dataset(project_dir, card)
        report = feasibility_report(project_dir, card, profile)
        profiles.append(profile)
        feasibility_reports.append(report)
    payload = {
        "schema_version": "v0.1.dataset_profile_index",
        "project_id": project_dir.name,
        "profile_count": len(profiles),
        "profiles": profiles,
        "feasibility_reports": feasibility_reports,
    }
    out_dir = _out_dir(project_dir)
    (out_dir / "dataset_profiles.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_profile_tsv(out_dir / "dataset_profiles.tsv", profiles)
    _write_feasibility_tsv(out_dir / "dataset_feasibility.tsv", feasibility_reports)
    return payload


def build_method_contracts(project_dir: Path) -> dict[str, Any]:
    contracts = [dict(row) for row in METHOD_CONTRACTS]
    for contract in contracts:
        _validate_payload(contract, "method_contract.schema.json", "MethodContract")
    payload = {
        "schema_version": "v0.1.method_contract_index",
        "project_id": project_dir.name,
        "method_count": len(contracts),
        "methods": contracts,
    }
    out_dir = _out_dir(project_dir)
    (out_dir / "method_contracts.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_method_contract_tsv(out_dir / "method_contracts.tsv", contracts)
    return payload


def build_compatibility_decisions(project_dir: Path, selected_ids: set[str] | None = None) -> dict[str, Any]:
    cached = _load_current_compatibility(project_dir, selected_ids)
    if cached:
        return cached
    profiles_payload = build_dataset_profiles(project_dir, selected_ids)
    contracts_payload = build_method_contracts(project_dir)
    decisions = []
    for profile in profiles_payload["profiles"]:
        for contract in contracts_payload["methods"]:
            decision = compatibility_decision(project_dir, profile, contract)
            decisions.append(decision)
    payload = {
        "schema_version": "v0.1.compatibility_decision_index",
        "project_id": project_dir.name,
        "decision_count": len(decisions),
        "summary": _compatibility_summary(decisions),
        "decisions": decisions,
    }
    out_dir = _out_dir(project_dir)
    (out_dir / "compatibility_decisions.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_compatibility_tsv(out_dir / "compatibility_decisions.tsv", decisions)
    return payload


def build_evidence_planning_bundle(project_dir: Path, selected_ids: set[str] | None = None) -> dict[str, Any]:
    plan = build_evidence_plan(project_dir)
    profiles = build_dataset_profiles(project_dir, selected_ids)
    methods = build_method_contracts(project_dir)
    compatibility = _build_compatibility_from_payloads(project_dir, profiles, methods)
    bundle = {
        "schema_version": "v0.1.evidence_planning_bundle",
        "project_id": project_dir.name,
        "evidence_plan": "results/evidence_planning/evidence_plan.json",
        "dataset_profiles": "results/evidence_planning/dataset_profiles.json",
        "method_contracts": "results/evidence_planning/method_contracts.json",
        "compatibility_decisions": "results/evidence_planning/compatibility_decisions.json",
        "profile_count": profiles["profile_count"],
        "method_count": methods["method_count"],
        "compatibility_summary": compatibility["summary"],
        "feasibility_summary": _feasibility_summary(profiles["feasibility_reports"]),
        "evidence_axes": plan["evidence_axes"],
    }
    out_dir = _out_dir(project_dir)
    (out_dir / "planning_bundle.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    return bundle


def _load_current_compatibility(project_dir: Path, selected_ids: set[str] | None) -> dict[str, Any]:
    if selected_ids is not None:
        return {}
    path = _out_dir(project_dir) / "compatibility_decisions.json"
    if not path.exists():
        return {}
    try:
        output_mtime = path.stat().st_mtime
        if any(input_path.stat().st_mtime > output_mtime for input_path in _planning_input_paths(project_dir)):
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if payload.get("schema_version") != "v0.1.compatibility_decision_index":
        return {}
    if payload.get("project_id") != project_dir.name:
        return {}
    return payload


def _planning_input_paths(project_dir: Path) -> list[Path]:
    paths = []
    spec = project_dir / "research_spec.json"
    if spec.exists():
        paths.append(spec)
    for card_path in sorted((project_dir / "dataset_cards").glob("*.yaml")):
        paths.append(card_path)
        try:
            card = load_dataset_card(card_path)
        except Exception:
            continue
        for value in (card.get("file_paths", {}) or {}).values():
            data_path = _resolve(project_dir, str(value or ""))
            if data_path.exists():
                paths.append(data_path)
    return paths


def _build_compatibility_from_payloads(project_dir: Path, profiles_payload: dict[str, Any], contracts_payload: dict[str, Any]) -> dict[str, Any]:
    decisions = []
    for profile in profiles_payload["profiles"]:
        for contract in contracts_payload["methods"]:
            decision = compatibility_decision(project_dir, profile, contract)
            decisions.append(decision)
    payload = {
        "schema_version": "v0.1.compatibility_decision_index",
        "project_id": project_dir.name,
        "decision_count": len(decisions),
        "summary": _compatibility_summary(decisions),
        "decisions": decisions,
    }
    out_dir = _out_dir(project_dir)
    (out_dir / "compatibility_decisions.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_compatibility_tsv(out_dir / "compatibility_decisions.tsv", decisions)
    return payload


def compatibility_decision(project_dir: Path, profile: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    method_id = contract["method_id"]
    matched: list[str] = []
    unmet: list[str] = []
    warnings: list[str] = []
    params: dict[str, Any] = {}
    next_actions: list[str] = []
    assay = profile.get("assay", "")
    readiness = profile.get("analysis_readiness", "")

    if method_id == "bulk_deg_limma_or_countlike_v1":
        if assay != "bulk_expression":
            return _decision(project_dir, profile, contract, "fail", [], [f"dataset assay is {assay}, not bulk_expression"], [], {}, ["choose a non-bulk or descriptive method"])
        if readiness != "ready":
            unmet.append(f"dataset analysis_readiness is {readiness}")
        else:
            matched.append("bulk dataset is ready")
        if profile.get("matrix_type") in {"raw_or_count_like", "normalized_or_log_expression"}:
            matched.append(f"matrix type supported: {profile.get('matrix_type')}")
        else:
            unmet.append(f"unsupported or missing matrix type: {profile.get('matrix_type')}")
        if profile.get("group_column"):
            matched.append("group column available")
        else:
            unmet.append("group column is missing")
        if int(profile.get("case_count") or 0) >= 2 and int(profile.get("control_count") or 0) >= 2:
            matched.append("case/control replicate counts meet minimum")
        else:
            unmet.append("case/control replicate counts are below minimum")
        if int(profile.get("case_count") or 0) < 3 or int(profile.get("control_count") or 0) < 3:
            warnings.append("small sample size; report as limited evidence")
        if profile.get("batch_column"):
            params["design_formula"] = f"~ {profile['batch_column']} + {profile.get('group_column', 'group')}"
        else:
            params["design_formula"] = f"~ {profile.get('group_column', 'group') or 'group'}"
        params["contrast"] = [profile.get("group_column", "group") or "group", profile.get("case_label", ""), profile.get("control_label", "")]
        next_actions = ["compile bulk DEG task packet", "run execution/data/statistical QC", "write bulk_deg EvidenceItem after review"]
        status = "pass" if not unmet else "repairable" if any("group column" in item or "readiness" in item for item in unmet) else "fail"
        return _decision(project_dir, profile, contract, status, matched, unmet, warnings, params, next_actions)

    if method_id == "scrna_pseudobulk_deg_v1":
        if assay not in {"single_cell_expression", "scrna", "snrna"}:
            return _decision(project_dir, profile, contract, "fail", [], [f"dataset assay is {assay}, not single_cell_expression"], [], {}, ["use only if scRNA/snRNA count matrix and metadata are supplied"])
        for field, label in [("donor_column", "donor/sample identifier"), ("group_column", "group labels"), ("cell_type_column", "cell type annotation")]:
            if profile.get(field):
                matched.append(f"{label} available")
            else:
                unmet.append(f"{label} missing")
        if profile.get("matrix_type") == "unavailable":
            unmet.append("count matrix is unavailable")
        else:
            matched.append("matrix artifact is available")
        params = {
            "donor_column": profile.get("donor_column", "donor_id"),
            "group_column": profile.get("group_column", "group"),
            "cell_type_column": profile.get("cell_type_column", "cell_type"),
        }
        status = "pass" if not unmet else "repairable" if "missing" in " ".join(unmet) else "fail"
        return _decision(project_dir, profile, contract, status, matched, unmet, warnings, params, ["compile one task per cell type and contrast"])

    if method_id == "sasp_score_from_deg_v1":
        deg_ready = _dataset_has_deg_artifact(project_dir, profile.get("dataset_id", ""))
        if deg_ready:
            matched.append("dataset-bound upstream DEG artifact is available")
            status = "pass"
        elif assay in {"bulk_expression", "single_cell_expression"} and readiness == "ready":
            warnings.append("SASP scoring requires DEG to run first")
            status = "repairable"
            unmet.append("upstream DEG artifact not generated yet")
        else:
            status = "fail"
            unmet.append("no compatible upstream expression analysis")
        return _decision(project_dir, profile, contract, status, matched, unmet, warnings, {"source_artifact": "deg_results.tsv"}, ["run DEG first", "then compute SASP score"])

    if method_id == "surface_secretome_annotation_v1":
        if profile.get("analysis_readiness") in {"ready", "exploratory_only"}:
            matched.append("candidate genes can be annotated after expression or literature evidence")
            status = "pass"
        else:
            warnings.append("annotation can run after candidate gene list exists")
            status = "repairable"
            unmet.append("candidate gene list is not ready")
        return _decision(project_dir, profile, contract, status, matched, unmet, warnings, {"annotation_routes": ["secreted", "surface", "ECD", "plasma_membrane"]}, ["run after candidate gene extraction"])

    if method_id == "cell_type_evidence_v1":
        if profile.get("cell_type_column"):
            matched.append("dataset metadata includes cell type context")
            status = "pass"
        elif profile.get("analysis_readiness") in {"ready", "exploratory_only"}:
            warnings.append("cell-type evidence requires HPA/marker DB/fulltext or scRNA metadata")
            status = "repairable"
            unmet.append("direct cell-type annotation not present in dataset profile")
        else:
            status = "fail"
            unmet.append("no candidate genes or cell-type source available")
        return _decision(project_dir, profile, contract, status, matched, unmet, warnings, {"sources": ["HPA", "PanglaoDB", "CellMarker", "scRNA", "fulltext"]}, ["collect cell-type source and write cell_type_evidence"])

    return _decision(project_dir, profile, contract, "needs_manual_review", [], ["unknown method contract"], [], {}, ["manual method review"])


def profile_dataset(project_dir: Path, card: dict[str, Any]) -> dict[str, Any]:
    fields = list(card.get("metadata_fields", []) or [])
    paths = card.get("file_paths", {}) or {}
    matrix_path = _resolve(project_dir, paths.get("expression_matrix", ""))
    metadata_path = _resolve(project_dir, paths.get("metadata", ""))
    metadata_info = _metadata_profile(metadata_path)
    matrix_type = _matrix_type(card, matrix_path)
    summary = card.get("sample_summary", {}) or {}
    profile = {
        "schema_version": DATASET_PROFILE_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "dataset_id": card.get("dataset_id", ""),
        "source": card.get("source", ""),
        "accession": card.get("accession", ""),
        "species": card.get("organism", ""),
        "tissue": card.get("tissue", ""),
        "assay": card.get("modality", "unknown"),
        "platform": card.get("platform", ""),
        "matrix_type": matrix_type,
        "sample_count": int(summary.get("case_n") or 0) + int(summary.get("control_n") or 0),
        "case_count": int(summary.get("case_n") or 0),
        "control_count": int(summary.get("control_n") or 0),
        "group_column": "group" if "group" in fields else "",
        "case_label": (card.get("contrast", {}) or {}).get("case", ""),
        "control_label": (card.get("contrast", {}) or {}).get("control", ""),
        "batch_column": _first_present(fields, ["batch", "Batch", "batch_id"]),
        "sex_column": _first_present(fields, ["sex", "gender"]),
        "donor_column": _first_present(fields, ["donor_id", "patient_id", "subject_id", "sample_id"]),
        "cell_type_column": _first_present(fields, ["cell_type", "cell type", "celltype", "annotation"]),
        "replicate_level": _replicate_level(fields, card),
        "metadata_fields": fields,
        "metadata_quality": metadata_quality(card, project_dir).get("label", "not_applicable") if card.get("modality") == "bulk_expression" else _metadata_quality_label(metadata_info),
        "download_status": "available" if card.get("matrix_available") and matrix_path.exists() else "metadata_only" if metadata_path.exists() else "unavailable",
        "analysis_readiness": _analysis_readiness(card, project_dir, matrix_path, metadata_path),
        "risk_flags": _risk_flags(card, matrix_path, metadata_path, metadata_info),
        "file_paths": paths,
    }
    _validate_payload(profile, "dataset_profile.schema.json", "DatasetProfile")
    return profile


def feasibility_report(project_dir: Path, card: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    matched = []
    unmet = []
    warnings = list(profile.get("risk_flags", []))
    recommended = list(card.get("recommended_use", []) or [])
    blocked = list(card.get("blocked_use", []) or [])
    modality = card.get("modality", "")
    if card.get("license_status") in {"public", "authorized"}:
        matched.append("license allows local analysis")
    else:
        unmet.append("license is not public or authorized")
    if card.get("matrix_available"):
        matched.append("matrix is declared available")
    else:
        unmet.append("matrix is not available")
    if modality == "bulk_expression":
        file_errors = validate_bulk_files(card, project_dir)
        if file_errors:
            unmet.extend(file_errors)
        else:
            matched.append("bulk expression matrix and metadata pass file checks")
        if profile.get("case_count", 0) < 2 or profile.get("control_count", 0) < 2:
            unmet.append("case/control biological replicate count is too low")
        elif profile.get("case_count", 0) < 3 or profile.get("control_count", 0) < 3:
            warnings.append("small sample size; formal DEG allowed with limitations")
        else:
            matched.append("case/control replicate counts are usable")
    else:
        warnings.append(f"{modality} is not a primary formal DEG modality in the local bundle")
    if not unmet and modality == "bulk_expression":
        decision = "pass"
        next_actions = ["compile bulk DEG WorkOrder", "run expression QC", "write DEG evidence after review"]
    elif card.get("matrix_available") and "metadata" in " ".join(unmet).lower():
        decision = "needs_metadata_repair"
        next_actions = ["repair or map metadata columns", "rerun DatasetProfile", "recheck method compatibility"]
    elif modality != "bulk_expression" and card.get("license_status") in {"public", "authorized"}:
        decision = "exploratory_only"
        next_actions = ["use as descriptive or literature/context evidence", "do not claim formal differential expression"]
    else:
        decision = "fail"
        next_actions = ["exclude from formal analysis unless missing inputs are supplied"]
    report = {
        "schema_version": DATASET_FEASIBILITY_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "dataset_id": card.get("dataset_id", ""),
        "decision": decision,
        "matched_requirements": matched,
        "unmet_requirements": _dedupe(unmet),
        "warnings": _dedupe(warnings),
        "recommended_uses": recommended,
        "blocked_uses": blocked,
        "next_actions": next_actions,
    }
    _validate_payload(report, "dataset_feasibility_report.schema.json", "DatasetFeasibilityReport")
    return report


def _preferred_data(spec: dict[str, Any], needs_cell_type: bool, needs_surface: bool) -> list[str]:
    tissues = ", ".join(spec.get("priority_tissues", [])) or "target tissue"
    data = []
    if needs_cell_type:
        data.append(f"scRNA/snRNA-seq case-control data from {tissues} with sample_id, group, donor_id, and cell_type metadata")
    data.append(f"bulk RNA-seq or microarray case-control expression data from {tissues}")
    if needs_surface:
        data.append("protein localization / secretome / surfaceome annotation for candidate genes")
    data.append("literature or full-text evidence with methods, samples, cell types, and result sentences")
    return data


def _minimum_evidence(needs_sasp: bool, needs_surface: bool, needs_cell_type: bool) -> list[str]:
    out = ["disease- or condition-relevant expression signal", "usable dataset with explicit metadata and QC"]
    if needs_sasp:
        out.append("SASP or senescence-program annotation/support")
    if needs_surface:
        out.append("secreted, surface, ECD, or plasma-membrane accessibility support")
    if needs_cell_type:
        out.append("cell-type or tissue-context localization evidence")
    return out


def _not_required_now(needs_causal: bool) -> list[dict[str, str]]:
    if needs_causal:
        return []
    return [
        {
            "method": "GWAS/QTL/coloc/MR",
            "reason": "Current ResearchSpec does not require genetic causality; expression and accessibility evidence remain association-level.",
        }
    ]


def _metadata_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "fields": [], "row_count": 0}
    try:
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            rows = list(reader)
            return {"exists": True, "fields": reader.fieldnames or [], "row_count": len(rows)}
    except Exception as exc:
        return {"exists": True, "fields": [], "row_count": 0, "error": str(exc)}


def _matrix_type(card: dict[str, Any], matrix_path: Path) -> str:
    if not matrix_path.exists():
        return "unavailable"
    modality = str(card.get("modality", "")).lower()
    if modality == "microarray":
        return "normalized_expression"
    try:
        with matrix_path.open(encoding="utf-8") as f:
            header = f.readline().strip().split("\t")
            first = f.readline().strip().split("\t")
        values = [float(item) for item in first[1: min(len(first), 8)] if item not in {"", "NA"}]
        if values and all(float(v).is_integer() and v >= 0 for v in values):
            return "raw_or_count_like"
        if values:
            return "normalized_or_log_expression"
    except Exception:
        pass
    return "unknown_expression_matrix"


def _analysis_readiness(card: dict[str, Any], project_dir: Path, matrix_path: Path, metadata_path: Path) -> str:
    if not matrix_path.exists():
        return "not_ready"
    if not metadata_path.exists():
        return "needs_metadata_repair"
    if card.get("modality") == "bulk_expression" and validate_bulk_files(card, project_dir):
        return "needs_metadata_repair"
    if card.get("modality") == "bulk_expression":
        return "ready"
    return "exploratory_only"


def _risk_flags(card: dict[str, Any], matrix_path: Path, metadata_path: Path, metadata_info: dict[str, Any]) -> list[str]:
    flags = list(card.get("known_limitations", []) or [])
    if not matrix_path.exists():
        flags.append("expression matrix is missing")
    if not metadata_path.exists():
        flags.append("metadata file is missing")
    fields = set(metadata_info.get("fields", []))
    if card.get("modality") == "bulk_expression":
        for field in ["sample_id", "group"]:
            if field not in fields:
                flags.append(f"metadata missing {field}")
        if not ({"batch", "sex", "age", "donor_id", "patient_id"} & fields):
            flags.append("metadata has limited covariates")
    if card.get("license_status") not in {"public", "authorized"}:
        flags.append("license requires review")
    return _dedupe(flags)


def _metadata_quality_label(metadata_info: dict[str, Any]) -> str:
    if not metadata_info.get("exists"):
        return "missing"
    fields = set(metadata_info.get("fields", []))
    if {"sample_id", "group", "donor_id", "cell_type"}.issubset(fields):
        return "high"
    if {"sample_id", "group"}.issubset(fields):
        return "medium"
    return "low"


def _replicate_level(fields: list[str], card: dict[str, Any]) -> str:
    if "donor_id" in fields or "patient_id" in fields or int((card.get("sample_summary", {}) or {}).get("donor_n") or 0) > 0:
        return "biological_replicate"
    if "sample_id" in fields:
        return "sample_level_unknown_donor"
    return "unknown"


def _first_present(fields: list[str], candidates: list[str]) -> str:
    lowered = {field.lower(): field for field in fields}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return ""


def _resolve(project_dir: Path, value: str) -> Path:
    path = Path(value or "")
    return path if path.is_absolute() else project_dir / path


def _out_dir(project_dir: Path) -> Path:
    path = project_dir / "results" / "evidence_planning"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_profile_tsv(path: Path, profiles: list[dict[str, Any]]) -> None:
    fields = ["dataset_id", "source", "accession", "assay", "matrix_type", "sample_count", "metadata_quality", "analysis_readiness", "risk_flags"]
    _write_tsv(path, profiles, fields)


def _write_feasibility_tsv(path: Path, reports: list[dict[str, Any]]) -> None:
    fields = ["dataset_id", "decision", "matched_requirements", "unmet_requirements", "warnings", "recommended_uses", "next_actions"]
    _write_tsv(path, reports, fields)


def _write_method_contract_tsv(path: Path, contracts: list[dict[str, Any]]) -> None:
    fields = ["method_id", "method_name", "data_modality", "purpose", "evidence_type", "claim_limit", "runner"]
    _write_tsv(path, contracts, fields)


def _write_compatibility_tsv(path: Path, decisions: list[dict[str, Any]]) -> None:
    fields = ["dataset_id", "method_id", "decision", "matched_requirements", "unmet_requirements", "warnings", "recommended_parameters", "next_actions", "claim_limit"]
    _write_tsv(path, decisions, fields)


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _cell(row.get(field, "")) for field in fields})


def _cell(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _feasibility_summary(reports: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in reports:
        decision = row.get("decision", "unknown")
        summary[decision] = summary.get(decision, 0) + 1
    return summary


def _compatibility_summary(decisions: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in decisions:
        decision = row.get("decision", "unknown")
        summary[decision] = summary.get(decision, 0) + 1
    return summary


def _decision(
    project_dir: Path,
    profile: dict[str, Any],
    contract: dict[str, Any],
    decision: str,
    matched: list[str],
    unmet: list[str],
    warnings: list[str],
    params: dict[str, Any],
    next_actions: list[str],
) -> dict[str, Any]:
    payload = {
        "schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "dataset_id": profile.get("dataset_id", ""),
        "method_id": contract.get("method_id", ""),
        "decision": decision,
        "matched_requirements": _dedupe(matched),
        "unmet_requirements": _dedupe(unmet),
        "warnings": _dedupe(warnings + list(profile.get("risk_flags", []) or [])),
        "recommended_parameters": params,
        "next_actions": next_actions,
        "claim_limit": contract.get("claim_limit", ""),
    }
    _validate_payload(payload, "compatibility_decision.schema.json", "CompatibilityDecision")
    return payload


def _project_has_artifact(project_dir: Path, pattern: str) -> bool:
    return any(path.exists() for path in project_dir.glob(pattern))


def _dataset_has_deg_artifact(project_dir: Path, dataset_id: str) -> bool:
    if not dataset_id:
        return False
    patterns = [
        f"results/bulk_deg_{dataset_id}/deg_results.tsv",
        f"results/scrna_pseudobulk_{dataset_id}/deg_results.tsv",
        f"results/*{dataset_id}*/deg_results.tsv",
    ]
    return any(_project_has_artifact(project_dir, pattern) for pattern in patterns)


def _validate_payload(payload: dict[str, Any], schema_name: str, label: str) -> None:
    errors = validate_object(payload, load_schema(schema_name), label)
    if errors:
        raise ValueError("; ".join(errors))


def _dedupe(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out
