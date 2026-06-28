import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import register_artifact
from targetcompass_lite.canonical.backend_access import load_artifact_registry_preferred
from targetcompass_lite.canonical.doctor import run_v5_doctor
from targetcompass_lite.canonical.report_manifest import build_canonical_flow_view, build_canonical_report_manifest


class V5DoctorTest(unittest.TestCase):
    def test_doctor_reports_pass_or_warn_for_initialized_v5_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _minimal_v5_project(Path(tmp) / "demo")

            result = run_v5_doctor(project)

            self.assertIn(result["status"], {"PASS", "WARN"})
            self.assertTrue((project / "v5" / "doctor" / "v5_doctor_report.json").exists())
            self.assertEqual(result["backend_summary"]["active_backends"]["object_store"], "minio_local")
            self.assertEqual(result["artifact_query"]["source"], "minio_local")
            self.assertGreaterEqual(result["artifact_query"]["artifact_count"], 1)

    def test_artifact_query_prefers_active_backend_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _minimal_v5_project(Path(tmp) / "demo")

            bundle = load_artifact_registry_preferred(project)

            self.assertEqual(bundle["source"], "minio_local")
            self.assertEqual(bundle["backend_status"], "ACTIVE")
            self.assertTrue(bundle["artifacts"])
            self.assertEqual(bundle["artifacts"][0]["source_backend"], "minio_local")

    def test_report_manifest_records_backend_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _minimal_v5_project(Path(tmp) / "demo")

            manifest = build_canonical_report_manifest(project)

            self.assertEqual(manifest["backend_preference"]["source"], "minio_local")
            flow = build_canonical_flow_view(project)
            self.assertEqual(flow["backend_preference"]["active_backends"]["object_store"], "minio_local")


def _minimal_v5_project(project: Path) -> Path:
    project.mkdir(parents=True)
    (project / "v5" / "objects").mkdir(parents=True)
    (project / "v5" / "handoffs").mkdir(parents=True)
    (project / "v5" / "task_runs").mkdir(parents=True)
    (project / "v5" / "qc_reports").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    (project / "v4").mkdir(parents=True)
    (project / "v5" / "project_state.json").write_text(json.dumps({"current_stage": "TASKS_READY"}), encoding="utf-8")
    (project / "v5" / "objects" / "research_spec_r1.json").write_text(
        json.dumps({"project_id": "demo", "research_spec_id": "r1", "research_question": "Question", "max_claim_level": "association"}),
        encoding="utf-8",
    )
    (project / "v5" / "objects" / "subquestion_sq1.json").write_text(json.dumps({"subquestion_id": "sq1", "question": "Subquestion"}), encoding="utf-8")
    (project / "v5" / "objects" / "scope_bundle_s1.json").write_text(
        json.dumps({"scope_bundle_id": "s1", "species": ["human"], "tissues": ["muscle"], "conditions": ["sarcopenia"]}),
        encoding="utf-8",
    )
    (project / "v5" / "objects" / "evidence_plan_ep1.json").write_text(
        json.dumps({"evidence_plan_id": "ep1", "max_claim_level": "association", "evidence_axes": ["expression"]}),
        encoding="utf-8",
    )
    (project / "v5" / "task_runs" / "tr1.json").write_text(
        json.dumps({"task_run_id": "tr1", "task_id": "task1", "executor": "local", "result_status": "completed"}),
        encoding="utf-8",
    )
    (project / "v5" / "qc_reports" / "qc1.json").write_text(
        json.dumps({"qc_report_id": "qc1", "task_id": "task1", "overall_status": "pass", "checks": []}),
        encoding="utf-8",
    )
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
    (project / "v5" / "active_backends.json").write_text(
        json.dumps(
            {
                "schema_version": "v5.active_backends/0.1",
                "project_id": "demo",
                "status": "ACTIVE",
                "active_backends": {"evidence_db": "postgres_local", "object_store": "minio_local"},
                "backend_check_ref": "v4/local_backend_check.json",
                "backend_sync_ref": "v4/local_backend_sync.json",
                "policy": {"read_preference": "postgres_local_then_sqlite", "artifact_write_preference": "minio_local_then_filesystem"},
            }
        ),
        encoding="utf-8",
    )
    return project


if __name__ == "__main__":
    unittest.main()
