import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.mock_runner import run_mock_canonical_pipeline
from targetcompass_lite.canonical.store import load_events, load_project_state


class CanonicalMockRunnerTest(unittest.TestCase):
    def test_mock_pipeline_completes_to_task_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "mock_project"
            result = run_mock_canonical_pipeline(project_dir, "Does sarcopenia muscle contain SASP-high cells?")
            self.assertEqual(result["project_state"]["current_stage"], "TASKS_READY")
            self.assertTrue((project_dir / "v5" / "project_state.json").exists())
            self.assertTrue((project_dir / "v5" / "events.jsonl").exists())
            self.assertTrue((project_dir / "v5" / "objects").exists())
            self.assertTrue((project_dir / "v5" / "handoffs").exists())

    def test_no_verified_dataset_in_mock_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_mock_canonical_pipeline(Path(tmp) / "mock_project", "question")
            self.assertTrue(result["resource_candidates"])
            for candidate in result["resource_candidates"]:
                self.assertIs(candidate["verified"], False)
                self.assertEqual(candidate["source_status"], "mock_placeholder")

    def test_state_does_not_exceed_tasks_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "mock_project"
            run_mock_canonical_pipeline(project_dir, "question")
            state = load_project_state(project_dir)
            self.assertEqual(state["current_stage"], "TASKS_READY")
            self.assertNotIn("TASKS_RUNNING", state["stage_history"])
            self.assertNotIn("REPORT_READY", state["stage_history"])

    def test_task_packets_have_subquestion_and_expected_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_mock_canonical_pipeline(Path(tmp) / "mock_project", "question")
            analysis_packets = [packet for packet in result["task_packets"] if packet["packet_type"] == "AnalysisTaskPacket"]
            review_packets = [packet for packet in result["task_packets"] if packet["packet_type"] == "ReviewTaskPacket"]
            self.assertTrue(analysis_packets)
            self.assertTrue(review_packets)
            for packet in analysis_packets:
                self.assertTrue(packet["subquestion_id"])
                self.assertTrue(packet["expected_outputs"])
                self.assertEqual(packet["code_change_instructions"], [])
            for packet in review_packets:
                self.assertTrue(packet["subquestion_id"])
                self.assertTrue(packet["required_checks"])

    def test_handoff_chain_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_mock_canonical_pipeline(Path(tmp) / "mock_project", "question")
            pairs = {(handoff["from_agent"], handoff["to_agent"]) for handoff in result["handoffs"]}
            self.assertEqual(
                pairs,
                {
                    ("question_normalizer", "scope_resolver"),
                    ("scope_resolver", "evidence_plan_builder"),
                    ("evidence_plan_builder", "resource_discovery_agent"),
                    ("resource_discovery_agent", "method_adapter_workorder_compiler"),
                    ("method_adapter_workorder_compiler", "result_auditor"),
                },
            )

    def test_claim_ceiling_is_not_loosened(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_mock_canonical_pipeline(Path(tmp) / "mock_project", "question")
            levels = [handoff["claim_ceiling"]["max_allowed_claim"] for handoff in result["handoffs"]]
            self.assertEqual(levels, ["descriptive", "descriptive", "descriptive", "descriptive", "descriptive"])

    def test_events_count_covers_agent_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "mock_project"
            run_mock_canonical_pipeline(project_dir, "question")
            self.assertGreaterEqual(len(load_events(project_dir)), 6)

    def test_does_not_write_old_v4_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "mock_project"
            run_mock_canonical_pipeline(project_dir, "question")
            self.assertFalse((project_dir / "v4").exists())
            self.assertFalse((project_dir / "results").exists())


if __name__ == "__main__":
    unittest.main()
