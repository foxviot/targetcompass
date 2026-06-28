import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.production_acceptance import (
    run_codex_worker_large_sample_acceptance,
    validate_windows_installer_release,
)


class ProductionAcceptanceTest(unittest.TestCase):
    def test_signature_waiver_is_recorded_without_clean_machine_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            dist = root / "dist"
            dist.mkdir()
            (dist / "TargetCompassV5_Setup.exe").write_bytes(b"setup")
            runtime = root / "packaging" / "windows_v5" / "runtime_cache"
            wheelhouse = root / "packaging" / "windows_v5" / "wheelhouse"
            runtime.mkdir(parents=True)
            wheelhouse.mkdir(parents=True)
            (runtime / "python-embed.zip").write_bytes(b"runtime")
            (wheelhouse / "python_docx.whl").write_bytes(b"wheel")
            waiver = project / "v5" / "packaging" / "signature_waiver.json"
            waiver.parent.mkdir(parents=True)
            waiver.write_text(
                json.dumps({"status": "ACCEPTED", "reason": "development acceptance only"}),
                encoding="utf-8",
            )

            result = validate_windows_installer_release(project)

            signature = json.loads((project / "v5" / "packaging" / "signature_validation.json").read_text(encoding="utf-8"))
            self.assertTrue(signature["waiver"])
            self.assertEqual(signature["status"], "PASS")
            self.assertEqual(result["offline_dependency_manifest"]["status"], "PASS")
            self.assertEqual(result["status"], "REVIEW")

    def test_real_codex_unavailable_does_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            with patch("targetcompass_lite.production_acceptance._codex_cli_status", return_value={"status": "BLOCKED", "blocking_reason": "access denied"}):
                result = run_codex_worker_large_sample_acceptance(project, sample_count=5, real_codex=True)

            self.assertEqual(result["status"], "REVIEW")
            self.assertEqual(result["execution_mode"], "real_codex_unavailable")
            self.assertIn("Codex CLI is not callable", result["blocking_reason"])


if __name__ == "__main__":
    unittest.main()
