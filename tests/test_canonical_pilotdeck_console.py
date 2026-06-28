import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import register_artifact
from targetcompass_lite.canonical.pilotdeck_console import build_pilotdeck_console


class CanonicalPilotDeckConsoleTest(unittest.TestCase):
    def test_console_collects_run_history_recovery_artifact_and_evidence_panels(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v5" / "task_runs").mkdir(parents=True)
            (project / "v5" / "qc_reports").mkdir(parents=True)
            (project / "v5" / "task_runs" / "tr1.json").write_text(json.dumps({"task_run_id": "tr1", "task_id": "task1", "executor": "nextflow", "result_status": "failed", "failure_reason": "failed"}), encoding="utf-8")
            (project / "v5" / "qc_reports" / "qc1.json").write_text(json.dumps({"qc_report_id": "qc1", "overall_status": "fail"}), encoding="utf-8")
            artifact = project / "reports" / "target_report.html"
            artifact.parent.mkdir()
            artifact.write_text("<html>report</html>", encoding="utf-8")
            register_artifact(project, "reports/target_report.html", producer="report", artifact_type="html_report", expected_by_task_ids=["task1"], supports_subquestion_ids=["sq1"], qc_status="pass")
            (project / "results").mkdir(exist_ok=True)
            (project / "results" / "recovery_manifest.json").write_text(json.dumps({"open_count": 1, "items": [{"stage": "nextflow", "reason": "failed", "command": "retry"}]}), encoding="utf-8")
            (project / "v4").mkdir()
            (project / "v4" / "evidence_db_last_query.json").write_text(json.dumps({"match_count": 1, "items": [{"entity_symbol": "IL6", "evidence_type": "bulk_deg", "evidence_level": "L3_omics", "source_dataset": "GSE1"}]}), encoding="utf-8")

            console = build_pilotdeck_console(project)

            self.assertEqual(console["schema_version"], "v5.pilotdeck_console/0.1")
            self.assertEqual(console["run_history"]["task_run_count"], 1)
            self.assertEqual(console["failure_recovery"]["open_count"], 1)
            self.assertEqual(console["artifact_drilldown"]["artifact_count"], 1)
            self.assertEqual(console["evidence_drilldown"]["match_count"], 1)
            self.assertTrue((project / "v5" / "pilotdeck" / "console.json").exists())

    def test_console_prefers_v5_failure_recovery_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "results").mkdir()
            (project / "results" / "recovery_manifest.json").write_text(json.dumps({"open_count": 1, "items": [{"stage": "legacy", "reason": "old"}]}), encoding="utf-8")
            (project / "v5" / "recovery").mkdir(parents=True)
            (project / "v5" / "recovery" / "failure_recovery_report.json").write_text(
                json.dumps(
                    {
                        "status": "review_required",
                        "item_count": 2,
                        "items": [
                            {"item_id": "metadata_insufficient:GSE1", "category": "resource_discovery", "severity": "medium", "status": "open", "reason": "metadata not verified"},
                            {"item_id": "claim_ceiling_violation", "category": "report", "severity": "critical", "status": "open", "reason": "claim too high"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            console = build_pilotdeck_console(project)

            self.assertEqual(console["failure_recovery"]["source_ref"], "v5/recovery/failure_recovery_report.json")
            self.assertEqual(console["failure_recovery"]["open_count"], 2)
            self.assertEqual(console["failure_recovery"]["items"][0]["category"], "resource_discovery")


if __name__ == "__main__":
    unittest.main()
