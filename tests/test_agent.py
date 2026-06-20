import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.agent import TargetDiscoveryAgent


CARD = """dataset_id: ds_agent
source: local
accession: AGENT001
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 2
  control_n: 2
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_agent/expression_matrix.tsv
  metadata: data/ds_agent/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_agent").mkdir(parents=True)
    (project / "dataset_cards" / "ds_agent.yaml").write_text(CARD, encoding="utf-8")
    (project / "data" / "ds_agent" / "expression_matrix.tsv").write_text(
        "gene_symbol\tY1\tY2\tA1\tA2\nIL6\t1\t2\t8\t9\nCXCL8\t2\t2\t10\t11\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_agent" / "metadata.tsv").write_text(
        "sample_id\tgroup\nY1\tyoung\nY2\tyoung\nA1\taged\nA2\taged\n",
        encoding="utf-8",
    )
    return project


class AgentTest(unittest.TestCase):
    def test_agent_blocks_unready_research_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("targetcompass_lite.paths.PROJECTS", Path(tmp)):
                _write_project(tmp)
                result = TargetDiscoveryAgent("demo").run("123456", "rule_based", ["ds_agent"], False)
                self.assertEqual(result.status, "blocked")
                self.assertIn("Research direction", result.message)
                trace = json.loads((Path(tmp) / "demo" / "results" / "agent_trace.json").read_text(encoding="utf-8"))
                self.assertEqual(trace["architecture"], "local_state_machine_agent_v1")
                self.assertEqual(
                    [state["state"] for state in trace["state_machine"]],
                    ["generation", "initial_review", "verification", "execution", "final_review", "report"],
                )

    def test_agent_runs_deterministic_workflow_and_writes_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("targetcompass_lite.paths.PROJECTS", Path(tmp)), patch.dict(
                "os.environ", {"TARGETCOMPASS_DEG_RUNNER": "python"}
            ):
                _write_project(tmp)
                result = TargetDiscoveryAgent("demo").run(
                    "Find secreted targets for human endothelial senescence in vascular aging",
                    "rule_based",
                    ["ds_agent"],
                    False,
                )
                self.assertEqual(result.status, "success")
                self.assertTrue((Path(tmp) / "demo" / "reports" / "target_report.html").exists())
                trace = json.loads((Path(tmp) / "demo" / "results" / "agent_trace.json").read_text(encoding="utf-8"))
                stage_names = [stage["name"] for stage in trace["stages"]]
                for stage in ["generation", "initial_review", "verification", "execution", "final_review", "report"]:
                    self.assertIn(stage, stage_names)
                self.assertEqual(trace["v4_compatibility"]["object_manifest"], "v4/object_manifest.json")
                v4_dir = Path(tmp) / "demo" / "v4"
                self.assertTrue((v4_dir / "object_manifest.json").exists())
                self.assertTrue((v4_dir / "evidence_snapshot.json").exists())
                self.assertTrue((v4_dir / "mcp_resources.json").exists())


if __name__ == "__main__":
    unittest.main()
