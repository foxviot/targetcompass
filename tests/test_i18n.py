import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.i18n import get_language, set_language, translator
from targetcompass_lite.webapp import _page


class I18nTest(unittest.TestCase):
    def test_language_defaults_to_chinese_and_can_switch_to_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "configs").mkdir(parents=True)
            self.assertEqual(get_language(project), "zh")
            self.assertEqual(set_language(project, "en"), "en")
            lang, t = translator(project)
            self.assertEqual(lang, "en")
            self.assertEqual(t("hero_title"), "Generate, audit, then run.")

    def test_web_page_uses_selected_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "configs").mkdir(parents=True)
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            set_language(project, "zh")
            html = _page(project).decode("utf-8")
            self.assertIn("生成、审查、再执行。", html)
            set_language(project, "en")
            html = _page(project).decode("utf-8")
            self.assertIn("Generate, audit, then run.", html)
            self.assertIn("切换到中文", html)


if __name__ == "__main__":
    unittest.main()
