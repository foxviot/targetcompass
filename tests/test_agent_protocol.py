import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.agent_protocol import enforce_claim_ceiling, validate_agent_handoff
from targetcompass_lite.canonical.agent_specs import build_agent_specs, next_agent_for_stage
from targetcompass_lite.canonical.handoff import build_handoff, load_handoffs, write_handoff


class AgentProtocolTest(unittest.TestCase):
    def test_all_seven_agent_specs_exist(self):
        specs = build_agent_specs()
        self.assertEqual(
            set(specs),
            {
                "question_normalizer",
                "scope_resolver",
                "evidence_plan_builder",
                "resource_discovery_agent",
                "method_adapter_workorder_compiler",
                "result_auditor",
                "evidence_synthesizer_reporter",
            },
        )
        self.assertEqual(next_agent_for_stage("INTAKE"), "question_normalizer")

    def test_forbidden_actions_are_non_empty(self):
        for agent_id, spec in build_agent_specs().items():
            self.assertTrue(spec["forbidden_actions"], agent_id)

    def test_handoff_missing_required_field_is_invalid(self):
        handoff = build_handoff(
            project_id="demo",
            from_agent="question_normalizer",
            to_agent="scope_resolver",
            output_object_refs=[{"object_type": "ResearchSpec", "object_id": "rs1"}],
            max_allowed_claim="descriptive",
        )
        handoff.pop("payload_hash")
        result = validate_agent_handoff(handoff, "question_normalizer", "scope_resolver")
        self.assertEqual(result["status"], "invalid")
        self.assertIn("payload_hash: missing required field", result["errors"])

    def test_blocking_issues_return_blocked_status(self):
        handoff = build_handoff(
            project_id="demo",
            from_agent="question_normalizer",
            to_agent="scope_resolver",
            output_object_refs=[{"object_type": "ResearchSpec", "object_id": "rs1"}],
            blocking_issues=["Species is ambiguous."],
            max_allowed_claim="descriptive",
        )
        result = validate_agent_handoff(handoff, "question_normalizer", "scope_resolver")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["errors"])

    def test_claim_ceiling_cannot_be_loosened(self):
        errors = enforce_claim_ceiling("association", "causal_support")
        self.assertIn("claim ceiling cannot be loosened", errors[0])
        self.assertFalse(enforce_claim_ceiling("association", "descriptive"))

    def test_placeholder_dataset_cannot_be_locked(self):
        handoff = build_handoff(
            project_id="demo",
            from_agent="resource_discovery_agent",
            to_agent="method_adapter_workorder_compiler",
            output_object_refs=[
                {
                    "object_type": "DatasetSelectionDecision",
                    "object_id": "dataset_auto",
                    "target_stage": "DATASETS_LOCKED",
                    "verified": False,
                    "source_status": "mock_placeholder",
                }
            ],
            max_allowed_claim="association",
        )
        result = validate_agent_handoff(handoff, "resource_discovery_agent", "method_adapter_workorder_compiler")
        self.assertEqual(result["status"], "invalid")
        self.assertIn("dataset candidate cannot enter DATASETS_LOCKED", result["errors"][0])

    def test_wrong_from_agent_is_rejected(self):
        handoff = build_handoff(
            project_id="demo",
            from_agent="question_normalizer",
            to_agent="scope_resolver",
            output_object_refs=[{"object_type": "ResearchSpec", "object_id": "rs1"}],
            max_allowed_claim="descriptive",
        )
        result = validate_agent_handoff(handoff, "scope_resolver", "evidence_plan_builder")
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("from_agent mismatch" in error for error in result["errors"]))

    def test_write_and_load_handoffs_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            handoff = build_handoff(
                project_id="demo_project",
                from_agent="question_normalizer",
                to_agent="scope_resolver",
                output_object_refs=[{"object_type": "ResearchSpec", "object_id": "rs1"}],
                max_allowed_claim="descriptive",
            )
            write_handoff(project_dir, handoff)
            loaded = load_handoffs(project_dir)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["handoff_id"], handoff["handoff_id"])


if __name__ == "__main__":
    unittest.main()
