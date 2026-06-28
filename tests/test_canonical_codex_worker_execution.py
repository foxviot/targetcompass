import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import load_artifact_registry
from targetcompass_lite.canonical.codex_worker_execution import build_subprocess_codex_executor, execute_claimed_codex_task
from targetcompass_lite.canonical.codex_worker_protocol import (
    REQUIRED_ENGINEERING_FORBIDDEN_PATHS,
    approve_task,
    claim_task,
    export_task_packet,
    load_worker_queue,
)


def engineering_packet(task_id="engineering_task_1", allowed_paths=None):
    return {
        "task_id": task_id,
        "packet_type": "EngineeringTaskPacket",
        "allowed_paths": allowed_paths if allowed_paths is not None else ["targetcompass_lite/canonical/**"],
        "forbidden_paths": list(REQUIRED_ENGINEERING_FORBIDDEN_PATHS),
        "expected_patch_summary": "Patch canonical code only.",
        "test_commands": ["python -m unittest tests.test_canonical_codex_worker_execution -v"],
    }


class CanonicalCodexWorkerExecutionTest(unittest.TestCase):
    def test_unclaimed_task_cannot_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            export_task_packet(project, engineering_packet())
            approve_task(project, "engineering_task_1", "reviewer")
            with self.assertRaises(ValueError):
                execute_claimed_codex_task(project, "engineering_task_1", "worker_a", executor=lambda *_: {"artifacts": []})

    def test_worker_mismatch_cannot_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            export_task_packet(project, engineering_packet())
            approve_task(project, "engineering_task_1", "reviewer")
            claim_task(project, "worker_a", "engineering_task_1")
            with self.assertRaises(ValueError):
                execute_claimed_codex_task(project, "engineering_task_1", "worker_b", executor=lambda *_: {"artifacts": []})

    def test_claimed_engineering_task_executes_and_registers_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "v5" / "codex_outputs" / "patch_result.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("patch ok", encoding="utf-8")
            export_task_packet(project, engineering_packet())
            approve_task(project, "engineering_task_1", "reviewer")
            claim_task(project, "worker_a", "engineering_task_1")

            def executor(project_dir, record):
                self.assertEqual(record["task_id"], "engineering_task_1")
                return {"artifacts": [{"path": "v5/codex_outputs/patch_result.txt", "artifact_type": "codex_patch"}], "result_ref": "result_1"}

            result = execute_claimed_codex_task(project, "engineering_task_1", "worker_a", executor=executor)
            self.assertEqual(result["status"], "completed")
            queue = load_worker_queue(project)
            self.assertEqual(len(queue["completed"]), 1)
            self.assertEqual(len(queue["claimed"]), 0)
            registry = load_artifact_registry(project)
            self.assertEqual(len(registry), 1)
            self.assertEqual(registry[0]["artifact_type"], "codex_patch")
            self.assertTrue(registry[0]["checksum_sha256"])

    def test_executor_failure_moves_task_to_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            export_task_packet(project, engineering_packet())
            approve_task(project, "engineering_task_1", "reviewer")
            claim_task(project, "worker_a", "engineering_task_1")

            def failing_executor(project_dir, record):
                raise RuntimeError("test failed")

            result = execute_claimed_codex_task(project, "engineering_task_1", "worker_a", executor=failing_executor)
            self.assertEqual(result["status"], "failed")
            queue = load_worker_queue(project)
            self.assertEqual(len(queue["failed"]), 1)
            self.assertIn("test failed", queue["failed"][0]["failure_reason"])

    def test_forbidden_allowed_path_is_rejected_before_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            export_task_packet(project, engineering_packet(allowed_paths=[".env"]))
            approve_task(project, "engineering_task_1", "reviewer")
            claim_task(project, "worker_a", "engineering_task_1")
            called = {"value": False}

            def executor(project_dir, record):
                called["value"] = True
                return {"artifacts": []}

            result = execute_claimed_codex_task(project, "engineering_task_1", "worker_a", executor=executor)
            self.assertEqual(result["status"], "failed")
            self.assertFalse(called["value"])

    def test_output_manifest_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            export_task_packet(project, engineering_packet())
            approve_task(project, "engineering_task_1", "reviewer")
            claim_task(project, "worker_a", "engineering_task_1")
            result = execute_claimed_codex_task(project, "engineering_task_1", "worker_a", executor=lambda *_: {})
            self.assertEqual(result["status"], "failed")

    def test_subprocess_executor_writes_request_stdout_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            export_task_packet(project, engineering_packet(test_id := "engineering_task_subprocess"))
            approve_task(project, test_id, "reviewer")
            claim_task(project, "worker_a", test_id)

            command = [
                "python",
                "-c",
                "import json, os, pathlib; p=pathlib.Path(os.environ['TARGETCOMPASS_CODEX_WORKER_RESPONSE']); p.write_text(json.dumps({'artifacts': [], 'result_ref': 'subprocess_ok', 'patch_refs': ['patch.diff'], 'test_refs': ['tests.ok']}), encoding='utf-8')",
            ]
            result = execute_claimed_codex_task(project, test_id, "worker_a", executor=build_subprocess_codex_executor(command, timeout_seconds=20))

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["output_manifest"]["executor"], "subprocess_codex_worker")
            self.assertTrue((project / "v5" / "codex_outputs" / test_id / "worker_request.json").exists())
            self.assertTrue(any(row["artifact_type"] == "codex_worker_stdout" for row in result["output_manifest"]["artifacts"]))


if __name__ == "__main__":
    unittest.main()
