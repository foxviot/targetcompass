import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite import project_manager


class ProjectManagerTest(unittest.TestCase):
    def test_create_archive_export_import_delete_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            with patch.object(project_manager, "PROJECTS", root), patch.object(project_manager, "project_path", lambda project: root / project):
                created = project_manager.create_project("demo")
                self.assertTrue((created / "research_spec.json").exists())

                registry = project_manager.list_projects(root)
                self.assertEqual(registry["project_count"], 1)
                self.assertEqual(registry["projects"][0]["project_id"], "demo")

                meta = project_manager.archive_project("demo", archived=True)
                self.assertTrue(meta["archived"])

                exported = project_manager.export_project("demo")
                self.assertTrue(exported.exists())
                with zipfile.ZipFile(exported) as zf:
                    self.assertFalse(any(name.endswith("secrets.local.json") for name in zf.namelist()))

                imported = project_manager.import_project(exported, project_id="imported_demo")
                self.assertTrue((imported / "research_spec.json").exists())
                self.assertEqual(json.loads((imported / "v5" / "project_meta.json").read_text(encoding="utf-8"))["project_id"], "imported_demo")

                deleted = project_manager.delete_project("imported_demo", backup=False)
                self.assertTrue(deleted["deleted"])
                self.assertFalse((root / "imported_demo").exists())

    def test_clone_template_and_sanitize_project_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            with patch.object(project_manager, "PROJECTS", root), patch.object(project_manager, "project_path", lambda project: root / project):
                template = project_manager.create_project("template")
                (template / "custom.txt").write_text("ok", encoding="utf-8")
                cloned = project_manager.create_project("../clone bad", template_project="template")
                self.assertEqual(cloned.name, "clonebad")
                self.assertEqual((cloned / "custom.txt").read_text(encoding="utf-8"), "ok")


if __name__ == "__main__":
    unittest.main()
