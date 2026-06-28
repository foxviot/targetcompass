import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.orchestrator import (
    cancel_orchestrator_run,
    get_orchestrator_status,
    partial_rerun_orchestrator,
    resume_orchestrator_run,
    submit_orchestrator_run,
)


class OrchestratorTest(unittest.TestCase):
    def test_submit_is_idempotent_and_status_aggregates_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)

            first = submit_orchestrator_run(project, idempotency_key="idem_demo", role_id="planner", actor="unit_test")
            second = submit_orchestrator_run(project, idempotency_key="idem_demo", role_id="planner", actor="unit_test")
            status = get_orchestrator_status(project, first["orchestrator_run_id"])

            self.assertEqual(first["orchestrator_run_id"], second["orchestrator_run_id"])
            self.assertTrue(second["idempotent_replay"])
            self.assertEqual(first["status"], "success")
            self.assertEqual(status["schema_version"], "v4.orchestrator_status/0.1")
            self.assertEqual(status["typed_orchestration_status"], "success")
            self.assertIn("run_status", status["state_refs"])
            self.assertTrue((project / "v4" / "orchestrator_runs.json").exists())

    def test_cancel_marks_running_run_and_writes_cancel_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)
            run = submit_orchestrator_run(project, idempotency_key="idem_cancel", run_type="status_only")

            status = cancel_orchestrator_run(project, run["orchestrator_run_id"], reason="operator_stop")

            self.assertTrue((project / "results" / "cancel_requested.json").exists())
            self.assertEqual(status["selected_run"]["cancel_reason"], "operator_stop")

    def test_resume_and_partial_rerun_create_new_orchestrator_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)
            original = submit_orchestrator_run(project, idempotency_key="idem_resume", role_id="planner", actor="unit_test")

            resumed = resume_orchestrator_run(project, original["orchestrator_run_id"], actor="unit_test")
            partial = partial_rerun_orchestrator(project, "manifest", actor="unit_test")
            index = json.loads((project / "v4" / "orchestrator_runs.json").read_text(encoding="utf-8"))

            self.assertNotEqual(original["orchestrator_run_id"], resumed["orchestrator_run_id"])
            self.assertEqual(resumed["resume_of"], original["orchestrator_run_id"])
            self.assertEqual(partial["request"]["run_type"], "partial_rerun")
            self.assertIn("v4/object_manifest.json", partial["result"]["artifacts"])
            self.assertIn("v4/object_manifest.json", partial["artifacts"])
            self.assertGreaterEqual(len(index["runs"]), 3)

    def test_work_order_dag_run_executes_nodes_skips_success_and_records_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_work_order_dag_project(project)

            first = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_dag", actor="unit_test")
            second = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_dag_skip", actor="unit_test")
            failed = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_adapter", work_order_id="wo_adapter", actor="unit_test")
            forced = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_force", module_id="P4_bulk", force=True, actor="unit_test")
            attempts = json.loads((project / "v4" / "work_order_attempts.json").read_text(encoding="utf-8"))["attempts"]
            dag = json.loads((project / "v4" / "work_order_dag.json").read_text(encoding="utf-8"))

            self.assertEqual(first["result"]["schema_version"], "v4.work_order_dag_run/0.1")
            self.assertEqual(first["result"]["status"], "failed")
            self.assertIn("v4/work_order_dag.json", first["artifacts"])
            self.assertTrue(any(item.endswith("executor_manifest.json") for item in first["artifacts"]))
            self.assertTrue(any(row["status"] == "success" and row["module_id"] == "P4_bulk" for row in first["result"]["node_results"]))
            self.assertTrue(any(row["status"] == "failed" and row["work_order_id"] == "wo_adapter" for row in first["result"]["node_results"]))
            bulk_result = next(row for row in first["result"]["node_results"] if row["module_id"] == "P4_bulk")
            qc = bulk_result["task_qc_report"]
            self.assertIn(qc["overall_status"], {"pass", "review"})
            self.assertEqual({layer["layer"] for layer in qc["layers"]}, {"Execution", "Data", "Statistical", "Biological"})
            self.assertTrue((project / "results" / "qc" / "task_qc_reports.json").exists())
            self.assertTrue(any(row["status"] == "skipped" for row in second["result"]["node_results"]))
            adapter = next(row for row in failed["result"]["node_results"] if row["work_order_id"] == "wo_adapter")
            self.assertIn("Codex engineering", adapter["recovery"]["suggested_action"])
            self.assertTrue(any(row["module_id"] == "P4_bulk" and row["status"] == "success" for row in attempts))
            self.assertTrue(any(row["module_id"] == "P4_bulk" and row["attempt_id"] == forced["result"]["node_results"][0]["attempt_id"] for row in attempts))
            self.assertIn("success", dag["status_summary"])
            dag_bulk = next(row for row in dag["nodes"] if row["module_id"] == "P4_bulk")
            self.assertIn("task_qc_report", dag_bulk)


def _write_full_orchestration_inputs(project: Path) -> None:
    (project / "v4").mkdir(parents=True, exist_ok=True)
    (project / "results" / "geo_discovery").mkdir(parents=True, exist_ok=True)
    (project / "results" / "causal_evidence").mkdir(parents=True, exist_ok=True)
    (project / "reports").mkdir(parents=True, exist_ok=True)
    (project / "research_spec.json").write_text(json.dumps({"project_id": "demo", "research_theme": "vascular aging"}), encoding="utf-8")
    (project / "v4" / "disease_spec.json").write_text(json.dumps({"project_id": "demo", "canonical": "vascular aging"}), encoding="utf-8")
    (project / "dataset_match_report.csv").write_text("dataset_id,score\nGSE1,0.9\n", encoding="utf-8")
    (project / "eligible_datasets.csv").write_text("dataset_id\nGSE1\n", encoding="utf-8")
    (project / "results" / "geo_discovery" / "geo_recommendations.json").write_text(json.dumps({"recommendations": [{"dataset_id": "GSE1"}]}), encoding="utf-8")
    (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": [{"module_id": "bulk_deg"}]}), encoding="utf-8")
    (project / "v4" / "work_orders.json").write_text(json.dumps({"work_orders": [{"work_order_id": "wo1"}]}), encoding="utf-8")
    (project / "results" / "causal_evidence" / "causal_evidence_grades.tsv").write_text("gene\tgrade\nCXCL8\tC2\n", encoding="utf-8")
    (project / "reports" / "target_report.html").write_text("<html>demo</html>", encoding="utf-8")
    (project / "reports" / "target_report_structured.json").write_text(json.dumps({"report_evidence_refs": {"CXCL8": {"evidence_refs": ["ev1"]}}}), encoding="utf-8")


def _write_work_order_dag_project(project: Path) -> None:
    (project / "v4").mkdir(parents=True, exist_ok=True)
    (project / "dataset_cards").mkdir(parents=True, exist_ok=True)
    (project / "data" / "ds1").mkdir(parents=True, exist_ok=True)
    (project / "dataset_cards" / "ds1.yaml").write_text(
        "\n".join(
            [
                "dataset_id: ds1",
                "source: local",
                "accession: DS1",
                "modality: bulk_expression",
                "organism: human",
                "tissue: muscle",
                "contrast:",
                "  case: case",
                "  control: control",
                "sample_summary:",
                "  case_n: 2",
                "  control_n: 2",
                "metadata_fields: [sample_id, group]",
                "matrix_available: true",
                "license_status: public",
                "file_paths:",
                "  expression_matrix: data/ds1/expression_matrix.tsv",
                "  metadata: data/ds1/metadata.tsv",
                "recommended_use: [bulk_deg]",
                "blocked_use: []",
            ]
        ),
        encoding="utf-8",
    )
    (project / "data" / "ds1" / "expression_matrix.tsv").write_text(
        "gene_symbol\tS1\tS2\tS3\tS4\nCXCL8\t10\t11\t2\t2\nIL6\t5\t5\t5\t5\n",
        encoding="utf-8",
    )
    (project / "data" / "ds1" / "metadata.tsv").write_text(
        "sample_id\tgroup\nS1\tcase\nS2\tcase\nS3\tcontrol\nS4\tcontrol\n",
        encoding="utf-8",
    )
    plan = {
        "project_id": "demo",
        "modules": [
            {
                "module_id": "P4_bulk",
                "module": "bulk_deg",
                "dataset_id": "ds1",
                "inputs": {},
                "parameters": {},
                "expected_outputs": ["results/bulk_deg_ds1/deg_results.tsv"],
            },
            {
                "module_id": "P5_report",
                "module": "report",
                "dataset_id": "",
                "inputs": {"deg": "results/bulk_deg_ds1/deg_results.tsv"},
                "parameters": {},
                "expected_outputs": ["reports/target_report.html"],
            },
            {
                "module_id": "P9_adapter",
                "module": "new_adapter",
                "dataset_id": "external",
                "inputs": {},
                "parameters": {},
                "expected_outputs": ["results/external/out.tsv"],
            },
        ],
    }
    from targetcompass_lite.v4 import compile_v4_work_orders

    compile_v4_work_orders(project, plan)
    orders = json.loads((project / "v4" / "work_orders.json").read_text(encoding="utf-8"))["work_orders"]
    for order in orders:
        if order["module_id"] == "P9_adapter":
            order["work_order_id"] = "wo_adapter"
    (project / "v4" / "work_orders.json").write_text(json.dumps({"schema_version": "v4.work_order_index/0.1", "work_orders": orders}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
