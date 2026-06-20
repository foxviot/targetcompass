import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.review import (
    build_review_queue,
    final_signoff,
    load_approval_state,
    load_review_events,
    load_reviews,
    record_review,
)
from targetcompass_lite.run_state import check_cancelled, clear_cancel, read_status, request_cancel, write_status
from targetcompass_lite.webapp import _run_status
from targetcompass_lite.v4 import (
    compile_v4_work_orders,
    finish_work_order_attempt,
    load_codex_task_packet,
    load_v4_work_orders,
    read_work_order_attempts,
    start_work_order_attempt,
)


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    ideas = project / "results" / "ideas"
    ideas.mkdir(parents=True)
    (ideas / "idea_batch.json").write_text(
        json.dumps(
            [
                {
                    "idea_id": "idea_1",
                    "title": "CXCL8 as secreted aging target",
                    "execution_status": "candidate",
                    "feasibility_score": 90,
                }
            ]
        ),
        encoding="utf-8",
    )
    return project


class ReviewAndRunStateTest(unittest.TestCase):
    def test_review_records_reason_versions_diff_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            row = record_review(
                project,
                "idea",
                "idea_1",
                "approve",
                note="looks feasible",
                reason="strong secreted signal",
                report_ref="reports/target_report.html#idea-idea-1",
            )
            self.assertIn("review_id", row)
            self.assertNotEqual(row["before_hash"], row["after_hash"])
            self.assertIn("review_status", row["diff"])
            reviews = load_reviews(project)
            self.assertEqual(reviews[0]["reason"], "strong secreted signal")
            self.assertTrue((project / reviews[0]["version_file"]).exists())
            events = load_review_events(project)
            self.assertEqual(events[0]["after"]["review_status"], "approve")

    def test_review_reason_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            with self.assertRaises(ValueError):
                record_review(project, "idea", "idea_1", "approve")

    def test_review_queue_and_final_signoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            queue = build_review_queue(project)
            self.assertEqual(queue["queue_count"], 1)
            with self.assertRaises(ValueError):
                final_signoff(project, signer="pi", reason="ready")
            record_review(project, "idea", "idea_1", "approve", reason="strong evidence")
            queue = build_review_queue(project)
            self.assertEqual(queue["queue_count"], 0)
            state = final_signoff(project, signer="pi", reason="all candidates reviewed")
            self.assertEqual(state["status"], "signed_off")
            self.assertEqual(load_approval_state(project)["signer"], "pi")
            self.assertIn("traceability_snapshot", state)

    def test_run_status_tracks_failure_reason_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            write_status(
                project,
                "failed",
                "Workflow failed.",
                stderr="boom",
                stages=[{"name": "execution", "status": "failed", "message": "boom"}],
                run_id="run_test",
                last_request={"interest": "vascular aging", "selected_datasets": ["ds"]},
                failure_reason="boom",
            )
            status = read_status(project)
            self.assertEqual(status["run_id"], "run_test")
            self.assertEqual(status["active_stage"], "execution")
            self.assertEqual(status["failure_reason"], "boom")
            html = _run_status(project)
            self.assertIn("Rerun last request", html)
            request_cancel(project)
            with self.assertRaises(RuntimeError):
                check_cancelled(project)
            clear_cancel(project)
            check_cancelled(project)

    def test_work_order_attempt_status_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            compile_v4_work_orders(project, {"project_id": "demo", "modules": [{"module_id": "P4_bulk_deg_ds", "module": "bulk_deg", "dataset_id": "ds"}]})
            attempt = start_work_order_attempt(project, "P4_bulk_deg_ds", "run_test")
            finish_work_order_attempt(project, attempt["attempt_id"], "success", ["results/bulk_deg_ds/deg_results.tsv"])
            manifest = read_work_order_attempts(project)
            self.assertEqual(manifest["attempts"][0]["status"], "success")
            write_status(project, "success", "done", run_id="run_test")
            self.assertEqual(read_status(project)["work_order_attempts"], "v4/work_order_attempts.json")

    def test_review_can_approve_v4_work_order_and_codex_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            plan = {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "P9_new_adapter_x",
                        "module": "new_external_adapter",
                        "dataset_id": "external_x",
                        "inputs": {},
                        "parameters": {},
                        "expected_outputs": ["results/external_x/normalized.tsv"],
                        "qc_checks": ["schema validated"],
                        "allowed_files": ["targetcompass_lite/db_adapters/**"],
                    }
                ],
            }
            order = compile_v4_work_orders(project, plan)[0]
            packet = load_codex_task_packet(project, order)
            record_review(project, "work_order", order["work_order_id"], "approve", reason="adapter build is scoped")
            updated = load_v4_work_orders(project)[0]
            self.assertEqual(updated["status"], "approved")
            self.assertEqual(updated["review_status"], "approve")
            record_review(project, "codex_task", packet["codex_job_id"], "approve", reason="task packet has fixtures and tests")
            updated_packet = load_codex_task_packet(project, load_v4_work_orders(project)[0])
            self.assertEqual(updated_packet["review_status"], "approve")
            self.assertEqual(updated_packet["release_gate"], "approved_for_codex_worker")
            queue = build_review_queue(project)
            self.assertEqual(queue["queue_count"], 0)


if __name__ == "__main__":
    unittest.main()
