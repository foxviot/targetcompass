import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.local_backends import (
    activate_v5_local_backends,
    check_local_backends,
    prepare_local_backend_stack,
    sync_local_backends,
)
from targetcompass_lite.production_storage import build_production_storage_readiness
from targetcompass_lite.services import dispatch_service_request
from targetcompass_lite.storage_manifest import build_storage_manifest


class LocalBackendsTest(unittest.TestCase):
    def test_prepare_generates_compose_env_and_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            stack = prepare_local_backend_stack(project)

            self.assertEqual(stack["schema_version"], "v4.local_backend_stack/0.1")
            self.assertTrue((project / stack["compose_file"]).exists())
            self.assertTrue((project / stack["env_example"]).exists())
            self.assertIn("postgres", (project / stack["compose_file"]).read_text(encoding="utf-8"))
            self.assertIn("minio", (project / stack["compose_file"]).read_text(encoding="utf-8"))
            self.assertTrue((project / "v4" / "local_backend_stack.json").exists())

    def test_check_records_blocked_when_docker_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            with patch("targetcompass_lite.local_backends._docker_bin", return_value=None), patch("targetcompass_lite.local_backends._check_minio") as minio:
                minio.return_value = {"status": "FAIL", "failure_reason": "MinIO endpoint is not reachable.", "bucket_ready": False}
                result = check_local_backends(project)

            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["active_backends"]["evidence_db"], "sqlite_local")
            self.assertEqual(result["active_backends"]["object_store"], "local_filesystem")
            self.assertTrue((project / "v4" / "local_backend_check.json").exists())

    def test_manifest_and_readiness_use_verified_local_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            (project / "v4" / "local_backend_check.json").write_text(
                json.dumps(
                    {
                        "postgres": {"schema_ready": True},
                        "minio": {"bucket_ready": True},
                    }
                ),
                encoding="utf-8",
            )

            manifest = build_storage_manifest(project)
            readiness = build_production_storage_readiness(project)

            self.assertEqual(manifest["active_backends"]["evidence_db"], "postgres_local")
            self.assertEqual(manifest["active_backends"]["object_store"], "minio_local")
            self.assertTrue(readiness["postgres"]["active"])
            self.assertTrue(readiness["object_store"]["active"])

    def test_service_contract_exposes_local_backend_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            prepared = dispatch_service_request("evidence_service", "local_backends_prepare", project, caller="mcp_gateway")["result"]
            with patch("targetcompass_lite.local_backends._docker_bin", return_value=None):
                checked = dispatch_service_request("evidence_service", "local_backends_check", project, caller="mcp_gateway")["result"]

            self.assertEqual(prepared["schema_version"], "v4.local_backend_stack/0.1")
            self.assertEqual(checked["schema_version"], "v4.local_backend_check/0.1")
            self.assertEqual(checked["status"], "BLOCKED")

    def test_sync_writes_manifest_when_backends_are_mocked_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            (project / "reports" / "target_report.html").write_text("<html>demo</html>", encoding="utf-8")

            with patch("targetcompass_lite.local_backends.check_local_backends") as check, patch(
                "targetcompass_lite.local_backends._sync_evidence_to_postgres"
            ) as evidence, patch("targetcompass_lite.local_backends._sync_artifacts_to_minio") as objects:
                check.return_value = {"postgres": {"schema_ready": True}, "minio": {"bucket_ready": True}}
                evidence.return_value = {"status": "PASS", "postgres_row_count": 1}
                objects.return_value = {"status": "PASS", "object_count": 1, "objects": [{"path": "reports/target_report.html"}]}
                result = sync_local_backends(project)

            self.assertEqual(result["status"], "READY")
            self.assertEqual(result["schema_version"], "v4.local_backend_sync/0.1")
            self.assertTrue((project / "v4" / "local_backend_sync.json").exists())

    def test_activate_v5_local_backends_records_active_or_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            with patch("targetcompass_lite.local_backends.check_local_backends") as check, patch("targetcompass_lite.local_backends.sync_local_backends") as sync:
                check.return_value = {"status": "READY", "checks": [], "active_backends": {"evidence_db": "postgres_local", "object_store": "minio_local"}}
                sync.return_value = {"status": "READY"}
                active = activate_v5_local_backends(project)

            self.assertEqual(active["status"], "ACTIVE")
            self.assertEqual(active["active_backends"]["evidence_db"], "postgres_local")
            self.assertTrue((project / "v5" / "active_backends.json").exists())

            with patch("targetcompass_lite.local_backends.check_local_backends") as check:
                check.return_value = {"status": "BLOCKED", "checks": [{"check_id": "postgres_live", "status": "FAIL"}]}
                fallback = activate_v5_local_backends(project)

            self.assertEqual(fallback["status"], "FALLBACK")
            self.assertEqual(fallback["active_backends"]["evidence_db"], "sqlite_local")


def _write_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "results").mkdir()
    (project / "v4").mkdir()
    (project / "research_spec.json").write_text(json.dumps({"project_id": project.name}), encoding="utf-8")
    con = sqlite3.connect(project / "evidence.sqlite")
    con.execute(
        """
        CREATE TABLE evidence_item (
          evidence_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, entity_symbol TEXT NOT NULL,
          entity_type TEXT DEFAULT 'gene', disease_context TEXT, organism TEXT, tissue TEXT, route TEXT,
          evidence_type TEXT NOT NULL, direction TEXT, effect_size REAL, p_value REAL, quality_score REAL,
          evidence_level TEXT, evidence_weight REAL, evidence_basis TEXT, review_status TEXT DEFAULT 'PENDING',
          source_dataset TEXT, artifact_path TEXT, run_id TEXT, artifact_id TEXT, module_version TEXT,
          limitation TEXT, created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        INSERT INTO evidence_item
        (evidence_id, project_id, entity_symbol, evidence_type, created_at)
        VALUES ('ev1', ?, 'CXCL8', 'bulk_deg', 'now')
        """,
        (project.name,),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
