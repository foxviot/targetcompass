import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.agent_roles import write_agent_role_manifest
from targetcompass_lite.deg import run_deg
from targetcompass_lite.webapp import _v4_work_order_panel


CARD = """dataset_id: ds_exec
source: local
accession: EXEC001
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 2
  control_n: 2
  donor_n: 4
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_exec/expression_matrix.tsv
  metadata: data/ds_exec/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_exec").mkdir(parents=True)
    (project / "dataset_cards" / "ds_exec.yaml").write_text(CARD, encoding="utf-8")
    (project / "data" / "ds_exec" / "expression_matrix.tsv").write_text(
        "gene_symbol\tY1\tY2\tA1\tA2\nIL6\t1\t2\t8\t9\nCXCL8\t2\t2\t10\t11\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_exec" / "metadata.tsv").write_text(
        "sample_id\tgroup\nY1\tyoung\nY2\tyoung\nA1\taged\nA2\taged\n",
        encoding="utf-8",
    )
    return project


class ExecutorAndRolesTest(unittest.TestCase):
    def test_bulk_deg_writes_executor_contract_manifest(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"TARGETCOMPASS_DEG_RUNNER": "python"}):
            project = _write_project(tmp)
            run_deg(project, "ds_exec")
            manifest_path = project / "results" / "bulk_deg_ds_exec" / "executor_manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "executor_artifact_manifest_v1")
            self.assertEqual(manifest["status"], "success")
            self.assertTrue(manifest["resume_key"].startswith("resume_"))
            self.assertEqual(manifest["contract"]["nextflow_compatible"]["profile"], "local")
            self.assertTrue(manifest["artifacts"])

    def test_agent_role_manifest_and_panel_are_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            write_agent_role_manifest(project, {"planner": {"analysis_plan": "analysis_plan.json"}})
            html = _v4_work_order_panel(project)
            self.assertIn("v4 Agent role split", html)
            self.assertIn("disease_normalizer", html)
            self.assertIn("report_writer", html)


if __name__ == "__main__":
    unittest.main()
