import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.platform_config import (
    build_post_install_setup_wizard,
    load_platform_config,
    platform_readiness,
    save_platform_config,
    service_status,
    write_pre_release_scripts,
    write_update_manifest,
)
from targetcompass_lite.secrets import load_secrets
from targetcompass_lite.secrets import save_openai_api_key


class PlatformConfigTest(unittest.TestCase):
    def test_save_config_keeps_key_out_of_platform_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "configs").mkdir(parents=True)

            cfg = save_platform_config(
                project,
                provider="deepseek",
                base_url="https://api.deepseek.com/",
                model="deepseek-chat",
                api_key="sk-test-secret",
                ui_port="8899",
                docker_enabled=True,
                rscript_path="Rscript",
                nextflow_path="nextflow",
            )

            self.assertEqual(cfg["llm"]["api_key_status"], "set")
            self.assertEqual(cfg["llm"]["base_url"], "https://api.deepseek.com")
            self.assertEqual(cfg["ui_port"], 8899)
            config_text = (project / "v5" / "platform" / "platform_config.json").read_text(encoding="utf-8")
            self.assertNotIn("sk-test-secret", config_text)
            self.assertEqual(load_secrets(project)["OPENAI_API_KEY"], "sk-test-secret")

    def test_readiness_and_service_status_write_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            save_platform_config(project, ui_port="8898")

            with patch("targetcompass_lite.platform_config._docker_daemon_running", return_value=False):
                readiness = platform_readiness(project)
            status = service_status(project, port=8898)
            update = write_update_manifest(project, version="0.5.test")

            self.assertIn(readiness["status"], {"FAIL", "WARN", "PASS"})
            self.assertEqual(status["ui"]["port"], 8898)
            self.assertEqual(update["current_version"], "0.5.test")
            self.assertTrue((project / "v5" / "platform" / "platform_readiness.json").exists())
            self.assertTrue((project / "v5" / "platform" / "service_status.json").exists())
            self.assertTrue((project / "v5" / "platform" / "update_manifest.json").exists())

    def test_load_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            cfg = load_platform_config(project)
            self.assertEqual(cfg["project_id"], "demo")
            self.assertIn("llm", cfg)

    def test_readiness_uses_project_secret_for_llm_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            save_openai_api_key(project, "sk-test-secret")

            readiness = platform_readiness(project)
            checks = {row["check_id"]: row for row in readiness["checks"]}

            self.assertEqual(checks["llm_api_key"]["status"], "PASS")

    def test_running_ui_port_is_not_reported_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            save_platform_config(project, ui_port="8898")

            with patch("targetcompass_lite.platform_config._port_available", return_value=False), patch(
                "targetcompass_lite.platform_config.urllib.request.urlopen"
            ) as opened:
                opened.return_value.__enter__.return_value.status = 200
                readiness = platform_readiness(project)
            checks = {row["check_id"]: row for row in readiness["checks"]}

            self.assertEqual(checks["ui_port"]["status"], "PASS")

    def test_setup_wizard_and_pre_release_scripts_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            save_platform_config(project, ui_port="8898")

            wizard = build_post_install_setup_wizard(project)
            scripts = write_pre_release_scripts(project, question_count=12)

            self.assertEqual(wizard["schema_version"], "v5.post_install_setup_wizard/0.1")
            self.assertTrue(any(row["step_id"] == "runtime_paths" for row in wizard["steps"]))
            self.assertTrue(Path(scripts["scripts"]["powershell"]).exists())
            self.assertIn("--question-count $QuestionCount", Path(scripts["scripts"]["powershell"]).read_text(encoding="utf-8"))
            self.assertTrue((project / "v5" / "platform" / "pre_release_scripts.json").exists())


if __name__ == "__main__":
    unittest.main()
