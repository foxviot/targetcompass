import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.agent_roles import write_agent_role_manifest
from targetcompass_lite.role_runner import load_role_runs, run_role
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

    def test_role_runner_writes_input_output_log_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            output, record = run_role(
                project,
                "planner",
                {"eligible_datasets": "eligible_datasets.csv"},
                lambda: {"modules": [{"module_id": "P1"}]},
                runner="unit_test_runner",
            )
            self.assertEqual(output["modules"][0]["module_id"], "P1")
            self.assertEqual(record["status"], "success")
            self.assertTrue((project / record["input_packet"]).exists())
            self.assertTrue((project / record["output_packet"]).exists())
            self.assertTrue((project / record["log"]).exists())
            runs = load_role_runs(project)["runs"]
            self.assertEqual(runs[0]["role_id"], "planner")
            self.assertTrue((project / "v4" / "agent_roles.json").exists())
            html = _v4_work_order_panel(project)
            self.assertIn("v4 Role runs", html)
            self.assertIn("unit_test_runner", json.dumps(runs))

    def test_role_runner_records_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            with self.assertRaises(RuntimeError):
                run_role(project, "dataset_scout", {"research_spec": "missing"}, lambda: (_ for _ in ()).throw(ValueError("boom")))
            runs = load_role_runs(project)["runs"]
            self.assertEqual(runs[0]["status"], "failed")
            self.assertIn("boom", runs[0]["failure_reason"])


if __name__ == "__main__":
    unittest.main()
