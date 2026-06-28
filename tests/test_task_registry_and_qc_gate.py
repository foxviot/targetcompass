import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_db import import_evidence
from targetcompass_lite.orchestrator import submit_orchestrator_run
from targetcompass_lite.qc_review import apply_qc_review, apply_qc_review_batch, build_qc_review_queue
from targetcompass_lite.review import final_signoff
from targetcompass_lite.scoring import score_project
from targetcompass_lite.task_registry import build_task_registry
from targetcompass_lite.v4 import compile_v4_work_orders, save_v4_work_order


class TaskRegistryAndQCGateTest(unittest.TestCase):
    def test_task_registry_tracks_codex_packets_attempts_and_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_bulk_project(project)
            plan = {
                "project_id": "demo",
                "codex_task_packets": [
                    {
                        "task_id": "ctp_bulk",
                        "name": "P4_bulk",
                        "method_contract_id": "bulk_deg_limma_or_countlike_v1",
                        "inputs": {"dataset_card": "dataset_cards/ds.yaml"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                        "forbidden_actions": ["do not invent metadata"],
                    }
                ],
                "modules": [
                    {
                        "module_id": "P4_bulk",
                        "module": "bulk_deg",
                        "dataset_id": "ds",
                        "inputs": {},
                        "parameters": {"method_contract_id": "bulk_deg_limma_or_countlike_v1"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv", "results/bulk_deg_ds/qc_summary.json"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            compile_v4_work_orders(project, plan)
            run = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_registry", force=True)
            registry = build_task_registry(project)
            task = next(row for row in registry["tasks"] if row["module_id"] == "P4_bulk")
            self.assertEqual(registry["task_count"], 1)
            self.assertEqual(task["task_id"], "ctp_bulk")
            self.assertIn(task["status"], {"qc_passed", "qc_review_required"})
            self.assertTrue(task["refs"]["attempt_id"])
            self.assertTrue(task["refs"]["qc_report"])
            self.assertIn("qc_gate", task)
            self.assertIn(task["qc_gate"]["evidence_import"], {"ALLOW", "QC_REVIEW_REQUIRED", "REJECT"})
            self.assertEqual(run["result"]["task_registry"]["task_count"], 1)

    def test_qc_review_marks_imported_evidence_for_review_and_scoring_uses_evidence_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_bulk_project(project)
            _write_annotation(project, "IL6")
            _write_qc_review(project)
            db = import_evidence(project)
            con = sqlite3.connect(db)
            try:
                statuses = {row[0] for row in con.execute("SELECT DISTINCT review_status FROM evidence_item").fetchall()}
            finally:
                con.close()
            self.assertIn("QC_REVIEW_REQUIRED", statuses)
            score_path = score_project(project)
            with score_path.open(encoding="utf-8") as f:
                row = next(csv.DictReader(f))
            score_json = json.loads(row["score_json"])
            self.assertIn("evidence_plan", score_json)
            self.assertIn("evidence_axis_coverage", score_json)
            self.assertIn("missing_axes", score_json["evidence_axis_coverage"])
            self.assertIn("axis_aggregation", score_json["evidence_axis_coverage"])
            self.assertIn("weighted_coverage_fraction", score_json["evidence_axis_coverage"])

    def test_qc_review_queue_updates_evidence_review_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_bulk_project(project)
            _write_annotation(project, "IL6")
            _write_qc_review(project)
            db = import_evidence(project)
            plan = {
                "project_id": "demo",
                "codex_task_packets": [{"task_id": "ctp_bulk", "name": "P4_bulk", "method_contract_id": "bulk_deg_limma_or_countlike_v1"}],
                "modules": [
                    {
                        "module_id": "P4_bulk",
                        "module": "bulk_deg",
                        "dataset_id": "ds",
                        "inputs": {},
                        "parameters": {"method_contract_id": "bulk_deg_limma_or_countlike_v1"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            compile_v4_work_orders(project, plan)
            # Align the generated WorkOrder id with the prebuilt QC index used by this test.
            work_orders = json.loads((project / "v4" / "work_orders.json").read_text(encoding="utf-8"))["work_orders"]
            work_orders[0]["work_order_id"] = "wo_bulk"
            save_v4_work_order(project, work_orders[0])
            queue = build_qc_review_queue(project)
            self.assertEqual(queue["queue_count"], 1)
            result = apply_qc_review(project, "wo_bulk", "approve", "statistical warning checked")
            self.assertEqual(result["evidence_update"]["target_status"], "ACCEPT_WITH_FLAGS")
            self.assertTrue((project / "results" / "review_actions.tsv").exists())
            self.assertTrue((project / "results" / "review_versions").exists())
            con = sqlite3.connect(db)
            try:
                statuses = {row[0] for row in con.execute("SELECT DISTINCT review_status FROM evidence_item").fetchall()}
            finally:
                con.close()
            self.assertIn("ACCEPT_WITH_FLAGS", statuses)

    def test_qc_review_batch_refreshes_downstream_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_bulk_project(project)
            _write_annotation(project, "IL6")
            _write_qc_review(project)
            import_evidence(project)
            plan = {
                "project_id": "demo",
                "codex_task_packets": [{"task_id": "ctp_bulk", "name": "P4_bulk", "method_contract_id": "bulk_deg_limma_or_countlike_v1"}],
                "modules": [
                    {
                        "module_id": "P4_bulk",
                        "module": "bulk_deg",
                        "dataset_id": "ds",
                        "inputs": {},
                        "parameters": {"method_contract_id": "bulk_deg_limma_or_countlike_v1"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            compile_v4_work_orders(project, plan)
            work_orders = json.loads((project / "v4" / "work_orders.json").read_text(encoding="utf-8"))["work_orders"]
            work_orders[0]["work_order_id"] = "wo_bulk"
            save_v4_work_order(project, work_orders[0])
            result = apply_qc_review_batch(project, ["wo_bulk"], "approve", "batch checked")
            self.assertEqual(result["reviewed_count"], 1)
            self.assertEqual(result["error_count"], 0)
            self.assertTrue((project / "v4" / "qc_review_downstream_refresh.json").exists())
            self.assertTrue((project / "candidate_scores.csv").exists())
            self.assertTrue((project / "reports" / "target_report.html").exists())
            refreshed = json.loads((project / "v4" / "qc_review_downstream_refresh.json").read_text(encoding="utf-8"))
            self.assertIn("traceability", refreshed["refreshed"])

    def test_final_signoff_rebuilds_stale_qc_review_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_bulk_project(project)
            _write_annotation(project, "IL6")
            _write_qc_review(project)
            plan = {
                "project_id": "demo",
                "codex_task_packets": [{"task_id": "ctp_bulk", "name": "P4_bulk", "method_contract_id": "bulk_deg_limma_or_countlike_v1"}],
                "modules": [
                    {
                        "module_id": "P4_bulk",
                        "module": "bulk_deg",
                        "dataset_id": "ds",
                        "inputs": {},
                        "parameters": {"method_contract_id": "bulk_deg_limma_or_countlike_v1"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            compile_v4_work_orders(project, plan)
            work_orders = json.loads((project / "v4" / "work_orders.json").read_text(encoding="utf-8"))["work_orders"]
            work_orders[0]["work_order_id"] = "wo_bulk"
            save_v4_work_order(project, work_orders[0])
            (project / "v4" / "qc_review_queue.json").write_text(
                json.dumps({"schema_version": "v4.qc_review_queue/0.1", "queue_count": 0, "items": []}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                final_signoff(project, signer="pi", reason="qc queue must refresh")
            refreshed = json.loads((project / "v4" / "qc_review_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(refreshed["queue_count"], 1)


def _write_bulk_project(project: Path) -> None:
    (project / "dataset_cards").mkdir(parents=True, exist_ok=True)
    (project / "data" / "ds").mkdir(parents=True, exist_ok=True)
    (project / "results" / "evidence_planning").mkdir(parents=True, exist_ok=True)
    (project / "research_spec.json").write_text(
        json.dumps({"disease_scope": {"canonical": "sarcopenia"}, "project_id": "demo"}),
        encoding="utf-8",
    )
    (project / "dataset_cards" / "ds.yaml").write_text(
        "\n".join(
            [
                "dataset_id: ds",
                "source: local",
                "accession: DS",
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
                "  expression_matrix: data/ds/expression_matrix.tsv",
                "  metadata: data/ds/metadata.tsv",
            ]
        ),
        encoding="utf-8",
    )
    (project / "data" / "ds" / "expression_matrix.tsv").write_text(
        "gene_symbol\tS1\tS2\tS3\tS4\nIL6\t10\t11\t2\t2\nCXCL8\t9\t10\t1\t1\n",
        encoding="utf-8",
    )
    (project / "data" / "ds" / "metadata.tsv").write_text(
        "sample_id\tgroup\nS1\tcase\nS2\tcase\nS3\tcontrol\nS4\tcontrol\n",
        encoding="utf-8",
    )
    (project / "results" / "evidence_planning" / "evidence_plan.json").write_text(
        json.dumps(
            {
                "evidence_axes": {
                    "disease_relevant_expression": True,
                    "condition_upregulation": True,
                    "SASP_annotation": True,
                    "secreted_or_surface_annotation": True,
                    "cell_type_specificity": True,
                }
            }
        ),
        encoding="utf-8",
    )


def _write_annotation(project: Path, gene: str) -> None:
    out = project / "results" / "annotation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "accessibility_annotation.tsv").write_text(f"gene_symbol\taccessibility_status\troute\n{gene}\tSUPPORTED\tsecreted\n", encoding="utf-8")
    (out / "safety_flags.tsv").write_text(f"gene_symbol\tsafety_gate\n{gene}\tPASS\n", encoding="utf-8")
    deg = project / "results" / "bulk_deg_ds"
    deg.mkdir(parents=True, exist_ok=True)
    (deg / "deg_results.tsv").write_text(
        "gene_symbol\tcase_mean\tcontrol_mean\tlogFC\tp_value\tadj_p_value\tdirection\nIL6\t10\t2\t2.3\t0.001\t0.01\tup\n",
        encoding="utf-8",
    )


def _write_qc_review(project: Path) -> None:
    out = project / "results" / "qc"
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "v0.1.task_qc_report",
        "project_id": "demo",
        "work_order_id": "wo_bulk",
        "module_id": "P4_bulk",
        "dataset_id": "ds",
        "overall_status": "review",
        "layers": [
            {"layer": "Execution", "status": "pass"},
            {"layer": "Data", "status": "pass"},
            {"layer": "Statistical", "status": "review"},
            {"layer": "Biological", "status": "pass"},
        ],
        "blocking_reasons": [],
        "warnings": ["statistical review"],
        "artifacts": ["results/bulk_deg_ds/deg_results.tsv"],
        "generated_at": "now",
    }
    (out / "qc_bulk.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "task_qc_reports.json").write_text(
        json.dumps(
            {
                "schema_version": "v0.1.task_qc_report_index",
                "project_id": "demo",
                "reports": [
                    {
                        "qc_report_id": "qc_bulk",
                        "work_order_id": "wo_bulk",
                        "module_id": "P4_bulk",
                        "dataset_id": "ds",
                        "overall_status": "review",
                        "path": "results/qc/qc_bulk.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
