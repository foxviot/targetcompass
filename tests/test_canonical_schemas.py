import unittest

from targetcompass_lite.canonical.ids import hash_payload, make_stable_id, normalize_id_text
from targetcompass_lite.canonical.schemas import ProjectState, ResearchSpec, SubQuestion
from targetcompass_lite.canonical.validation import (
    validate_claim_ceiling,
    validate_no_unknown_verified_dataset,
    validate_project_state,
    validate_required_fields,
)


class CanonicalSchemasTest(unittest.TestCase):
    def test_stable_id_is_deterministic_and_normalized(self):
        payload_a = {"b": 2, "a": ["x", "y"]}
        payload_b = {"a": ["x", "y"], "b": 2}
        self.assertEqual(hash_payload(payload_a), hash_payload(payload_b))
        self.assertEqual(make_stable_id("Research Spec", payload_a), make_stable_id("research_spec", payload_b))
        self.assertEqual(normalize_id_text("Research Spec!"), "research_spec")

    def test_research_spec_required_fields(self):
        spec = ResearchSpec(project_id="demo", research_question="Find SASP-high muscle cells").to_dict()
        self.assertFalse(validate_required_fields(spec, ["schema_version", "research_spec_id", "project_id", "research_question", "created_at", "provenance"]))
        bad = dict(spec)
        bad["research_question"] = ""
        self.assertIn("research_question: missing required field", validate_required_fields(bad, ["research_question"]))

    def test_subquestion_links_to_research_spec(self):
        spec = ResearchSpec(project_id="demo", research_question="Find SASP-high muscle cells").to_dict()
        sub = SubQuestion(research_spec_id=spec["research_spec_id"], question="Which cell type is SASP-high?").to_dict()
        self.assertEqual(sub["research_spec_id"], spec["research_spec_id"])
        self.assertTrue(sub["subquestion_id"].startswith("subquestion_"))

    def test_claim_ceiling_rejects_causal_claim_when_max_is_association(self):
        claim = {"claim_id": "c1", "claim_level": "causal_support"}
        errors = validate_claim_ceiling(claim, "association")
        self.assertIn("exceeds ceiling", errors[0])
        self.assertFalse(validate_claim_ceiling({"claim_id": "c2", "claim_level": "association"}, "association"))

    def test_resource_candidate_placeholder_cannot_be_verified(self):
        candidate = {"resource_name": "AUTO_GEO", "accession": "AUTO_GEO_SARCOPENIA", "verified": True, "source_status": "mock_placeholder"}
        errors = validate_no_unknown_verified_dataset(candidate)
        self.assertGreaterEqual(len(errors), 1)
        self.assertFalse(
            validate_no_unknown_verified_dataset(
                {"resource_name": "GSE123", "accession": "GSE123", "verified": True, "source_status": "metadata_verified"}
            )
        )

    def test_project_state_validates_required_stage_fields(self):
        state = ProjectState(project_id="demo", current_stage="INTAKE", allowed_next_stages=["QUESTION_RESOLVED"]).__dict__
        self.assertFalse(validate_project_state(state))
        bad = dict(state)
        bad["allowed_next_stages"] = "QUESTION_RESOLVED"
        self.assertIn("allowed_next_stages: expected list", validate_project_state(bad))


if __name__ == "__main__":
    unittest.main()
