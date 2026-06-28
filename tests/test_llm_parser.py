import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.llm_gateway import execute_llm_task_packet
from targetcompass_lite.llm_parser import _extract_chat_completion_text, _extract_response_text, parse_with_openai


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


class _FakeChatResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "goal": "target_prioritization",
                                    "research_theme": "diabetes muscle SASP targets",
                                    "disease_scope": {
                                        "canonical": "type 2 diabetes",
                                        "related_phenotypes": ["skeletal muscle insulin resistance"],
                                    },
                                    "organisms": ["human"],
                                    "priority_tissues": ["skeletal muscle"],
                                    "priority_cells": ["myocyte", "fibro-adipogenic progenitor"],
                                    "target_routes": ["secreted"],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


class _FakeRoleResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "project_id": "demo",
                                    "research_spec_ref": "research_spec.json",
                                    "disease_spec_ref": "research_spec.json",
                                    "normalized_terms": ["vascular aging", "senescence"],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


class LlmParserTest(unittest.TestCase):
    def test_extract_response_text(self):
        text = _extract_response_text({"output": [{"content": [{"type": "output_text", "text": "{}"}]}]})
        self.assertEqual(text, "{}")

    def test_extract_chat_completion_text(self):
        text = _extract_chat_completion_text({"choices": [{"message": {"content": "{}"}}]})
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

    def test_parse_with_deepseek_chat_compatible_provider(self):
        env = {
            "OPENAI_API_KEY": "test-key",
            "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
            "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
            "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
        }
        captured = {}

        def fake_urlopen(req, timeout=60):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _FakeChatResponse()

        with patch.dict("os.environ", env, clear=True), patch("targetcompass_lite.llm_parser.urllib.request.urlopen", side_effect=fake_urlopen):
            spec = parse_with_openai("diabetes muscle SASP targets", "demo")
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["payload"]["model"], "deepseek-chat")
        self.assertEqual(spec["parser_metadata"]["parser_version"], "deepseek_chat_completions_v1")
        self.assertEqual(spec["disease_scope"]["canonical"], "type 2 diabetes")

    def test_llm_execution_artifacts_do_not_persist_api_key_or_bearer_header(self):
        env = {
            "OPENAI_API_KEY": "sk-secret-never-write",
            "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
            "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
            "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
        }
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            with patch.dict("os.environ", env, clear=True), patch("targetcompass_lite.llm_gateway.urllib.request.urlopen", return_value=_FakeRoleResponse()):
                result = execute_llm_task_packet(project, role_id="disease_normalizer", prompt="Normalize vascular aging.", actor="unit_test")

            self.assertEqual(result["status"], "executed")
            files = list((project / "v4" / "llm_tasks").glob("*.json")) + [project / "v4" / "llm_call_audit.jsonl"]
            combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
            self.assertNotIn("sk-secret-never-write", combined)
            self.assertNotIn("Authorization", combined)
            self.assertNotIn("Bearer", combined)


if __name__ == "__main__":
    unittest.main()
