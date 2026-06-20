import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.agent import TargetDiscoveryAgent
from targetcompass_lite.methods import (
    available_methods,
    available_project_methods,
    install_markdown_method,
    load_method_config,
    run_method,
    save_method_config,
)
from targetcompass_lite.methods.contracts import MethodContext


CARD = """dataset_id: ds_method
source: local
accession: METHOD001
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
  expression_matrix: data/ds_method/expression_matrix.tsv
  metadata: data/ds_method/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_method").mkdir(parents=True)
    (project / "configs").mkdir(parents=True)
    (project / "dataset_cards" / "ds_method.yaml").write_text(CARD, encoding="utf-8")
    (project / "data" / "ds_method" / "expression_matrix.tsv").write_text(
        "gene_symbol\tY1\tY2\tA1\tA2\nIL6\t1\t2\t8\t9\nCXCL8\t2\t2\t10\t11\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_method" / "metadata.tsv").write_text(
        "sample_id\tgroup\nY1\tyoung\nY2\tyoung\nA1\taged\nA2\taged\n",
        encoding="utf-8",
    )
    return project


class MethodsTest(unittest.TestCase):
    def test_methods_are_listed_and_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            self.assertIn("query", available_methods())
            self.assertIn("dataset_scout", available_methods())
            self.assertIn("local_dataset_scout_v0", [row["method_id"] for row in available_project_methods(project)["dataset_scout"]])
            self.assertEqual(load_method_config(project)["query"], "local_idea_query_v0")
            saved = save_method_config(
                project,
                {
                    "query": "gpt_review_ready_query_v0",
                    "audit": "strict_feasibility_audit_v0",
                    "experiment": "review_first_experiment_design_v0",
                    "dataset_scout": "local_dataset_scout_v0",
                    "report_writer": "local_report_writer_v0",
                },
            )
            self.assertEqual(saved["audit"], "strict_feasibility_audit_v0")
            self.assertEqual(load_method_config(project)["experiment"], "review_first_experiment_design_v0")
            self.assertEqual(load_method_config(project)["report_writer"], "local_report_writer_v0")

    def test_agent_trace_records_replaceable_method_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("targetcompass_lite.paths.PROJECTS", Path(tmp)), patch.dict(
                "os.environ", {"TARGETCOMPASS_DEG_RUNNER": "python"}
            ):
                project = _write_project(tmp)
                save_method_config(
                    project,
                    {
                        "query": "gpt_review_ready_query_v0",
                        "audit": "strict_feasibility_audit_v0",
                        "experiment": "review_first_experiment_design_v0",
                    },
                )
                result = TargetDiscoveryAgent("demo").run(
                    "Find secreted targets for human endothelial senescence in vascular aging",
                    "rule_based",
                    ["ds_method"],
                    False,
                    idea_count=4,
                )
                self.assertEqual(result.status, "success")
                trace = json.loads((project / "results" / "agent_trace.json").read_text(encoding="utf-8"))
                self.assertEqual(trace["method_config"]["query"], "gpt_review_ready_query_v0")
                run_status = json.loads((project / "results" / "run_status.json").read_text(encoding="utf-8"))
                self.assertEqual(run_status["status"], "success")
                self.assertEqual(run_status["last_request"]["selected_datasets"], ["ds_method"])
                self.assertEqual(len(run_status["stages"]), len(trace["stages"]))
                self.assertIn("initial_review", [stage["name"] for stage in trace["stages"]])
                review_stage = next(stage for stage in trace["stages"] if stage["name"] == "initial_review" and stage["status"] != "running")
                self.assertEqual(review_stage["details"]["audit_method"], "strict_feasibility_audit_v0")
                audit = json.loads((project / "results" / "ideas" / "feasibility_audit.json").read_text(encoding="utf-8"))
                self.assertEqual(len(audit), 4)

    def test_markdown_method_can_be_registered_and_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            installed = install_markdown_method(
                project,
                "query",
                "professor_query_skill.md",
                "# Professor query skill\nPrioritize secreted, measurable targets before execution.",
            )
            self.assertIn(installed["method_id"], [row["method_id"] for row in available_project_methods(project)["query"]])
            save_method_config(project, {"query": installed["method_id"]})
            result = run_method(
                "query",
                MethodContext(
                    project_dir=project,
                    interest="Find secreted vascular aging targets",
                    parser="rule_based",
                    selected_datasets=["ds_method"],
                    confirmed=False,
                    idea_count=2,
                ),
            )
            self.assertIn("Markdown method guidance attached", result.message)
            self.assertEqual(result.details["markdown_method_id"], installed["method_id"])

    def test_markdown_method_stage_supports_v4_role_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            installed = install_markdown_method(
                project,
                "dataset_scout",
                "dataset_scout_skill.md",
                "# Dataset scout skill\nPrefer datasets with explicit donor-level metadata.",
            )
            self.assertIn(installed["method_id"], [row["method_id"] for row in available_project_methods(project)["dataset_scout"]])
            save_method_config(project, {"dataset_scout": installed["method_id"]})
            self.assertEqual(load_method_config(project)["dataset_scout"], installed["method_id"])


if __name__ == "__main__":
    unittest.main()
