import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from targetcompass_lite.canonical.codex_worker_protocol import (
    REQUIRED_ENGINEERING_FORBIDDEN_PATHS,
    approve_task,
    claim_task,
    complete_task,
    export_task_packet,
    fail_task,
    load_worker_queue,
    release_task,
)


def analysis_packet(task_id="analysis_task_1"):
    return {
        "task_id": task_id,
        "packet_type": "AnalysisTaskPacket",
        "subquestion_id": "sq1",
        "expected_inputs": ["input"],
        "expected_outputs": ["output"],
        "qc_requirements": ["qc"],
        "failure_conditions": ["failure"],
    }


def engineering_packet(task_id="engineering_task_1", allowed_paths=None, forbidden_paths=None):
    return {
        "task_id": task_id,
        "packet_type": "EngineeringTaskPacket",
        "allowed_paths": allowed_paths if allowed_paths is not None else ["targetcompass_lite/canonical/**"],
        "forbidden_paths": forbidden_paths if forbidden_paths is not None else list(REQUIRED_ENGINEERING_FORBIDDEN_PATHS),
        "expected_patch_summary": "Patch canonical code only.",
        "test_commands": ["python -m unittest tests.test_codex_worker_protocol -v"],
    }


class CodexWorkerProtocolTest(unittest.TestCase):
    def test_pending_unapproved_task_cannot_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            export_task_packet(project_dir, analysis_packet())
            with self.assertRaises(ValueError):
                claim_task(project_dir, "worker_a", "analysis_task_1")

    def test_approve_then_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            export_task_packet(project_dir, analysis_packet())
            approve_task(project_dir, "analysis_task_1", "reviewer")
            claimed = claim_task(project_dir, "worker_a", "analysis_task_1")
            self.assertEqual(claimed["status"], "claimed")
            self.assertEqual(claimed["worker_id"], "worker_a")
            self.assertTrue(claimed["claimed_at"])
            self.assertTrue(claimed["lease_expires_at"])
            queue = load_worker_queue(project_dir)
            self.assertEqual(len(queue["approved"]), 0)
            self.assertEqual(len(queue["claimed"]), 1)

    def test_worker_mismatch_cannot_complete_or_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            export_task_packet(project_dir, analysis_packet())
            approve_task(project_dir, "analysis_task_1", "reviewer")
            claim_task(project_dir, "worker_a", "analysis_task_1")
            with self.assertRaises(ValueError):
                complete_task(project_dir, "analysis_task_1", "worker_b", {"artifacts": []})
            with self.assertRaises(ValueError):
                fail_task(project_dir, "analysis_task_1", "worker_b", "failed")

    def test_expired_lease_can_be_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            export_task_packet(project_dir, analysis_packet())
            approve_task(project_dir, "analysis_task_1", "reviewer")
            claim_task(project_dir, "worker_a", "analysis_task_1")
            claimed_path = project_dir / "v5" / "codex" / "claimed" / "analysis_task_1.json"
            record = json.loads(claimed_path.read_text(encoding="utf-8"))
            record["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            claimed_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            reclaimed = claim_task(project_dir, "worker_b", "analysis_task_1")
            self.assertEqual(reclaimed["worker_id"], "worker_b")
            self.assertEqual(reclaimed["status"], "claimed")

    def test_engineering_task_missing_allowed_paths_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            with self.assertRaises(ValueError):
                export_task_packet(project_dir, engineering_packet(allowed_paths=[]))

    def test_engineering_forbidden_paths_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            with self.assertRaises(ValueError):
                export_task_packet(project_dir, engineering_packet(forbidden_paths=[".git/"]))
            record = export_task_packet(project_dir, engineering_packet())
            self.assertEqual(record["status"], "pending_approval")

    def test_complete_requires_output_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            export_task_packet(project_dir, analysis_packet())
            approve_task(project_dir, "analysis_task_1", "reviewer")
            claim_task(project_dir, "worker_a", "analysis_task_1")
            with self.assertRaises(ValueError):
                complete_task(project_dir, "analysis_task_1", "worker_a", {})
            completed = complete_task(project_dir, "analysis_task_1", "worker_a", {"artifacts": [{"artifact_id": "a1"}]})
            self.assertEqual(completed["status"], "completed")
            queue = load_worker_queue(project_dir)
            self.assertEqual(len(queue["completed"]), 1)
            self.assertEqual(len(queue["claimed"]), 0)

    def test_release_returns_task_to_approved_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            export_task_packet(project_dir, analysis_packet())
            approve_task(project_dir, "analysis_task_1", "reviewer")
            claim_task(project_dir, "worker_a", "analysis_task_1")
            released = release_task(project_dir, "analysis_task_1", "worker_a", "Need another worker.")
            self.assertEqual(released["status"], "approved")
            queue = load_worker_queue(project_dir)
            self.assertEqual(len(queue["approved"]), 1)
            self.assertEqual(len(queue["claimed"]), 0)


if __name__ == "__main__":
    unittest.main()
