from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import now_iso


RESOURCE_GATE_SCHEMA_VERSION = "v5.resource_gate/0.1"


def build_resource_gate_report(project_dir: str | Path, bundle: dict[str, Any] | None = None, *, write: bool = True) -> dict[str, Any]:
    project_dir = Path(project_dir)
    bundle = bundle or _read_json(project_dir / "v5" / "resource_discovery" / "resource_discovery_bundle.json", {})
    corrections = _load_manual_corrections(project_dir)
    candidates = bundle.get("resource_candidates", [])
    profiles = bundle.get("dataset_profiles", [])
    decisions = bundle.get("dataset_selection_decisions", [])
    profiles_by_candidate = {row.get("resource_candidate_id", ""): row for row in profiles}
    gate_items = []
    for candidate in candidates:
        candidate_id = candidate.get("resource_candidate_id", "")
        profile = _apply_correction_to_profile(profiles_by_candidate.get(candidate_id, {}), corrections.get(candidate_id, {}))
        gate_items.append(_gate_candidate(project_dir, candidate, profile, corrections.get(candidate_id, {})))
    manual_items = [item for item in gate_items if item["manual_action_required"]]
    payload = {
        "schema_version": RESOURCE_GATE_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "resource_discovery_ref": "v5/resource_discovery/resource_discovery_bundle.json",
        "candidate_count": len(candidates),
        "dataset_profile_count": len(profiles),
        "decision_count": len(decisions),
        "verified_metadata_count": sum(1 for item in candidates if item.get("verified") is True),
        "analysis_ready_count": sum(1 for item in gate_items if item["gate_status"] in {"analysis_ready_after_review", "datasets_locked_ready"}),
        "datasets_lockable_count": sum(1 for item in gate_items if item["can_enter_datasets_locked"] is True),
        "matrix_parse_ready_count": sum(1 for item in gate_items if item.get("matrix_parse_ready") is True),
        "manual_review_count": len(manual_items),
        "gate_items": gate_items,
        "human_correction_queue": manual_items,
        "manual_correction_count": len(corrections),
        "manual_correction_ref": "v5/resource_discovery/resource_manual_corrections.jsonl",
        "verified_gate_policy": {
            "metadata_verified": "candidate may proceed to human review",
            "analysis_ready_after_review": "metadata has accession/title plus dataset profile, but still requires grouping/raw-data confirmation before DATASETS_LOCKED",
            "blocked": "cannot be locked or used for analysis until correction is supplied",
        },
    }
    if write:
        out = project_dir / "v5" / "resource_discovery" / "resource_gate_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def apply_resource_manual_correction(
    project_dir: str | Path,
    resource_candidate_id: str,
    *,
    group_metadata_status: str = "",
    sample_size_status: str = "",
    organism: str = "",
    tissue: str = "",
    platform: str = "",
    group_column: str = "",
    case_label: str = "",
    control_label: str = "",
    sample_count: str = "",
    notes: str = "",
    actor: str = "human",
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    path = project_dir / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "v5.resource_manual_correction/0.1",
        "project_id": project_dir.name,
        "resource_candidate_id": resource_candidate_id,
        "group_metadata_status": group_metadata_status,
        "sample_size_status": sample_size_status,
        "organism": organism,
        "tissue": tissue,
        "platform": platform,
        "group_column": group_column,
        "case_label": case_label,
        "control_label": control_label,
        "sample_count": sample_count,
        "notes": notes,
        "actor": actor,
        "created_at": now_iso(),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def apply_suggested_resource_corrections(
    project_dir: str | Path,
    *,
    actor: str = "human_batch_accept_suggested",
    require_complete: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Append human-accepted correction rows from complete metadata suggestions.

    This is intentionally not automatic dataset locking. It records an explicit
    actor and only accepts suggestions that already satisfy required fields when
    ``require_complete`` is true.
    """

    project_dir = Path(project_dir)
    report = build_resource_gate_report(project_dir, write=False)
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in report.get("gate_items", []):
        if item.get("resource_type") != "dataset":
            continue
        if item.get("manual_correction_applied"):
            skipped.append({"resource_candidate_id": item.get("resource_candidate_id", ""), "reason": "manual_correction_already_exists"})
            continue
        suggestion = item.get("suggested_manual_correction") or {}
        if not suggestion:
            skipped.append({"resource_candidate_id": item.get("resource_candidate_id", ""), "reason": "no_suggested_manual_correction"})
            continue
        missing = _missing_after_suggestion(item, suggestion)
        if require_complete and missing:
            skipped.append({"resource_candidate_id": item.get("resource_candidate_id", ""), "reason": "suggestion_incomplete", "missing_required_fields": missing})
            continue
        accepted.append(
            apply_resource_manual_correction(
                project_dir,
                item.get("resource_candidate_id", ""),
                group_metadata_status=suggestion.get("group_metadata_status", "case_control_selected"),
                sample_size_status=suggestion.get("sample_size_status", "sufficient"),
                organism=suggestion.get("organism", item.get("organism", "")),
                tissue=suggestion.get("tissue", item.get("tissue", "")),
                platform=suggestion.get("platform", item.get("platform", "")),
                group_column=suggestion.get("group_column", ""),
                case_label=suggestion.get("case_label", ""),
                control_label=suggestion.get("control_label", ""),
                sample_count=suggestion.get("sample_count", ""),
                notes=suggestion.get("notes", "Accepted suggested public metadata correction."),
                actor=actor,
            )
        )
        if limit is not None and len(accepted) >= limit:
            break
    refreshed = build_resource_gate_report(project_dir, write=True)
    payload = {
        "schema_version": "v5.suggested_resource_correction_batch/0.1",
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "actor": actor,
        "require_complete": require_complete,
        "accepted_count": len(accepted),
        "skipped_count": len(skipped),
        "accepted": accepted,
        "skipped": skipped,
        "datasets_lockable_count": refreshed.get("datasets_lockable_count", 0),
        "resource_gate_ref": "v5/resource_discovery/resource_gate_report.json",
    }
    out = project_dir / "v5" / "resource_discovery" / "suggested_resource_correction_batch.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _missing_after_suggestion(item: dict[str, Any], suggestion: dict[str, Any]) -> list[str]:
    missing = []
    for field in ["group_column", "case_label", "control_label", "sample_count", "organism", "tissue", "platform"]:
        current = item.get("required_fields_status", {}).get(field, {}).get("value", "")
        value = suggestion.get(field) or current
        if value in {"", None, "unknown", "not_assessed"}:
            missing.append(field)
    return missing


def _gate_candidate(project_dir: Path, candidate: dict[str, Any], profile: dict[str, Any], correction: dict[str, Any] | None = None) -> dict[str, Any]:
    issues = []
    recommendations = []
    resource_type = candidate.get("resource_type", "")
    correction = correction or {}
    if candidate.get("verified") is not True:
        issues.append("metadata_not_verified")
        recommendations.append("rerun resource discovery or manually provide accession/title/source metadata")
    if str(candidate.get("accession", "")).upper().startswith(("AUTO_", "MOCK_")):
        issues.append("placeholder_accession")
        recommendations.append("replace placeholder accession with a real GEO/SRA/ArrayExpress/cellxgene identifier")
    if resource_type == "dataset":
        if not profile:
            issues.append("missing_dataset_profile")
            recommendations.append("profile dataset metadata before method compatibility")
        else:
            if profile.get("group_metadata_status") in {"not_assessed", "", None}:
                issues.append("group_metadata_not_assessed")
                recommendations.append("inspect metadata columns and select case/control/grouping fields")
            if profile.get("sample_size_status") in {"not_assessed", "", None}:
                issues.append("sample_size_not_assessed")
                recommendations.append("confirm usable sample/cell/donor counts before locking dataset")
            if profile.get("organism") in {"unknown", "", None}:
                issues.append("organism_unknown")
                recommendations.append("confirm organism matches ScopeBundle")
            if profile.get("tissue") in {"unknown", "", None}:
                issues.append("tissue_unknown")
                recommendations.append("confirm tissue/cell context matches ScopeBundle")
            if not _matrix_parse_ready(project_dir, candidate, profile, correction):
                issues.append("matrix_parse_not_ready")
                recommendations.append("verify downloadable expression matrix or upload expression_matrix.tsv and metadata.tsv before analysis")
    if resource_type == "literature":
        recommendations.append("abstract metadata is verified only for literature identity; run fulltext-literature/fulltext-llm-extract for stronger evidence")
    hard_blocked = any(item in issues for item in {"metadata_not_verified", "placeholder_accession", "missing_dataset_profile"})
    matrix_ready = _matrix_parse_ready(project_dir, candidate, profile, correction)
    dataset_lockable = resource_type == "dataset" and not hard_blocked and not issues and bool(correction)
    gate_status = "datasets_locked_ready" if dataset_lockable else ("blocked" if hard_blocked else "analysis_ready_after_review")
    required_fields = _required_field_status(project_dir, candidate, profile, correction)
    missing_required = [key for key, row in required_fields.items() if row["status"] != "ok"]
    suggested_correction = _suggest_manual_correction(profile, required_fields)
    return {
        "resource_candidate_id": candidate.get("resource_candidate_id", ""),
        "source_database": candidate.get("source_database", ""),
        "accession": candidate.get("accession", ""),
        "resource_type": resource_type,
        "modality": profile.get("modality", ""),
        "organism": profile.get("organism", ""),
        "tissue": profile.get("tissue", ""),
        "platform": profile.get("platform", ""),
        "verified": candidate.get("verified", False),
        "source_status": candidate.get("source_status", ""),
        "dataset_profile_id": profile.get("dataset_profile_id", ""),
        "gate_status": gate_status,
        "manual_action_required": bool(issues) or resource_type == "dataset",
        "blocking_issues": issues,
        "recovery_suggestions": recommendations,
        "manual_correction_applied": bool(correction),
        "manual_correction": correction,
        "suggested_manual_correction": suggested_correction,
        "required_fields_status": required_fields,
        "missing_required_fields": missing_required,
        "matrix_parse_ready": matrix_ready,
        "matrix_parse_status": "ready" if matrix_ready else "needs_matrix_validation",
        "matrix_parse_preview": _matrix_parse_preview(project_dir, candidate, profile),
        "metadata_value_preview": _metadata_value_preview(project_dir, candidate, profile),
        "next_human_action": _next_human_action(gate_status, missing_required, resource_type),
        "can_enter_datasets_locked": dataset_lockable,
        "reason": "Manual correction confirms metadata, grouping, sample size, organism, tissue, platform, and matrix readiness." if dataset_lockable else "DATASETS_LOCKED requires human review after metadata, grouping, sample size, organism, tissue, and raw-data usability are confirmed.",
    }


def _matrix_parse_ready(project_dir: Path, candidate: dict[str, Any], profile: dict[str, Any], correction: dict[str, Any] | None = None) -> bool:
    correction = correction or {}
    accession = str(candidate.get("accession", "") or profile.get("dataset_id", "")).upper()
    source = str(candidate.get("source_database", "")).lower()
    if accession:
        data_dir = project_dir / "data" / accession
        matrix_exists = (data_dir / "expression_matrix.tsv").exists()
        metadata_exists = (data_dir / "metadata.tsv").exists()
        sra_manifest_exists = (data_dir / "quantification_manifest.json").exists()
        cxg_manifest_exists = (data_dir / "cellxgene_manifest.json").exists() or any(data_dir.glob("*.h5ad"))
        if source == "sra":
            return matrix_exists and metadata_exists and sra_manifest_exists
        if source in {"cellxgene", "cz_cellxgene"}:
            return matrix_exists and metadata_exists and cxg_manifest_exists
        if source == "arrayexpress":
            return matrix_exists and metadata_exists
        if matrix_exists and metadata_exists:
            return True
    if str(correction.get("matrix_parse_ready", "")).lower() in {"true", "yes", "1", "ready"}:
        return source not in {"sra", "cellxgene", "cz_cellxgene"}
    if str(profile.get("matrix_parse_ready", "")).lower() in {"true", "yes", "1", "ready"}:
        return source not in {"sra", "cellxgene", "cz_cellxgene"}
    if source in {"sra", "cellxgene", "cz_cellxgene", "arrayexpress"}:
        return False
    return False


def _matrix_parse_preview(project_dir: Path, candidate: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    accession = str(candidate.get("accession", "") or profile.get("dataset_id", "")).upper()
    data_dir = project_dir / "data" / accession if accession else project_dir / "data"
    return {
        "accession": accession,
        "source_database": candidate.get("source_database", ""),
        "expression_matrix_exists": bool((data_dir / "expression_matrix.tsv").exists()),
        "metadata_exists": bool((data_dir / "metadata.tsv").exists()),
        "quantification_manifest_exists": bool((data_dir / "quantification_manifest.json").exists()),
        "cellxgene_manifest_exists": bool((data_dir / "cellxgene_manifest.json").exists()),
        "h5ad_export_exists": bool(any(data_dir.glob("*.h5ad"))) if data_dir.exists() else False,
        "expected_expression_matrix": str(data_dir / "expression_matrix.tsv").replace("\\", "/"),
        "expected_metadata": str(data_dir / "metadata.tsv").replace("\\", "/"),
        "expected_quantification_manifest": str(data_dir / "quantification_manifest.json").replace("\\", "/"),
        "expected_cellxgene_manifest": str(data_dir / "cellxgene_manifest.json").replace("\\", "/"),
    }


def _metadata_value_preview(project_dir: Path, candidate: dict[str, Any], profile: dict[str, Any], *, max_columns: int = 12) -> list[dict[str, Any]]:
    accession = str(candidate.get("accession", "") or profile.get("dataset_id", "")).upper()
    if not accession:
        return []
    profile_path = project_dir / "data" / accession / "metadata_profile.json"
    payload = _read_json(profile_path, {})
    columns = payload.get("columns", [])
    if not columns:
        columns = _profile_metadata_tsv(project_dir / "data" / accession / "metadata.tsv")
    rows = []
    for column in columns[:max_columns]:
        rows.append(
            {
                "name": column.get("name", ""),
                "non_empty": column.get("non_empty", 0),
                "unique_count": column.get("unique_count", 0),
                "value_counts": dict(list((column.get("value_counts") or {}).items())[:8]),
            }
        )
    return rows


def _profile_metadata_tsv(path: Path, *, max_rows: int = 500, max_columns: int = 40) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            header_line = handle.readline()
            if not header_line:
                return []
            headers = header_line.rstrip("\n\r").split("\t")[:max_columns]
            stats = [{"name": name, "non_empty": 0, "value_counts": {}} for name in headers]
            for row_index, line in enumerate(handle):
                if row_index >= max_rows:
                    break
                cells = line.rstrip("\n\r").split("\t")
                for index, stat in enumerate(stats):
                    value = cells[index].strip() if index < len(cells) else ""
                    if not value:
                        continue
                    stat["non_empty"] += 1
                    counts = stat["value_counts"]
                    if len(counts) < 25 or value in counts:
                        counts[value] = counts.get(value, 0) + 1
            for stat in stats:
                stat["unique_count"] = len(stat["value_counts"])
            return stats
    except OSError:
        return []


def _suggest_manual_correction(profile: dict[str, Any], required_fields: dict[str, dict[str, str]]) -> dict[str, str]:
    inference = profile.get("metadata_inference") or {}
    scope_suggestions = profile.get("scope_suggestions") or {}
    suggestion: dict[str, str] = {}
    for field in ["group_column", "case_label", "control_label", "sample_count", "organism", "tissue", "platform"]:
        value = _first_meaningful(
            required_fields.get(field, {}).get("value"),
            profile.get(field),
            inference.get(field),
            scope_suggestions.get(field),
        )
        if value and value not in {"unknown", "not_assessed"}:
            suggestion[field] = str(value)
    if inference.get("group_metadata_status"):
        suggestion["group_metadata_status"] = str(inference["group_metadata_status"])
    if inference.get("sample_size_status"):
        suggestion["sample_size_status"] = str(inference["sample_size_status"])
    if suggestion:
        suggestion["notes"] = "Auto-filled from public metadata inference; human review is required before DATASETS_LOCKED."
        if scope_suggestions:
            suggestion["notes"] += " Some fields may be scope suggestions rather than source metadata."
    return suggestion


def _first_meaningful(*values: Any) -> Any:
    for value in values:
        if value not in {"", None, "unknown", "not_assessed"}:
            return value
    return ""


def _required_field_status(project_dir: Path, candidate: dict[str, Any], profile: dict[str, Any], correction: dict[str, Any]) -> dict[str, dict[str, str]]:
    def status(field: str, value: Any, hint: str) -> dict[str, str]:
        ok = value not in {"", None, "unknown", "not_assessed"}
        return {"status": "ok" if ok else "missing", "value": str(value or ""), "hint": hint}

    if candidate.get("resource_type", "") != "dataset":
        return {}
    return {
        "accession": status("accession", candidate.get("accession", ""), "Use a real GEO/SRA/ArrayExpress/cellxgene accession."),
        "metadata_verified": {
            "status": "ok" if candidate.get("verified") is True else "missing",
            "value": str(candidate.get("verified", False)),
            "hint": "Rerun discovery or manually verify source metadata.",
        },
        "group_column": status("group_column", correction.get("group_column") or profile.get("group_column", ""), "Select the metadata column that separates case and control."),
        "case_label": status("case_label", correction.get("case_label") or profile.get("case_label", ""), "Select the disease/case value in the group column."),
        "control_label": status("control_label", correction.get("control_label") or profile.get("control_label", ""), "Select the control/healthy value in the group column."),
        "sample_count": status("sample_count", correction.get("sample_count") or profile.get("sample_count", ""), "Confirm usable sample/cell/donor count."),
        "organism": status("organism", profile.get("organism", ""), "Confirm organism matches the project scope."),
        "tissue": status("tissue", profile.get("tissue", ""), "Confirm tissue/cell context matches the project scope."),
        "platform": status("platform", profile.get("platform", ""), "Confirm platform and raw data usability."),
        "matrix_parse_ready": {
            "status": "ok" if _matrix_parse_ready(project_dir, candidate, profile, correction) else "missing",
            "value": str(correction.get("matrix_parse_ready") or profile.get("matrix_parse_ready") or ""),
            "hint": "Confirm downloadable/parsed expression matrix and metadata, or upload expression_matrix.tsv and metadata.tsv.",
        },
    }


def _next_human_action(gate_status: str, missing_required: list[str], resource_type: str) -> str:
    if resource_type != "dataset":
        return "Use literature as supporting context only; do not lock as an analysis dataset."
    if gate_status == "datasets_locked_ready":
        return "Dataset is ready for DATASETS_LOCKED analysis route."
    if missing_required:
        return "Fill required metadata fields before running GEO import / matrix parse / analysis: " + ", ".join(missing_required)
    return "Review raw-data availability and confirm whether this dataset can be locked."


def _load_manual_corrections(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl"
    if not path.exists():
        return {}
    corrections: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate_id = row.get("resource_candidate_id", "")
        if candidate_id:
            corrections[candidate_id] = row
    return corrections


def _apply_correction_to_profile(profile: dict[str, Any], correction: dict[str, Any]) -> dict[str, Any]:
    merged = dict(profile)
    if not correction:
        return merged
    for field in ["group_metadata_status", "sample_size_status", "organism", "tissue", "platform"]:
        value = correction.get(field)
        if value:
            merged[field] = value
    if correction.get("group_column"):
        merged["group_column"] = correction["group_column"]
    if correction.get("case_label"):
        merged["case_label"] = correction["case_label"]
    if correction.get("control_label"):
        merged["control_label"] = correction["control_label"]
    if correction.get("sample_count"):
        merged["sample_count"] = correction["sample_count"]
    return merged


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
