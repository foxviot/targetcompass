import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.canonical.real_question_validation import run_real_question_validation


class RealQuestionValidationTests(unittest.TestCase):
    def test_validation_writes_summary_and_per_question_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"

            def fake_llm(project_dir, user_question, **kwargs):
                out = Path(project_dir) / "v5" / "llm_roles"
                out.mkdir(parents=True, exist_ok=True)
                payload = {
                    "status": "completed",
                    "agent_runs": [{"agent_id": "question_normalizer", "fallback_used": False}],
                }
                (out / "llm_orchestration_run.json").write_text(json.dumps(payload), encoding="utf-8")
                return payload

            def fake_local(project_dir, question, **kwargs):
                base = Path(project_dir) / "v5"
                (base / "local_demo").mkdir(parents=True, exist_ok=True)
                (base / "resource_discovery").mkdir(parents=True, exist_ok=True)
                local = {"status": "completed"}
                bundle = {
                    "candidate_count": 2,
                    "verified_candidate_count": 2,
                    "resource_candidates": [{"resource_candidate_id": "r1"}, {"resource_candidate_id": "r2"}],
                }
                gate = {"datasets_lockable_count": 0, "manual_review_count": 2}
                (base / "local_demo" / "local_demo_run.json").write_text(json.dumps(local), encoding="utf-8")
                (base / "resource_discovery" / "resource_discovery_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
                (base / "resource_discovery" / "resource_gate_report.json").write_text(json.dumps(gate), encoding="utf-8")
                return local

            with patch("targetcompass_lite.canonical.real_question_validation.run_canonical_llm_roles", side_effect=fake_llm), patch(
                "targetcompass_lite.canonical.real_question_validation.run_v5_local_demo", side_effect=fake_local
            ):
                result = run_real_question_validation(project, question_count=2, output_name="validation_test")

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["question_count"], 2)
            self.assertEqual(result["totals"]["resource_candidates"], 4)
            out = project / "v5" / "validation" / "validation_test"
            self.assertTrue((out / "summary.json").exists())
            self.assertTrue((out / "summary.md").exists())
            self.assertTrue((out / "summary.html").exists())
            self.assertTrue((out / "q01" / "llm_orchestration_run.json").exists())
            self.assertTrue((out / "q02" / "resource_gate_report.json").exists())

    def test_isolated_projects_write_report_export_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"

            def fake_llm(project_dir, user_question, **kwargs):
                out = Path(project_dir) / "v5" / "llm_roles"
                out.mkdir(parents=True, exist_ok=True)
                payload = {
                    "status": "completed",
                    "agent_runs": [{"agent_id": "question_normalizer", "fallback_used": False}],
                }
                (out / "llm_orchestration_run.json").write_text(json.dumps(payload), encoding="utf-8")
                return payload

            def fake_local(project_dir, question, **kwargs):
                base = Path(project_dir) / "v5"
                (base / "local_demo").mkdir(parents=True, exist_ok=True)
                (base / "resource_discovery").mkdir(parents=True, exist_ok=True)
                (base / "reports").mkdir(parents=True, exist_ok=True)
                local = {"status": "completed"}
                bundle = {
                    "candidate_count": 1,
                    "verified_candidate_count": 1,
                    "resource_candidates": [{"resource_candidate_id": "r1"}],
                }
                gate = {"datasets_lockable_count": 0, "manual_review_count": 1}
                (base / "local_demo" / "local_demo_run.json").write_text(json.dumps(local), encoding="utf-8")
                (base / "resource_discovery" / "resource_discovery_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
                (base / "resource_discovery" / "resource_gate_report.json").write_text(json.dumps(gate), encoding="utf-8")
                (base / "reports" / "canonical_report_manifest.json").write_text(
                    json.dumps({"human_review_gate": {"required": True}, "task_run_refs": [], "qc_report_refs": []}),
                    encoding="utf-8",
                )
                return local

            with patch("targetcompass_lite.canonical.real_question_validation.PROJECTS", Path(tmp) / "projects"), patch(
                "targetcompass_lite.canonical.real_question_validation.run_canonical_llm_roles", side_effect=fake_llm
            ), patch("targetcompass_lite.canonical.real_question_validation.run_v5_local_demo", side_effect=fake_local):
                result = run_real_question_validation(project, question_count=1, output_name="isolated_test", isolated_projects=True)

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["isolated_project_count"], 1)
            self.assertEqual(result["export_package_count"], 1)
            row = result["rows"][0]
            isolated = Path(row["isolated_project_path"])
            self.assertTrue((isolated / "v5" / "reports" / "product_report.html").exists())
            self.assertTrue(Path(row["export_package"]).exists())
            self.assertTrue((project / "v5" / "validation" / "isolated_test" / "q01" / "question_project_index.json").exists())


if __name__ == "__main__":
    unittest.main()
