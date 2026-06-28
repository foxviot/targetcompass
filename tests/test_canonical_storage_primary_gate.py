import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.storage_primary_gate import build_storage_primary_gate


class CanonicalStoragePrimaryGateTest(unittest.TestCase):
    def test_gate_blocks_without_active_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            gate = build_storage_primary_gate(project)

            self.assertEqual(gate["status"], "BLOCKED")
            self.assertFalse(gate["primary_path"]["is_postgres_minio_primary"])

    def test_gate_recognizes_active_postgres_minio_and_legacy_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5").mkdir(parents=True)
            (project / "reports").mkdir(parents=True)
            (project / "reports" / "target_report.html").write_text("<html></html>", encoding="utf-8")
            (project / "v5" / "active_backends.json").write_text(
                json.dumps(
                    {
                        "status": "ACTIVE",
                        "active_backends": {"evidence_db": "postgres_local", "object_store": "minio_local"},
                        "policy": {"read_preference": "postgres_local_then_sqlite", "artifact_write_preference": "minio_local_then_filesystem"},
                    }
                ),
                encoding="utf-8",
            )

            gate = build_storage_primary_gate(project)

            self.assertEqual(gate["status"], "READY_WITH_WARNINGS")
            self.assertTrue(gate["primary_path"]["is_postgres_minio_primary"])
            self.assertTrue(gate["legacy_local_writers"])

    def test_gate_does_not_count_outputs_with_primary_backend_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "storage").mkdir(parents=True)
            (project / "reports").mkdir(parents=True)
            (project / "reports" / "target_report.html").write_text("<html></html>", encoding="utf-8")
            (project / "v5" / "active_backends.json").write_text(
                json.dumps(
                    {
                        "status": "ACTIVE",
                        "active_backends": {"evidence_db": "postgres_local", "object_store": "minio_local"},
                        "policy": {"read_preference": "postgres_local_then_sqlite", "artifact_write_preference": "minio_local_then_filesystem"},
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "storage" / "backend_writes.jsonl").write_text(
                json.dumps(
                    {
                        "relative_path": "reports/target_report.html",
                        "primary_backend": "minio_local",
                        "primary_write": {"status": "PASS"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            gate = build_storage_primary_gate(project)

            self.assertEqual(gate["status"], "READY")
            self.assertEqual(gate["legacy_local_writers"], [])


if __name__ == "__main__":
    unittest.main()
