import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.package import export_run_package
from targetcompass_lite.secrets import (
    apply_project_secrets,
    clear_openai_api_key,
    load_secrets,
    masked_openai_key,
    save_openai_api_key,
    secrets_path,
)


class SecretsTest(unittest.TestCase):
    def test_openai_key_can_be_saved_masked_applied_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            project = Path(tmp) / "demo"
            save_openai_api_key(project, "sk-test-1234567890")
            self.assertEqual(load_secrets(project)["OPENAI_API_KEY"], "sk-test-1234567890")
            self.assertEqual(masked_openai_key(project), "sk-tes...7890")
            self.assertEqual(__import__("os").environ["OPENAI_API_KEY"], "sk-test-1234567890")
            __import__("os").environ.pop("OPENAI_API_KEY")
            apply_project_secrets(project)
            self.assertEqual(__import__("os").environ["OPENAI_API_KEY"], "sk-test-1234567890")
            clear_openai_api_key(project)
            self.assertFalse(secrets_path(project).exists())

    def test_run_package_does_not_include_local_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "configs").mkdir(parents=True)
            (project / "research_spec.json").write_text("{}", encoding="utf-8")
            save_openai_api_key(project, "sk-test-1234567890")
            package = export_run_package(project)
            with zipfile.ZipFile(package) as zf:
                self.assertNotIn("configs/secrets.local.json", zf.namelist())


if __name__ == "__main__":
    unittest.main()
