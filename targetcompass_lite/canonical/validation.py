from typing import Any

from .schemas import CLAIM_LEVELS


def validate_required_fields(obj: dict[str, Any], required: list[str]) -> list[str]:
    errors = []
    for field in required:
        if field not in obj or obj[field] in (None, "", []):
            errors.append(f"{field}: missing required field")
    return errors


def validate_enum(value: str, allowed: list[str], field: str) -> list[str]:
    return [] if value in allowed else [f"{field}: must be one of {', '.join(allowed)}"]


def validate_no_unknown_verified_dataset(candidate: dict[str, Any]) -> list[str]:
    errors = []
    if candidate.get("verified") is True:
        source_status = str(candidate.get("source_status", "")).lower()
        accession = str(candidate.get("accession", "") or candidate.get("dataset_id", "")).upper()
        if source_status in {"mock", "mock_placeholder", "placeholder", "unknown"}:
            errors.append("verified dataset cannot come from mock/placeholder/unknown source_status")
        if accession.startswith("AUTO_") or accession.startswith("MOCK_") or not accession:
            errors.append("verified dataset requires a real accession, not AUTO_/MOCK_/empty")
    return errors


def validate_claim_ceiling(claim: dict[str, Any], max_allowed: str) -> list[str]:
    errors = []
    claim_level = claim.get("claim_level", "")
    if claim_level not in CLAIM_LEVELS:
        return [f"claim_level: must be one of {', '.join(CLAIM_LEVELS)}"]
    if max_allowed not in CLAIM_LEVELS:
        return [f"max_allowed: must be one of {', '.join(CLAIM_LEVELS)}"]
    if CLAIM_LEVELS.index(claim_level) > CLAIM_LEVELS.index(max_allowed):
        errors.append(f"claim_level {claim_level} exceeds ceiling {max_allowed}")
    return errors


def validate_artifact_manifest(manifest: dict[str, Any]) -> list[str]:
    errors = validate_required_fields(
        manifest,
        ["artifact_id", "project_id", "path", "exists", "checksum_sha256", "is_placeholder", "qc_status"],
    )
    if manifest.get("exists") is not True:
        errors.append("artifact cannot support evidence when exists=false")
    if manifest.get("is_placeholder") is True:
        errors.append("artifact cannot support evidence when is_placeholder=true")
    if manifest.get("qc_status") == "fail":
        errors.append("artifact cannot support evidence when qc_status=fail")
    return errors


def validate_project_state(state: dict[str, Any]) -> list[str]:
    errors = validate_required_fields(
        state,
        ["schema_version", "project_id", "current_stage", "allowed_next_stages", "status"],
    )
    if "allowed_next_stages" in state and not isinstance(state["allowed_next_stages"], list):
        errors.append("allowed_next_stages: expected list")
    return errors
