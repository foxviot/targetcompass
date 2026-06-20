import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.nextflow_plane import build_nextflow_execution_plane, validate_nextflow_execution_plane


class NextflowPlaneTest(unittest.TestCase):
    def test_builds_nextflow_dsl2_execution_plane(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            manifest = build_nextflow_execution_plane(project)
            self.assertEqual(manifest["schema_version"], "v4.nextflow_execution_plane/0.1")
            self.assertIn("local", manifest["profiles"])
            self.assertIn("slurm", manifest["profiles"])
            self.assertGreaterEqual(manifest["module_count"], 5)
            self.assertTrue((project / "workflows" / "target_discovery" / "main.nf").exists())
            self.assertTrue((project / "workflows" / "target_discovery" / "nextflow.config").exists())
            self.assertTrue((project / "workflows" / "target_discovery" / "params.schema.json").exists())
            self.assertTrue((project / "workflows" / "common" / "modules" / "bulk_deg" / "module_contract.json").exists())
            self.assertIn("production containers must replace", " ".join(manifest["limitations"]))

            main_text = (project / "workflows" / "target_discovery" / "main.nf").read_text(encoding="utf-8")
            self.assertIn("nextflow.enable.dsl=2", main_text)
            self.assertIn("include { BULK_DEG }", main_text)
            config_text = (project / "workflows" / "target_discovery" / "nextflow.config").read_text(encoding="utf-8")
            self.assertIn("profiles", config_text)
            self.assertIn("docker.enabled", config_text)

            validation = validate_nextflow_execution_plane(project)
            self.assertEqual(validation["status"], "pass")
            self.assertTrue((project / "workflows" / "target_discovery" / "nextflow_validation.json").exists())


if __name__ == "__main__":
    unittest.main()
