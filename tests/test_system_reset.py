import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.reset_demo import reset_demo_outputs
from targetcompass_lite.system_status import system_status
from targetcompass_lite.webapp import _page


class SystemResetTest(unittest.TestCase):
    def test_system_status_reports_core_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "dataset_cards").mkdir(parents=True)
            (project / "dataset_cards" / "x.yaml").write_text("dataset_id: x\n", encoding="utf-8")
            rows = system_status(project)
            names = {row["name"] for row in rows}
            self.assertIn("Python", names)
            self.assertIn("LLM API key", names)
            self.assertIn("Dataset cards", names)

    def test_reset_demo_outputs_preserves_configs_and_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "configs").mkdir(parents=True)
            (project / "data").mkdir()
            (project / "results").mkdir()
            (project / "reports").mkdir()
            (project / "configs" / "secrets.local.json").write_text("{}", encoding="utf-8")
            (project / "results" / "x.txt").write_text("x", encoding="utf-8")
            (project / "candidate_scores.csv").write_text("x", encoding="utf-8")
            removed = reset_demo_outputs(project)
            self.assertIn("results", removed)
            self.assertFalse((project / "candidate_scores.csv").exists())
            self.assertTrue((project / "configs" / "secrets.local.json").exists())
            self.assertTrue((project / "data").exists())

    def test_page_renders_system_status_and_api_key_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "configs").mkdir(parents=True)
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            html = _page(project).decode("utf-8")
            self.assertIn("系统状态", html)
            self.assertIn("API Key", html)
            self.assertIn("清空输出并重建 Demo", html)


if __name__ == "__main__":
    unittest.main()
