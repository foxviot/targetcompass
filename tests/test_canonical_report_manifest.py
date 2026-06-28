import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import register_artifact
from targetcompass_lite.canonical.report_manifest import build_canonical_flow_view, build_canonical_report_manifest


class CanonicalReportManifestTest(unittest.TestCase):
    def test_manifest_links_evidence_plan_artifacts_qc_alignment_and_handoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "objects").mkdir(parents=True)
            (project / "v5" / "handoffs").mkdir(parents=True)
            (project / "v5" / "task_runs").mkdir(parents=True)
            (project / "v5" / "qc_reports").mkdir(parents=True)
            (project / "reports").mkdir(parents=True)
            (project / "v5" / "project_state.json").write_text(json.dumps({"current_stage": "REPORT_READY"}), encoding="utf-8")
            (project / "v5" / "objects" / "research_spec_r1.json").write_text(
                json.dumps({"project_id": "demo", "research_spec_id": "r1", "research_question": "Question", "max_claim_level": "association"}),
                encoding="utf-8",
            )
            (project / "v5" / "objects" / "subquestion_sq1.json").write_text(
                json.dumps({"subquestion_id": "sq1", "question": "Subquestion"}),
                encoding="utf-8",
            )
            (project / "v5" / "objects" / "scope_bundle_s1.json").write_text(
                json.dumps({"scope_bundle_id": "s1", "species": ["human"], "tissues": ["muscle"], "conditions": ["sarcopenia"]}),
                encoding="utf-8",
            )
            (project / "v5" / "objects" / "evidence_plan_ep1.json").write_text(
                json.dumps({"evidence_plan_id": "ep1", "max_claim_level": "association", "evidence_axes": ["expression"]}),
                encoding="utf-8",
            )
            (project / "v5" / "handoffs" / "handoff_h1.json").write_text(
                json.dumps({"handoff_id": "handoff_h1", "from_agent": "question_normalizer", "to_agent": "scope_resolver", "claim_ceiling": {"max_allowed_claim": "descriptive"}}),
                encoding="utf-8",
            )
            (project / "v5" / "task_runs" / "tr1.json").write_text(
                json.dumps({"task_run_id": "tr1", "task_id": "task1", "result_status": "completed"}),
                encoding="utf-8",
            )
            (project / "v5" / "qc_reports" / "qc1.json").write_text(
                json.dumps({"qc_report_id": "qc1", "task_id": "task1", "overall_status": "pass", "checks": []}),
                encoding="utf-8",
            )
            (project / "reports" / "target_report.html").write_text("<html>report</html>", encoding="utf-8")
            artifact_path = project / "reports" / "target_report_structured.json"
            artifact_path.write_text("{}", encoding="utf-8")
            register_artifact(
                project,
                "reports/target_report_structured.json",
                producer="report_writer",
                artifact_type="structured_report",
                expected_by_task_ids=["task1"],
                supports_subquestion_ids=["sq1"],
                producer_run_id="tr1",
                qc_status="pass",
            )

            manifest = build_canonical_report_manifest(project)
            self.assertEqual(manifest["evidence_plan_ref"]["object_id"], "ep1")
            self.assertTrue(manifest["artifact_manifest_refs"])
            self.assertTrue(manifest["qc_report_refs"])
            self.assertEqual(manifest["question_alignment_report_ref"]["object_type"], "QuestionAlignmentReport")
            self.assertTrue(manifest["handoff_refs"])
            self.assertTrue((project / "v5" / "reports" / "canonical_report_manifest.json").exists())
            self.assertTrue(manifest["human_review_gate"]["required"])

            flow = build_canonical_flow_view(project)
            self.assertEqual(len(flow["flow"]), 7)
            self.assertEqual(flow["flow"][0]["agent_id"], "question_normalizer")
            self.assertEqual(flow["flow"][0]["handoff_id"], "handoff_h1")


if __name__ == "__main__":
    unittest.main()
