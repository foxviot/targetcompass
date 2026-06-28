from __future__ import annotations

from typing import Any

from .ids import make_stable_id
from .schemas import CANONICAL_SCHEMA_VERSION, CLAIM_LEVELS, now_iso


def audit_question_alignment(
    *,
    research_spec: dict[str, Any],
    subquestions: list[dict[str, Any]],
    scope_bundle: dict[str, Any],
    evidence_item_refs: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    artifact_manifests: list[dict[str, Any]],
    qc_reports: list[dict[str, Any]],
    max_claim_level: str | None = None,
) -> dict[str, Any]:
    project_id = research_spec.get("project_id", "")
    research_spec_id = research_spec.get("research_spec_id", "")
    ceiling = max_claim_level or research_spec.get("max_claim_level") or "association"
    evidence_by_id = {item.get("evidence_item_id"): item for item in evidence_item_refs}
    artifacts_by_id = {item.get("artifact_id"): item for item in artifact_manifests}
    failed_artifact_ids, failed_evidence_ids = _failed_qc_refs(qc_reports)

    coverage_by_subquestion = _coverage_by_subquestion(subquestions, claims)
    scope_fidelity: list[dict[str, Any]] = []
    unsupported_claims: list[dict[str, Any]] = []
    claim_ceiling_violations: list[dict[str, Any]] = []
    method_relevance_findings: list[dict[str, Any]] = []
    required_reruns: list[dict[str, Any]] = []

    for claim in claims:
        claim_id = claim.get("claim_id", "")
        claim_errors = _validate_claim_required_fields(claim)
        for error in claim_errors:
            unsupported_claims.append({"claim_id": claim_id, "reason": error})

        if _claim_exceeds_ceiling(claim.get("claim_level", ""), ceiling):
            claim_ceiling_violations.append(
                {
                    "claim_id": claim_id,
                    "claim_level": claim.get("claim_level", ""),
                    "max_allowed_claim": ceiling,
                    "reason": "claim_level exceeds configured ceiling",
                }
            )
            required_reruns.append({"claim_id": claim_id, "action": "lower_claim_level_or_add_stronger_evidence"})

        drift = _scope_drift(claim.get("scope") or {}, scope_bundle)
        if drift:
            scope_fidelity.append({"claim_id": claim_id, "status": "drift", "findings": drift})
            required_reruns.append({"claim_id": claim_id, "action": "rerun_or_reframe_claim_with_matching_scope"})
        else:
            scope_fidelity.append({"claim_id": claim_id, "status": "aligned", "findings": []})

        claim_evidence_ids = claim.get("evidence_item_refs") or []
        if not claim_evidence_ids:
            unsupported_claims.append({"claim_id": claim_id, "reason": "claim has no evidence_item_refs"})
            required_reruns.append({"claim_id": claim_id, "action": "attach_audited_evidence_before_approval"})
            continue

        for evidence_id in claim_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if not evidence:
                unsupported_claims.append({"claim_id": claim_id, "evidence_item_id": evidence_id, "reason": "referenced evidence item is missing"})
                continue
            artifact = artifacts_by_id.get(evidence.get("artifact_id"))
            if not artifact:
                unsupported_claims.append({"claim_id": claim_id, "evidence_item_id": evidence_id, "reason": "referenced artifact manifest is missing"})
                continue
            if artifact.get("is_placeholder") is True:
                unsupported_claims.append({"claim_id": claim_id, "artifact_id": artifact.get("artifact_id"), "reason": "placeholder artifact cannot support approved claim"})
                required_reruns.append({"claim_id": claim_id, "action": "replace_placeholder_artifact_with_real_result"})
            if artifact.get("exists") is not True:
                unsupported_claims.append({"claim_id": claim_id, "artifact_id": artifact.get("artifact_id"), "reason": "missing artifact cannot support approved claim"})
            if artifact.get("qc_status") in {"fail", "failed", "rejected"}:
                unsupported_claims.append({"claim_id": claim_id, "artifact_id": artifact.get("artifact_id"), "reason": "artifact qc_status failed"})
            if evidence_id in failed_evidence_ids or evidence.get("artifact_id") in failed_artifact_ids:
                unsupported_claims.append({"claim_id": claim_id, "evidence_item_id": evidence_id, "reason": "QC failed evidence used by claim"})

    omitted_negative_or_failed_evidence = _omitted_negative_or_failed_evidence(evidence_item_refs, claims, failed_evidence_ids)
    for artifact in artifact_manifests:
        if not artifact.get("expected_by_task_ids"):
            method_relevance_findings.append({"artifact_id": artifact.get("artifact_id"), "finding": "artifact is not linked to expected task ids"})

    unresolved_questions = [
        {
            "subquestion_id": item.get("subquestion_id"),
            "reason": item.get("unresolved_reason"),
        }
        for item in subquestions
        if item.get("unresolved_reason")
    ]

    final_decision = _final_decision(
        unsupported_claims=unsupported_claims,
        claim_ceiling_violations=claim_ceiling_violations,
        scope_fidelity=scope_fidelity,
        coverage_by_subquestion=coverage_by_subquestion,
        unresolved_questions=unresolved_questions,
        omitted_negative_or_failed_evidence=omitted_negative_or_failed_evidence,
    )

    report_id = make_stable_id(
        "question_alignment_report",
        {
            "project_id": project_id,
            "research_spec_id": research_spec_id,
            "claim_ids": [claim.get("claim_id") for claim in claims],
            "final_decision": final_decision,
        },
    )
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "report_id": report_id,
        "project_id": project_id,
        "research_spec_id": research_spec_id,
        "generated_at": now_iso(),
        "coverage_by_subquestion": coverage_by_subquestion,
        "scope_fidelity": scope_fidelity,
        "unsupported_claims": unsupported_claims,
        "claim_ceiling_violations": claim_ceiling_violations,
        "omitted_negative_or_failed_evidence": omitted_negative_or_failed_evidence,
        "method_relevance_findings": method_relevance_findings,
        "unresolved_questions": unresolved_questions,
        "final_decision": final_decision,
        "required_reruns": required_reruns,
        "human_review_notes": _human_review_notes(final_decision, unresolved_questions, omitted_negative_or_failed_evidence),
        "status": "recorded",
    }


def _validate_claim_required_fields(claim: dict[str, Any]) -> list[str]:
    errors = []
    for field in ["supports_subquestion_ids", "evidence_item_refs", "claim_level", "scope", "limitations"]:
        if field not in claim:
            errors.append(f"claim missing required field: {field}")
    if not isinstance(claim.get("supports_subquestion_ids", []), list) or not claim.get("supports_subquestion_ids", []):
        errors.append("claim must support at least one subquestion")
    if not isinstance(claim.get("limitations", []), list):
        errors.append("claim limitations must be a list")
    return errors


def _coverage_by_subquestion(subquestions: list[dict[str, Any]], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage = []
    for subquestion in subquestions:
        subquestion_id = subquestion.get("subquestion_id")
        supporting_claim_ids = [
            claim.get("claim_id")
            for claim in claims
            if subquestion_id in (claim.get("supports_subquestion_ids") or [])
        ]
        unresolved_reason = subquestion.get("unresolved_reason", "")
        if supporting_claim_ids:
            status = "covered"
        elif unresolved_reason:
            status = "unresolved"
        else:
            status = "missing"
        coverage.append(
            {
                "subquestion_id": subquestion_id,
                "status": status,
                "supporting_claim_ids": supporting_claim_ids,
                "unresolved_reason": unresolved_reason,
            }
        )
    return coverage


def _scope_drift(claim_scope: dict[str, Any], scope_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    checks = [
        ("species", "species"),
        ("tissue", "tissues"),
        ("condition", "conditions"),
    ]
    for claim_field, scope_field in checks:
        claim_values = _as_normalized_set(claim_scope.get(claim_field))
        allowed_values = _as_normalized_set(scope_bundle.get(scope_field))
        if claim_values and allowed_values and not claim_values.issubset(allowed_values):
            findings.append(
                {
                    "field": claim_field,
                    "claim_values": sorted(claim_values),
                    "allowed_values": sorted(allowed_values),
                }
            )
    return findings


def _claim_exceeds_ceiling(claim_level: str, ceiling: str) -> bool:
    if claim_level not in CLAIM_LEVELS or ceiling not in CLAIM_LEVELS:
        return True
    return CLAIM_LEVELS.index(claim_level) > CLAIM_LEVELS.index(ceiling)


def _failed_qc_refs(qc_reports: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    failed_artifacts: set[str] = set()
    failed_evidence: set[str] = set()
    for report in qc_reports:
        for check in report.get("checks", []):
            status = str(check.get("status", "")).lower()
            if status in {"fail", "failed", "rejected"}:
                if check.get("artifact_id"):
                    failed_artifacts.add(check["artifact_id"])
                if check.get("evidence_item_id"):
                    failed_evidence.add(check["evidence_item_id"])
    return failed_artifacts, failed_evidence


def _omitted_negative_or_failed_evidence(
    evidence_item_refs: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    failed_evidence_ids: set[str],
) -> list[dict[str, Any]]:
    referenced = {
        evidence_id
        for claim in claims
        for evidence_id in (claim.get("evidence_item_refs") or [])
    }
    omitted = []
    for evidence in evidence_item_refs:
        evidence_id = evidence.get("evidence_item_id")
        review_status = str(evidence.get("review_status", "")).lower()
        is_negative_or_failed = review_status in {"negative", "failed", "fail", "rejected"} or evidence_id in failed_evidence_ids
        if is_negative_or_failed and evidence_id not in referenced:
            omitted.append(
                {
                    "evidence_item_id": evidence_id,
                    "review_status": evidence.get("review_status", ""),
                    "reason": "negative or failed evidence is not represented in final claims",
                }
            )
    return omitted


def _final_decision(
    *,
    unsupported_claims: list[dict[str, Any]],
    claim_ceiling_violations: list[dict[str, Any]],
    scope_fidelity: list[dict[str, Any]],
    coverage_by_subquestion: list[dict[str, Any]],
    unresolved_questions: list[dict[str, Any]],
    omitted_negative_or_failed_evidence: list[dict[str, Any]],
) -> str:
    if unsupported_claims or claim_ceiling_violations:
        return "reject"
    if any(item.get("status") == "drift" for item in scope_fidelity):
        return "reject"
    if any(item.get("status") == "missing" for item in coverage_by_subquestion):
        return "reject"
    if unresolved_questions or omitted_negative_or_failed_evidence:
        return "needs_review"
    return "approve"


def _human_review_notes(
    final_decision: str,
    unresolved_questions: list[dict[str, Any]],
    omitted_negative_or_failed_evidence: list[dict[str, Any]],
) -> list[str]:
    notes = []
    if final_decision == "approve":
        notes.append("All structured alignment checks passed.")
    if unresolved_questions:
        notes.append("One or more subquestions have unresolved reasons and require human review.")
    if omitted_negative_or_failed_evidence:
        notes.append("Negative or failed evidence was omitted from claims and requires review.")
    if final_decision == "reject":
        notes.append("At least one hard alignment, evidence, QC, or claim-ceiling violation was found.")
    return notes


def _as_normalized_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return {str(item).strip().lower() for item in values if str(item).strip()}
