import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from targetcompass_lite.codex_task_queue import claim_codex_task, execute_codex_queue_task, release_stale_codex_tasks, sync_codex_task_queue
from targetcompass_lite.v4 import compile_v4_work_orders


class CodexTaskQueueTest(unittest.TestCase):
    def test_codex_task_packets_become_claimable_executable_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            plan = {
                "project_id": "demo",
                "codex_task_packets": [
                    {
                        "schema_version": "v0.2.codex_task_packet",
                        "task_id": "ctp_bulk",
                        "name": "P4_bulk",
                        "goal": "Run bulk DEG",
                        "dataset": {"dataset_id": "ds"},
                        "inputs": {"dataset_card": "dataset_cards/ds.yaml"},
                        "method": {"method_contract_id": "bulk_deg_limma_or_countlike_v1"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                        "acceptance_criteria": ["QC exists"],
                        "failure_condition": "missing input",
                        "forbidden_actions": ["do not invent metadata"],
                        "method_contract_id": "bulk_deg_limma_or_countlike_v1",
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

            queue = sync_codex_task_queue(project)
            self.assertEqual(queue["task_count"], 1)
            self.assertEqual(queue["tasks"][0]["status"], "pending")
            claim = claim_codex_task(project, worker_id="unit_worker")
            self.assertTrue(claim["claimed"])
            result = execute_codex_queue_task(project, task_id="ctp_bulk", worker_id="unit_worker", force=True)
            self.assertEqual(result["task"]["status"], "succeeded")
            self.assertTrue((project / "v4" / "codex_task_queue_results.json").exists())
            self.assertTrue((project / "v4" / "codex_task_queue_tests.json").exists())
            self.assertTrue((project / "v4" / "codex_task_queue_patches.json").exists())
            patches = json.loads((project / "v4" / "codex_task_queue_patches.json").read_text(encoding="utf-8"))["patches"]
            self.assertEqual(patches[0]["status"], "not_applicable")

    def test_engineering_queue_uses_packet_codex_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            plan = {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "BUILD_demo_adapter",
                        "module": "new_database_adapter",
                        "dataset_id": "ds",
                        "inputs": {"fixture": "dataset_cards/ds.yaml"},
                        "parameters": {"method_contract_id": "adapter_build_v1"},
                        "allowed_files": ["targetcompass_lite/db_adapters.py", "tests/test_db_adapters.py"],
                        "expected_outputs": ["adapter test passes"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            orders = compile_v4_work_orders(project, plan)
            packet = json.loads((project / orders[0]["codex_task_packet"]).read_text(encoding="utf-8"))
            queue = sync_codex_task_queue(project)
            self.assertEqual(queue["tasks"][0]["task_kind"], "engineering")
            self.assertEqual(queue["tasks"][0]["codex_job_id"], packet["codex_job_id"])

    def test_stale_claimed_and_running_tasks_are_released_conservatively(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            plan = {
                "project_id": "demo",
                "codex_task_packets": [
                    {"schema_version": "v0.2.codex_task_packet", "task_id": "old_claim", "name": "P4_old_claim", "goal": "Run old claimed task"},
                    {"schema_version": "v0.2.codex_task_packet", "task_id": "old_running", "name": "P4_old_running", "goal": "Run old running task"},
                    {"schema_version": "v0.2.codex_task_packet", "task_id": "fresh_running", "name": "P4_fresh_running", "goal": "Run fresh task"},
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            queue = sync_codex_task_queue(project)
            old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
            fresh = datetime.now(timezone.utc).isoformat()
            for task in queue["tasks"]:
                if task["task_id"] == "old_claim":
                    task["status"] = "claimed"
                    task["claim"] = {"worker_id": "crashed", "claimed_at": old}
                elif task["task_id"] == "old_running":
                    task["status"] = "running"
                    task["started_at"] = old
                elif task["task_id"] == "fresh_running":
                    task["status"] = "running"
                    task["started_at"] = fresh
            (project / "v4" / "codex_task_queue.json").write_text(json.dumps(queue), encoding="utf-8")

            recovery = release_stale_codex_tasks(project, stale_after_seconds=60 * 60)
            self.assertEqual(recovery["released_count"], 2)
            refreshed = json.loads((project / "v4" / "codex_task_queue.json").read_text(encoding="utf-8"))
            by_id = {task["task_id"]: task for task in refreshed["tasks"]}
            self.assertEqual(by_id["old_claim"]["status"], "released")
            self.assertEqual(by_id["old_running"]["status"], "released")
            self.assertEqual(by_id["fresh_running"]["status"], "running")
            self.assertIn("resume_action", by_id["old_claim"]["recovery"])


def _write_project(project: Path) -> None:
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds").mkdir(parents=True)
    (project / "research_spec.json").write_text(json.dumps({"disease_scope": {"canonical": "demo"}}), encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
