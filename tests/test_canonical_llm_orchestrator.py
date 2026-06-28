import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.canonical.llm_orchestrator import CANONICAL_AGENT_ORDER, run_canonical_llm_roles
from targetcompass_lite.secrets import save_llm_provider, save_openai_api_key


class CanonicalLlmOrchestratorTest(unittest.TestCase):
    def test_runs_all_seven_roles_with_schema_validation(self):
        calls = []

        def fake_caller(url, payload, headers, timeout):
            content = json.loads(payload["messages"][1]["content"])
            agent_id = content["agent_id"]
            calls.append(agent_id)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "agent_id": agent_id,
                                    "status": "success",
                                    "output_object_refs": [{"object_type": "Ref", "object_id": agent_id}],
                                    "assumptions": [],
                                    "open_questions": [],
                                    "blocking_issues": [],
                                    "claim_ceiling": {"max_allowed_claim": "descriptive", "reason": "test"},
                                    "audit_notes": [],
                                }
                            )
                        }
                    }
                ]
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            project = Path(tmp) / "demo"
            run = run_canonical_llm_roles(project, user_question="question", max_retries=0, chat_caller=fake_caller)

            self.assertEqual(calls, CANONICAL_AGENT_ORDER)
            self.assertEqual(run["executed_count"], 7)
            self.assertEqual(run["status"], "completed")
            self.assertTrue((project / "v5" / "llm_roles" / "llm_orchestration_run.json").exists())

    def test_invalid_llm_output_retries_then_falls_back(self):
        def bad_caller(url, payload, headers, timeout):
            return {"choices": [{"message": {"content": "{}"}}]}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            run = run_canonical_llm_roles(Path(tmp) / "demo", user_question="question", max_retries=1, chat_caller=bad_caller)

            self.assertEqual(run["fallback_count"], 7)
            self.assertEqual(run["status"], "completed")
            self.assertTrue(all(len(row["attempts"]) == 3 for row in run["role_runs"]))

    def test_project_secrets_are_applied_before_llm_execution(self):
        captured = {}

        def fake_caller(url, payload, headers, timeout):
            captured["url"] = url
            captured["authorization"] = headers.get("Authorization", "")
            content = json.loads(payload["messages"][1]["content"])
            agent_id = content["agent_id"]
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "agent_id": agent_id,
                                    "status": "success",
                                    "output_object_refs": [{"object_type": "Ref", "object_id": agent_id}],
                                    "assumptions": [],
                                    "open_questions": [],
                                    "blocking_issues": [],
                                    "claim_ceiling": {"max_allowed_claim": "descriptive", "reason": "test"},
                                    "audit_notes": [],
                                }
                            )
                        }
                    }
                ]
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            project = Path(tmp) / "demo"
            save_llm_provider(project, "deepseek", base_url="https://api.deepseek.com", model="deepseek-chat")
            save_openai_api_key(project, "sk-project-secret")

            run = run_canonical_llm_roles(project, user_question="question", max_retries=0, chat_caller=fake_caller)

            self.assertEqual(run["executed_count"], 7)
            self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
            self.assertEqual(captured["authorization"], "Bearer sk-project-secret")


if __name__ == "__main__":
    unittest.main()
