import tomllib
import unittest
from pathlib import Path

from targetcompass_lite.cli import main


ROOT = Path(__file__).resolve().parents[1]


class PackagingTest(unittest.TestCase):
    def test_pyproject_declares_console_scripts(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = data["project"]["scripts"]
        self.assertEqual(scripts["targetcompass-lite"], "targetcompass_lite.cli:main")
        self.assertEqual(scripts["tc-lite"], "targetcompass_lite.cli:main")

    def test_cli_entrypoint_is_importable(self):
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
