import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.v5_package_acceptance import _validate_installer_zip, run_acceptance


class V5PackageAcceptanceTest(unittest.TestCase):
    def test_validate_installer_zip_checks_required_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            installer = Path(tmp) / "TargetCompassV5_Windows_Installer.zip"
            manifest = {
                "requires_preinstalled_python": False,
                "runtime_strategy": "embedded_python_with_optional_offline_cache",
                "default_demo_project": "vascular_aging_demo",
                "install_self_checks": ["embedded_python", "v5-doctor"],
                "diagnostic_repair": {"script": "Repair-TargetCompassV5.ps1"},
                "launcher_cmd": "TargetCompassV5-Launcher.cmd",
                "service_management": {"start": "TargetCompassV5-Launcher.cmd"},
            }
            with zipfile.ZipFile(installer, "w") as zf:
                for name in [
                    "Install-TargetCompassV5.ps1",
                    "Launch-TargetCompassV5.ps1",
                    "TargetCompassV5-Launcher.cmd",
                    "Repair-TargetCompassV5.ps1",
                    "Uninstall-TargetCompassV5.ps1",
                    "README_CN.md",
                    "TargetCompassV5.iss",
                    "build_setup_exe.ps1",
                    "packaging_profile.json",
                    "dependency_cache_manifest.json",
                    "runtime_repair_plan.json",
                    "payload/targetcompass_v5_local_bundle.zip",
                    "runtime_cache/README.md",
                    "wheelhouse/README.md",
                ]:
                    zf.writestr(name, "x")
                zf.writestr("installer_manifest.json", json.dumps(manifest))

            result = _validate_installer_zip(installer)

            self.assertEqual(result["status"], "PASS")
            self.assertFalse(result["missing_entries"])

    def test_acceptance_runner_writes_structured_report(self):
        fake_steps = []

        def fake_run_step(steps, step_id, command, timeout):
            row = {
                "step_id": step_id,
                "status": "PASS",
                "returncode": 0,
                "duration_seconds": 0.01,
                "timeout_seconds": timeout,
                "command": " ".join(command),
                "stdout": "dist/fake.zip\n",
                "stderr": "",
                "stdout_tail": "dist/fake.zip\n",
                "stderr_tail": "",
                "failure_reason": "",
            }
            steps.append(row)
            fake_steps.append(row)
            return row

        fake_validation = {"step_id": "validate_installer_zip", "status": "PASS", "failure_reason": ""}
        with patch("scripts.v5_package_acceptance._run_step", side_effect=fake_run_step), patch(
            "scripts.v5_package_acceptance._validate_installer_zip", return_value=fake_validation
        ):
            report = run_acceptance(suite="quick", timeout=60)

        self.assertEqual(report["schema_version"], "v5.package_acceptance/0.1")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["suite"], "quick")
        self.assertGreaterEqual(len(report["steps"]), 4)


if __name__ == "__main__":
    unittest.main()
