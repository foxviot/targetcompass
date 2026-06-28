import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.artifact_store import put_artifact
from targetcompass_lite.canonical.access_control import issue_access_token, set_project_member
from targetcompass_lite.canonical.backend_writer import write_json_artifact
from targetcompass_lite.platform_admin import (
    build_backend_primary_status,
    build_data_cache_manifest,
    build_platform_p1_readiness,
    build_platform_p2_readiness,
    build_platform_production_readiness,
    cleanup_data_cache,
    query_platform_audit,
)
from targetcompass_lite.services import dispatch_service_request


class PlatformAdminTest(unittest.TestCase):
    def test_backend_status_audit_and_cache_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            set_project_member(project, "reviewer1", "reviewer")
            issue_access_token(project, "reviewer1", ttl_minutes=10, scopes=["project:read"])
            write_json_artifact(project, "v5/demo/object.json", {"ok": True}, producer="unit", artifact_type="json")
            (project / "reports" / "target_report.html").write_text("<html>ok</html>", encoding="utf-8")
            put_artifact(project, "reports/target_report.html", producer="report", artifact_type="html_report")
            dispatch_service_request("project_api", "status", project, caller="mcp_gateway")

            status = build_backend_primary_status(project)
            audit = query_platform_audit(project, source="all", limit=50)
            cache = build_data_cache_manifest(project)

            self.assertEqual(status["schema_version"], "v5.backend_primary_status/0.1")
            self.assertIn(status["overall_status"], {"FALLBACK_ACTIVE", "LEGACY_WRITER_REMAINING", "PRIMARY_READY", "EVIDENCE_FALLBACK", "OBJECT_WRITE_WARN", "ARTIFACT_RECOVERY_REQUIRED"})
            self.assertGreaterEqual(audit["match_count"], 3)
            self.assertGreaterEqual(cache["total_size_bytes"], 1)
            self.assertTrue((project / "v5" / "platform" / "backend_primary_status.json").exists())
            self.assertTrue((project / "v5" / "platform" / "platform_audit_last_query.json").exists())
            self.assertTrue((project / "v5" / "platform" / "data_cache_manifest.json").exists())

            p1 = build_platform_p1_readiness(project)
            self.assertEqual(p1["schema_version"], "v5.platform_p1_readiness/0.1")
            self.assertIn(p1["status"], {"PASS", "REVIEW"})
            self.assertTrue((project / "v5" / "platform" / "p1_readiness.json").exists())
            self.assertIn("projects", p1["pages"])
            self.assertTrue(any(row["check_id"] == "project_lifecycle" for row in p1["checks"]))

            p2 = build_platform_p2_readiness(project)
            self.assertEqual(p2["schema_version"], "v5.platform_p2_readiness/0.1")
            self.assertIn(p2["status"], {"PASS", "REVIEW"})
            self.assertTrue((project / "v5" / "platform" / "p2_readiness.json").exists())
            for check_id in [
                "multi_user_permissions",
                "postgres_minio_primary_path",
                "professor_demo_slim_storage",
                "nextflow_large_scale_analysis",
                "long_term_memory",
                "wet_lab_protocol_signoff",
            ]:
                self.assertTrue(any(row["check_id"] == check_id for row in p2["checks"]))
            self.assertIn("storage", p2["pages"])

            validation_dir = project / "v5" / "validation" / "online_longrun_50q_test"
            validation_dir.mkdir(parents=True)
            (validation_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "question_count": 50,
                        "expected_question_count": 50,
                        "created_at": "2026-06-24T00:00:00Z",
                        "totals": {"llm_failures": 0, "resource_failures": 0},
                        "export_package_count": 50,
                    }
                ),
                encoding="utf-8",
            )
            production = build_platform_production_readiness(project)
            self.assertEqual(production["schema_version"], "v5.production_readiness/0.1")
            self.assertTrue((project / "v5" / "platform" / "production_readiness.json").exists())
            for check_id in [
                "formal_auth_oidc_vault_sessions",
                "postgres_minio_primary_only",
                "long_term_memory_productized",
                "windows_gui_installer_release",
                "nextflow_large_sample_validation",
                "codex_worker_large_sample_validation",
                "online_question_longrun_validation",
            ]:
                self.assertTrue(any(row["check_id"] == check_id for row in production["checks"]))
            online = next(row for row in production["checks"] if row["check_id"] == "online_question_longrun_validation")
            self.assertEqual(online["status"], "PASS")

    def test_cache_cleanup_is_allowlisted_and_dry_run_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            target = project / "v5" / "object_store" / "last_download_manifest.json"
            target.parent.mkdir(parents=True)
            target.write_text("{}", encoding="utf-8")

            dry = cleanup_data_cache(project, target="last_download_manifest", dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertTrue(target.exists())

            real = cleanup_data_cache(project, target="last_download_manifest", dry_run=False)
            self.assertFalse(real["dry_run"])
            self.assertFalse(target.exists())
            with self.assertRaises(ValueError):
                cleanup_data_cache(project, target="data", dry_run=False)


def _write_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "results").mkdir()
    (project / "reports").mkdir()
    (project / "v5").mkdir()
    con = sqlite3.connect(project / "evidence.sqlite")
    con.execute(
        """
        CREATE TABLE evidence_item (
            evidence_id TEXT, project_id TEXT, entity_symbol TEXT, evidence_type TEXT, review_status TEXT
        )
        """
    )
    con.execute("INSERT INTO evidence_item VALUES ('ev1', 'demo', 'IL6', 'bulk_deg', 'PENDING')")
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
