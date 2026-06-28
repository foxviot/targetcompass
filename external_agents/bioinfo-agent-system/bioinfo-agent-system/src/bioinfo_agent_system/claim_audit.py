from __future__ import annotations

from typing import Any

from .validators import ValidationError


CLAIM_STRENGTH_ORDER = [
    "descriptive",
    "association",
    "correlation",
    "co_expression",
    "cell_state_marker",
    "candidate_biomarker",
    "mechanistic_hypothesis",
    "causal_support",
    "therapeutic_target_hypothesis",
    "experimentally_validated_target",
]

CLAIM_RANK = {name: index for index, name in enumerate(CLAIM_STRENGTH_ORDER)}


def compare_claim_strength(left: str, right: str) -> int:
    _require_known_claim(left)
    _require_known_claim(right)
    return CLAIM_RANK[left] - CLAIM_RANK[right]


def assert_not_exceed(actual: str, maximum_allowed: str, context: str) -> None:
    if compare_claim_strength(actual, maximum_allowed) > 0:
        raise ValidationError(
            f"{context}: claim {actual!r} exceeds maximum allowed {maximum_allowed!r}"
        )


def audit_project_claims(project_state: dict[str, Any]) -> list[dict[str, str]]:
    outputs = project_state["agent_outputs"]
    final_claim = outputs["executable_research_plan"]["claim_ceiling"]["max_allowed_claim"]
    audits: list[dict[str, str]] = []

    for upstream_field in [
        "question_normalization",
        "evidence_dataset_scout",
        "method_motif_feasibility",
    ]:
        upstream_output = outputs[upstream_field]
        upstream_claim = upstream_output["claim_ceiling"]["max_allowed_claim"]
        assert_not_exceed(final_claim, upstream_claim, f"claim_audit:{upstream_field}")
        audits.append(
            {
                "event": f"claim_audit:{upstream_field}",
                "status": "ok",
                "detail": f"Final claim ceiling {final_claim} <= {upstream_claim}.",
            }
        )

    for allowed_claim in outputs["executable_research_plan"]["claim_boundaries"]["allowed_claims"]:
        lowered = allowed_claim.lower()
        if "causal" in lowered or "therapeutic target" in lowered:
            raise ValidationError(
                "claim_audit: allowed claims contain unsupported causal or therapeutic language"
            )

    audits.append(
        {
            "event": "claim_audit:text_scan",
            "status": "ok",
            "detail": "Allowed claims remain at association or co-expression level.",
        }
    )
    return audits


def _require_known_claim(value: str) -> None:
    if value not in CLAIM_RANK:
        raise ValidationError(f"Unknown claim strength: {value!r}")
