import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.spec_builder import confirm_project_spec, parse_interest, readiness_errors, update_project_spec


class SpecBuilderTest(unittest.TestCase):
    def test_parse_vascular_endothelial_interest(self):
        spec = parse_interest(
            "Find secreted or surface targets for human endothelial senescence in vascular aging.",
            "demo",
        )
        self.assertEqual(spec["disease_scope"]["canonical"], "vascular aging")
        self.assertIn("human", spec["organisms"])
        self.assertIn("vascular endothelium", spec["priority_tissues"])
        self.assertIn("endothelial cell", spec["priority_cells"])
        self.assertIn("secreted", spec["target_routes"])
        self.assertIn("surface", spec["target_routes"])
        self.assertEqual(spec["parser_metadata"]["parser_version"], "rule_based_v0")

    def test_parse_chinese_interest(self):
        spec = parse_interest(
            "\u7814\u7a76\u4eba\u7c7b\u52a8\u8109\u5185\u76ae\u7ec6\u80de\u8870\u8001\u4e2d\u7684\u5206\u6ccc\u56e0\u5b50\u548c\u819c\u86cb\u767d\u9776\u70b9",
            "demo",
        )
        self.assertEqual(spec["disease_scope"]["canonical"], "endothelial senescence")
        self.assertIn("human", spec["organisms"])
        self.assertIn("artery", spec["priority_tissues"])
        self.assertIn("endothelial cell", spec["priority_cells"])
        self.assertIn("secreted", spec["target_routes"])
        self.assertIn("surface", spec["target_routes"])

    def test_nonsense_interest_is_not_ready(self):
        spec = parse_interest("123456", "demo")
        self.assertEqual(spec["disease_scope"]["canonical"], "unknown")
        self.assertEqual(spec["parser_metadata"]["confidence"], "low")
        self.assertTrue(readiness_errors(spec))

    def test_parse_arterial_ageing_synonyms(self):
        spec = parse_interest("Identify surface targets for arterial ageing in human aorta.", "demo")
        self.assertEqual(spec["disease_scope"]["canonical"], "vascular aging")
        self.assertIn("artery", spec["priority_tissues"])
        self.assertFalse(readiness_errors(spec))

    def test_parse_atheroma_and_vascular_inflammation(self):
        spec = parse_interest("Screen secretome targets connected to vascular inflammation and atheroma.", "demo")
        self.assertIn(spec["disease_scope"]["canonical"], {"vascular aging", "atherosclerosis"})
        self.assertFalse(readiness_errors(spec))

    def test_update_project_spec_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "example_demo"
            project.mkdir()
            spec = update_project_spec(project, "Human pulmonary fibrosis endothelial secretome")
            self.assertTrue((project / "research_interest.md").exists())
            loaded = json.loads((project / "research_spec.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded["project_id"], "example_demo")
            self.assertEqual(loaded["disease_scope"]["canonical"], spec["disease_scope"]["canonical"])

    def test_gpt_parser_requires_confirmation_before_running(self):
        fake_spec = {
            "project_id": "demo",
            "goal": "target_prioritization",
            "research_theme": "vascular aging targets",
            "disease_scope": {"canonical": "vascular aging", "related_phenotypes": []},
            "organisms": ["human"],
            "priority_tissues": ["vascular endothelium"],
            "priority_cells": ["endothelial cell"],
            "target_routes": ["secreted"],
            "modalities_mvp": {"required": ["bulk_expression"], "optional": []},
            "constraints": {},
            "parser_metadata": {
                "parser_version": "openai_responses_v1",
                "confidence": "requires_user_review",
                "confirmation_required": True,
                "confirmed": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "example_demo"
            project.mkdir()
            with patch("targetcompass_lite.spec_builder.parse_with_openai", return_value=fake_spec):
                spec = update_project_spec(project, "vascular aging targets", parser="gpt")
            self.assertIn("ResearchSpec requires user confirmation before running.", readiness_errors(spec))
            confirmed = confirm_project_spec(project)
            self.assertTrue(confirmed["parser_metadata"]["confirmed"])
            self.assertNotIn("ResearchSpec requires user confirmation before running.", readiness_errors(confirmed))


if __name__ == "__main__":
    unittest.main()
