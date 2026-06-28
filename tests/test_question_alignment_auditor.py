import unittest

from targetcompass_lite.canonical.alignment_auditor import audit_question_alignment


def base_inputs():
    research_spec = {
        "project_id": "demo_project",
        "research_spec_id": "research_spec_1",
        "research_question": "Does sarcopenia muscle contain SASP-high surface marker cells?",
        "max_claim_level": "association",
    }
    subquestions = [
        {"subquestion_id": "sq1", "question": "Which cell population is SASP-high?"},
    ]
    scope_bundle = {
        "scope_bundle_id": "scope1",
        "species": ["human"],
        "tissues": ["muscle"],
        "conditions": ["sarcopenia"],
    }
    evidence_item_refs = [
        {"evidence_item_id": "ev1", "artifact_id": "artifact1", "review_status": "audited"},
    ]
    artifact_manifests = [
        {
            "artifact_id": "artifact1",
            "exists": True,
            "is_placeholder": False,
            "qc_status": "pass",
            "expected_by_task_ids": ["task1"],
        }
    ]
    qc_reports = [{"qc_report_id": "qc1", "overall_status": "pass", "checks": []}]
    claims = [
        {
            "claim_id": "claim1",
            "text": "A human sarcopenia muscle-associated signal is supported at association level.",
            "claim_level": "association",
            "evidence_item_refs": ["ev1"],
            "supports_subquestion_ids": ["sq1"],
            "scope": {"species": "human", "tissue": "muscle", "condition": "sarcopenia"},
            "limitations": ["Association only."],
        }
    ]
    return research_spec, subquestions, scope_bundle, evidence_item_refs, claims, artifact_manifests, qc_reports


class QuestionAlignmentAuditorTest(unittest.TestCase):
    def test_claim_without_evidence_is_rejected(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        claims[0]["evidence_item_refs"] = []
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        self.assertEqual(report["final_decision"], "reject")
        self.assertTrue(any("no evidence_item_refs" in item["reason"] for item in report["unsupported_claims"]))

    def test_species_drift_is_detected(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        claims[0]["scope"]["species"] = "mouse"
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        self.assertEqual(report["final_decision"], "reject")
        self.assertEqual(report["scope_fidelity"][0]["status"], "drift")
        self.assertEqual(report["scope_fidelity"][0]["findings"][0]["field"], "species")

    def test_causal_claim_exceeds_association_ceiling(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        claims[0]["claim_level"] = "causal_support"
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
            max_claim_level="association",
        )
        self.assertEqual(report["final_decision"], "reject")
        self.assertEqual(report["claim_ceiling_violations"][0]["claim_id"], "claim1")

    def test_placeholder_artifact_supporting_claim_is_rejected(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        artifacts[0]["is_placeholder"] = True
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        self.assertEqual(report["final_decision"], "reject")
        self.assertTrue(any("placeholder artifact" in item["reason"] for item in report["unsupported_claims"]))

    def test_subquestion_coverage_is_correct(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        subquestions.append({"subquestion_id": "sq2", "question": "Which marker is surface-accessible?"})
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        by_id = {item["subquestion_id"]: item for item in report["coverage_by_subquestion"]}
        self.assertEqual(by_id["sq1"]["status"], "covered")
        self.assertEqual(by_id["sq2"]["status"], "missing")
        self.assertEqual(report["final_decision"], "reject")

    def test_unresolved_reason_needs_review_not_full_failure(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        subquestions.append(
            {
                "subquestion_id": "sq2",
                "question": "Which exact cell type?",
                "unresolved_reason": "No single-cell dataset has been verified yet.",
            }
        )
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        by_id = {item["subquestion_id"]: item for item in report["coverage_by_subquestion"]}
        self.assertEqual(by_id["sq2"]["status"], "unresolved")
        self.assertEqual(report["final_decision"], "needs_review")
        self.assertTrue(report["unresolved_questions"])

    def test_qc_failed_evidence_used_by_claim_is_rejected(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        qc_reports = [
            {
                "qc_report_id": "qc1",
                "overall_status": "fail",
                "checks": [{"status": "fail", "evidence_item_id": "ev1", "artifact_id": "artifact1"}],
            }
        ]
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        self.assertEqual(report["final_decision"], "reject")
        self.assertTrue(any("QC failed evidence" in item["reason"] for item in report["unsupported_claims"]))

    def test_omitted_negative_evidence_needs_review(self):
        research_spec, subquestions, scope_bundle, evidence_refs, claims, artifacts, qc_reports = base_inputs()
        evidence_refs.append({"evidence_item_id": "ev_negative", "artifact_id": "artifact1", "review_status": "negative"})
        report = audit_question_alignment(
            research_spec=research_spec,
            subquestions=subquestions,
            scope_bundle=scope_bundle,
            evidence_item_refs=evidence_refs,
            claims=claims,
            artifact_manifests=artifacts,
            qc_reports=qc_reports,
        )
        self.assertEqual(report["final_decision"], "needs_review")
        self.assertEqual(report["omitted_negative_or_failed_evidence"][0]["evidence_item_id"], "ev_negative")


if __name__ == "__main__":
    unittest.main()
