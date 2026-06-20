import json
import unittest
from unittest.mock import patch

from targetcompass_lite.llm_parser import _extract_response_text, parse_with_openai


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "goal": "target_prioritization",
                                        "research_theme": "vascular aging secreted targets",
                                        "disease_scope": {
                                            "canonical": "vascular aging",
                                            "related_phenotypes": ["endothelial senescence"],
                                        },
                                        "organisms": ["human"],
                                        "priority_tissues": ["vascular endothelium"],
                                        "priority_cells": ["endothelial cell"],
                                        "target_routes": ["secreted"],
                                    }
                                ),
                            }
                        ]
                    }
                ]
            }
        ).encode("utf-8")


class LlmParserTest(unittest.TestCase):
    def test_extract_response_text(self):
        text = _extract_response_text({"output": [{"content": [{"type": "output_text", "text": "{}"}]}]})
        self.assertEqual(text, "{}")

    def test_parse_with_openai_adds_project_defaults_and_confirmation_gate(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "targetcompass_lite.llm_parser.urllib.request.urlopen", return_value=_FakeResponse()
        ):
            spec = parse_with_openai("vascular aging secreted targets", "demo")
        self.assertEqual(spec["project_id"], "demo")
        self.assertEqual(spec["disease_scope"]["canonical"], "vascular aging")
        self.assertEqual(spec["parser_metadata"]["parser_version"], "openai_responses_v1")
        self.assertTrue(spec["parser_metadata"]["confirmation_required"])
        self.assertFalse(spec["parser_metadata"]["confirmed"])

    def test_parse_with_openai_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                parse_with_openai("vascular aging", "demo")


if __name__ == "__main__":
    unittest.main()
