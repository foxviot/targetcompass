import unittest
from unittest.mock import patch

from pathlib import Path
import tempfile

from targetcompass_lite.release_acceptance import build_release_acceptance_manifest
from targetcompass_lite.test_suites import build_platform_test_matrix, list_test_suites, run_test_suite


class TestSuitesTest(unittest.TestCase):
    def test_suite_manifest_lists_quick_full_e2e(self):
        manifest = list_test_suites()

        self.assertEqual(manifest["schema_version"], "v4.test_suite_manifest/0.1")
        self.assertIn("quick", manifest["suites"])
        self.assertIn("full", manifest["suites"])
        self.assertIn("e2e", manifest["suites"])
        self.assertGreater(manifest["suites"]["quick"]["module_count"], 10)
        self.assertGreater(manifest["suites"]["full"]["module_count"], manifest["suites"]["quick"]["module_count"])
        self.assertIn("tests.test_demo_workflow", manifest["suites"]["e2e"]["modules"])

    def test_runner_writes_structured_report(self):
        fake_row = {
            "module": "tests.test_schemas",
            "status": "PASS",
            "duration_seconds": 0.01,
            "timeout_seconds": 20,
            "returncode": 0,
            "failure_reason": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "command": "python -m unittest tests.test_schemas -v",
        }
        with patch("targetcompass_lite.test_suites.SUITES", {"quick": {"modules": ["tests.test_schemas"], "timeout_seconds": 30, "per_test_timeout_seconds": 20}}), patch(
            "targetcompass_lite.test_suites._run_module", return_value=fake_row
        ):
            result = run_test_suite("quick")

        self.assertEqual(result["schema_version"], "v4.test_suite_run/0.1")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["passed_count"], 1)
        self.assertEqual(result["failed_count"], 0)

    def test_platform_test_matrix_covers_real_questions_and_failure_modes(self):
        matrix = build_platform_test_matrix(question_count=12)

        self.assertEqual(matrix["schema_version"], "v5.platform_test_matrix/0.1")
        self.assertEqual(matrix["question_count"], 12)
        scenario_ids = {row["scenario_id"] for row in matrix["scenarios"]}
        self.assertIn("real_question_e2e", scenario_ids)
        self.assertIn("network_failure", scenario_ids)
        self.assertIn("llm_failure", scenario_ids)
        self.assertIn("metadata_missing", scenario_ids)
        self.assertIn("docker_nextflow_missing", scenario_ids)
        self.assertIn("report_acceptance", scenario_ids)

    def test_release_acceptance_manifest_is_truthful_when_gates_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            manifest = build_release_acceptance_manifest(project, question_count=10)

            self.assertEqual(manifest["schema_version"], "v5.release_acceptance/0.1")
            self.assertEqual(manifest["status"], "REVIEW")
            check_ids = {row["check_id"] for row in manifest["checks"]}
            self.assertIn("quick_regression", check_ids)
            self.assertIn("real_question_longrun", check_ids)
            self.assertIn("clean_windows_installer_smoke", check_ids)
            self.assertTrue((project / "v5" / "platform" / "release_acceptance.json").exists())
            self.assertTrue((project / "v5" / "platform" / "platform_test_matrix.json").exists())


if __name__ == "__main__":
    unittest.main()
