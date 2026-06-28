import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.external_agent_adapter import run_bioinfo_agent_adapter


class ExternalAgentAdapterTest(unittest.TestCase):
    def test_adapter_synthesizes_task_packets_when_mock_agent_rejects_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            agent_root = Path(tmp) / "agent"
            script_dir = agent_root / "scripts"
            script_dir.mkdir(parents=True)
            (script_dir / "run_mock_pipeline.py").write_text(
                "import sys\n"
                "print('This mock pipeline currently supports only the bundled example question.', file=sys.stderr)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )

            result = run_bioinfo_agent_adapter(
                project,
                "肌少症的患者肌肉背景细胞中是否存在有特征性表面分子的sasp评分高的细胞",
                agent_root,
            )

            self.assertEqual(result["mode"], "schema_compatible_synthesis")
            self.assertEqual(result["codex_task_packet_count"], 8)
            packets = json.loads((project / "external_agent_runs" / "bioinfo_agent_system" / "codex_task_packets.json").read_text(encoding="utf-8"))
            self.assertEqual(packets["packets"][0]["method_contract_id"], "question_normalization")
            plan = json.loads((project / result["plan_ref"]).read_text(encoding="utf-8"))
            self.assertEqual(plan["claim_ceiling"]["max_allowed_claim"], "co_expression")
            self.assertIn("SASP", plan["normalized_research_question"])


if __name__ == "__main__":
    unittest.main()
