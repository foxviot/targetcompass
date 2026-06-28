import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import scripts.export_v5_local_bundle as bundle
from targetcompass_lite.packaging_profiles import build_dependency_cache_manifest, build_packaging_profile, build_runtime_repair_plan


class PackagingProfilesTest(unittest.TestCase):
    def test_profiles_and_dependency_manifest_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packaging" / "windows_v5" / "runtime_cache").mkdir(parents=True)
            (root / "packaging" / "windows_v5" / "wheelhouse").mkdir(parents=True)
            (root / "packaging" / "windows_v5" / "wheelhouse" / "demo.whl").write_text("x", encoding="utf-8")

            professor = build_packaging_profile("professor_demo")
            developer = build_packaging_profile("developer")
            deps = build_dependency_cache_manifest(root)
            repair = build_runtime_repair_plan(root)

            self.assertFalse(professor["include_tests"])
            self.assertTrue(developer["include_tests"])
            self.assertEqual(deps["python"]["wheel_count"], 1)
            self.assertTrue(any(row["repair_id"] == "repair_nextflow" for row in repair["repairs"]))

    def test_export_bundle_uses_profile_and_skips_tests_for_professor_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            (root / "targetcompass_lite").mkdir()
            (root / "targetcompass_lite" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("x", encoding="utf-8")
            (root / "tc_lite.py").write_text("print('ok')", encoding="utf-8")
            (root / "README.md").write_text("readme", encoding="utf-8")

            with patch.object(bundle, "ROOT", root), patch.object(bundle, "OUT_DIR", dist):
                out = bundle.export_v5_local_bundle(profile="professor_demo")

            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                manifest = json.loads(zf.read("v5_local_bundle_manifest.json").decode("utf-8"))

            self.assertEqual(manifest["profile"], "professor_demo")
            self.assertIn("targetcompass_lite/__init__.py", names)
            self.assertNotIn("tests/test_demo.py", names)
            self.assertIn("packaging_manifests/packaging_profile.json", names)


if __name__ == "__main__":
    unittest.main()
