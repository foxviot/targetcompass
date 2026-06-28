from typing import Any


ROLE_DEPENDENCIES = {
    "disease_normalizer": [],
    "dataset_scout": ["disease_normalizer"],
    "planner": ["dataset_scout"],
    "method_reviewer": ["planner"],
    "result_reviewer": ["method_reviewer"],
    "causal_reviewer": ["result_reviewer"],
    "report_writer": ["result_reviewer", "causal_reviewer"],
}

REVIEWER_ROLES = {"method_reviewer", "result_reviewer", "causal_reviewer"}
GENERATOR_ROLES = {"disease_normalizer", "dataset_scout", "planner", "report_writer"}


def retry_policy_for_role(role_id: str) -> dict[str, Any]:
    return {
        "max_attempts": 2 if role_id in REVIEWER_ROLES else 1,
        "retry_on": ["schema_validation_failed", "transient_tool_failure"],
        "backoff_seconds": [0, 5],
    }


def fallback_method_for_role(role_id: str) -> str:
    return {
        "disease_normalizer": "local_disease_normalizer_v0",
        "dataset_scout": "local_dataset_scout_v0",
        "planner": "local_planner_v0",
        "method_reviewer": "local_method_reviewer_v0",
        "result_reviewer": "local_result_reviewer_v0",
        "causal_reviewer": "local_causal_reviewer_v0",
        "report_writer": "local_report_writer_v0",
    }[role_id]


def fallback_policy_for_role(role_id: str) -> dict[str, Any]:
    return {
        "fallback_allowed": True,
        "fallback_method": fallback_method_for_role(role_id),
        "requires_review_after_fallback": role_id in REVIEWER_ROLES,
    }


def approval_policy_for_role(role_id: str) -> dict[str, Any]:
    return {
        "can_approve_outputs": role_id in REVIEWER_ROLES,
        "cannot_approve_roles": sorted(GENERATOR_ROLES if role_id in GENERATOR_ROLES else [role_id]),
        "must_write_review_items": role_id in REVIEWER_ROLES,
    }
