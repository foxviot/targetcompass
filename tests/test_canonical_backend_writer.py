import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.canonical.backend_writer import backend_write_summary, load_backend_writes, write_json_artifact


class CanonicalBackendWriterTest(unittest.TestCase):
    def test_write_json_artifact_records_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"

            result = write_json_artifact(project, "v5/test/object.json", {"ok": True}, producer="unit", artifact_type="test")

            self.assertEqual(result["primary_backend"], "local_filesystem")
            self.assertTrue((project / "v5" / "test" / "object.json").exists())
            self.assertEqual(len(load_backend_writes(project)), 1)

    def test_minio_primary_write_is_attempted_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5").mkdir(parents=True)
            (project / "v5" / "active_backends.json").write_text(
                json.dumps({"status": "ACTIVE", "active_backends": {"object_store": "minio_local"}}),
                encoding="utf-8",
            )
            with patch("targetcompass_lite.canonical.backend_writer._s3_request", return_value=200):
                result = write_json_artifact(project, "v5/test/object.json", {"ok": True}, producer="unit", artifact_type="test")

            self.assertEqual(result["primary_backend"], "minio_local")
            self.assertEqual(result["primary_write"]["status"], "PASS")
            summary = backend_write_summary(project)
            self.assertEqual(summary["minio_primary_pass_count"], 1)


if __name__ == "__main__":
    unittest.main()
