import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.canonical.llm_role_execution import (
    execute_llm_role,
    load_llm_role_audit,
    prepare_llm_role_request,
    validate_llm_role_output,
)
from targetcompass_lite.canonical.agent_specs import build_agent_specs


def fake_chat_response(content):
    return {"choices": [{"message": {"content": json.dumps(content)}}]}


class CanonicalLlmRoleExecutionTest(unittest.TestCase):
    def test_prepare_blocks_without_api_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            project_dir = Path(tmp) / "demo_project"
            request = prepare_llm_role_request(project_dir, "question_normalizer", input_refs={"user_question": "question"})
            self.assertEqual(request["status"], "blocked_missing_api_key")
            self.assertTrue((project_dir / "v5" / "llm_roles" / "requests" / f"{request['request_id']}.json").exists())

    def test_execute_without_api_key_returns_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            project_dir = Path(tmp) / "demo_project"
            result = execute_llm_role(project_dir, "question_normalizer", input_refs={"user_question": "question"})
            self.assertEqual(result["status"], "blocked")
            self.assertIn("OPENAI_API_KEY", result["failure_reason"])
            self.assertEqual(load_llm_role_audit(project_dir)[-1]["status"], "blocked")

    def test_execute_valid_llm_role_output(self):
        captured = {}

        def fake_caller(url, payload, headers, timeout):
            captured["url"] = url
            captured["headers"] = headers
            return fake_chat_response(
                {
                    "agent_id": "question_normalizer",
                    "status": "success",
                    "output_object_refs": [{"object_type": "ResearchSpec", "object_id": "rs1"}],
                    "assumptions": [],
                    "open_questions": [],
                    "blocking_issues": [],
                    "claim_ceiling": {"max_allowed_claim": "descriptive", "reason": "No empirical evidence."},
                    "audit_notes": [],
                }
            )

        env = {
            "OPENAI_API_KEY": "sk-test-secret",
            "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
            "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True):
            project_dir = Path(tmp) / "demo_project"
            result = execute_llm_role(project_dir, "question_normalizer", input_refs={"user_question": "question"}, chat_caller=fake_caller)
            self.assertEqual(result["status"], "executed")
            self.assertEqual(result["schema_validation"]["valid"], True)
            self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
            self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-test-secret")

            all_text = "\n".join(path.read_text(encoding="utf-8") for path in (project_dir / "v5" / "llm_roles").rglob("*.json"))
            self.assertNotIn("sk-test-secret", all_text)

    def test_claim_ceiling_above_agent_max_fails(self):
        def fake_caller(url, payload, headers, timeout):
            return fake_chat_response(
                {
                    "agent_id": "question_normalizer",
                    "status": "success",
                    "output_object_refs": [],
                    "assumptions": [],
                    "open_questions": [],
                    "blocking_issues": [],
                    "claim_ceiling": {"max_allowed_claim": "association", "reason": "Too high for normalizer."},
                    "audit_notes": [],
                }
            )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            result = execute_llm_role(Path(tmp) / "demo_project", "question_normalizer", input_refs={}, chat_caller=fake_caller)
            self.assertEqual(result["status"], "failed")
            self.assertIn("claim ceiling exceeds", result["failure_reason"])

    def test_invalid_json_response_fails(self):
        def fake_caller(url, payload, headers, timeout):
            return {"choices": [{"message": {"content": "not json"}}]}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            result = execute_llm_role(Path(tmp) / "demo_project", "scope_resolver", input_refs={}, chat_caller=fake_caller)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["failure_reason"])

    def test_malformed_deepseek_style_output_returns_schema_errors(self):
        spec = build_agent_specs()["question_normalizer"]
        result = validate_llm_role_output(
            {
                "agent_id": "question_normalizer",
                "status": "success",
                "output_object_refs": {"research_spec": "rs1"},
                "assumptions": [],
                "open_questions": [],
                "blocking_issues": [],
                "claim_ceiling": "descriptive",
                "audit_notes": "not a list",
            },
            spec,
        )
        self.assertFalse(result["valid"])
        self.assertIn("output_object_refs: expected list", result["errors"])
        self.assertIn("claim_ceiling: expected object", result["errors"])
        self.assertIn("audit_notes: expected list", result["errors"])

    def test_handoff_malformed_refs_do_not_raise(self):
        spec = build_agent_specs()["question_normalizer"]
        result = validate_llm_role_output(
            {
                "agent_id": "question_normalizer",
                "status": "success",
                "output_object_refs": [{"object_type": "ResearchSpec", "object_id": "rs1"}],
                "assumptions": [],
                "open_questions": [],
                "blocking_issues": [],
                "claim_ceiling": {"max_allowed_claim": "descriptive", "reason": "No evidence."},
                "audit_notes": [],
                "handoff": {
                    "handoff_id": "h1",
                    "schema_version": "v5.agent_handoff/0.1",
                    "project_id": "demo",
                    "from_agent": "question_normalizer",
                    "to_agent": "scope_resolver",
                    "created_at": "2026-06-23T00:00:00+00:00",
                    "input_object_refs": ["bad_ref"],
                    "output_object_refs": [{"object_type": "ResearchSpec", "object_id": "rs1"}],
                    "evidence_refs": [],
                    "artifact_refs": [],
                    "assumptions": [],
                    "open_questions": [],
                    "blocking_issues": [],
                    "claim_ceiling": {"max_allowed_claim": "descriptive", "reason": "No evidence."},
                    "audit_notes": [],
                    "payload_hash": "hash",
                },
            },
            spec,
        )
        self.assertFalse(result["valid"])
        self.assertIn("handoff: input_object_refs[0]: expected object ref", result["errors"])


if __name__ == "__main__":
    unittest.main()
