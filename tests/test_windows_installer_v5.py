import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.build_windows_installer_v5 as installer


class WindowsInstallerV5Test(unittest.TestCase):
    def test_installer_bundle_contains_platform_scripts_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bundle = tmp_path / "targetcompass_v5_local_bundle_20990101T000000Z.zip"
            fake_bundle.write_bytes(b"bundle")
            packaging_src = installer.ROOT / "packaging" / "windows_v5"
            packaging_tmp = tmp_path / "windows_v5"
            shutil.copytree(packaging_src, packaging_tmp, ignore=shutil.ignore_patterns("payload"))
            dist_tmp = tmp_path / "dist"
            payload_tmp = packaging_tmp / "payload"
            with patch.object(installer, "PACKAGING_DIR", packaging_tmp), patch.object(installer, "PAYLOAD_DIR", payload_tmp), patch.object(
                installer, "DIST_DIR", dist_tmp
            ), patch("scripts.build_windows_installer_v5._latest_v5_bundle", return_value=fake_bundle):
                out = installer.build_windows_installer_v5(profile="professor_demo")

            self.assertTrue(out.exists())
            import zipfile

            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                manifest = json.loads(zf.read("installer_manifest.json").decode("utf-8"))

            self.assertIn("Install-TargetCompassV5.ps1", names)
            self.assertIn("Launch-TargetCompassV5.ps1", names)
            self.assertIn("TargetCompassV5-Launcher.cmd", names)
            self.assertIn("Stop-TargetCompassV5.ps1", names)
            self.assertIn("Restart-TargetCompassV5.ps1", names)
            self.assertIn("Repair-TargetCompassV5.ps1", names)
            self.assertIn("Uninstall-TargetCompassV5.ps1", names)
            self.assertIn("README_CN.md", names)
            self.assertIn("TargetCompassV5.iss", names)
            self.assertIn("build_setup_exe.ps1", names)
            self.assertIn("runtime_cache/README.md", names)
            self.assertIn("wheelhouse/README.md", names)
            self.assertIn("payload/targetcompass_v5_local_bundle.zip", names)
            self.assertIn("packaging_profile.json", names)
            self.assertIn("dependency_cache_manifest.json", names)
            self.assertIn("runtime_repair_plan.json", names)
            self.assertEqual(manifest["profile"], "professor_demo")
            self.assertFalse(manifest["requires_preinstalled_python"])
            self.assertEqual(manifest["runtime_strategy"], "embedded_python_with_optional_offline_cache")
            self.assertEqual(manifest["formal_installer"]["type"], "Inno Setup")
            self.assertEqual(manifest["formal_installer"]["wizard_style"], "modern")
            self.assertIn("signing", manifest["formal_installer"])
            self.assertEqual(manifest["diagnostic_repair"]["script"], "Repair-TargetCompassV5.ps1")
            self.assertEqual(manifest["launcher_cmd"], "TargetCompassV5-Launcher.cmd")
            self.assertEqual(manifest["service_management"]["start"], "TargetCompassV5-Launcher.cmd")
            self.assertEqual(manifest["service_management"]["stop"], "Stop-TargetCompassV5.ps1")
            self.assertEqual(manifest["service_management"]["restart"], "Restart-TargetCompassV5.ps1")
            self.assertEqual(manifest["default_demo_project"], "vascular_aging_demo")
            self.assertIn("v5-doctor", manifest["install_self_checks"])
            self.assertIn("optional project backup", manifest["uninstall_features"])


if __name__ == "__main__":
    unittest.main()
