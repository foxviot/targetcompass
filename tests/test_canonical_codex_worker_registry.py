import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.codex_worker_protocol import REQUIRED_ENGINEERING_FORBIDDEN_PATHS
from targetcompass_lite.canonical.codex_worker_registry import refresh_codex_worker_registry, run_approved_codex_worker_task


class CanonicalCodexWorkerRegistryTest(unittest.TestCase):
    def test_approved_worker_task_records_patch_test_result_and_merge_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "v5" / "codex_outputs" / "patch.diff"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("diff --git a/a b/a\n", encoding="utf-8")
            packet = _engineering_packet()

            def executor(project_dir, record):
                return {
                    "artifacts": [{"path": "v5/codex_outputs/patch.diff", "artifact_type": "codex_patch"}],
                    "result_ref": "result_1",
                    "patch_refs": ["patch_1"],
                    "test_refs": ["test_1"],
                }

            result = run_approved_codex_worker_task(project, packet, executor=executor)

            self.assertEqual(result["status"], "completed")
            registry = result["registry"]
            self.assertEqual(registry["ready_for_merge_count"], 1)
            self.assertEqual(registry["result_registry"][0]["merge_status"], "ready_for_human_merge_approval")
            self.assertTrue((project / "v5" / "codex" / "worker_registry.json").exists())

    def test_registry_blocks_failed_worker_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"

            def executor(project_dir, record):
                raise RuntimeError("tests failed")

            run_approved_codex_worker_task(project, _engineering_packet(), executor=executor)
            registry = refresh_codex_worker_registry(project)

            self.assertEqual(registry["blocked_count"], 1)
            self.assertEqual(registry["result_registry"][0]["merge_status"], "blocked")


def _engineering_packet():
    return {
        "task_id": "engineering_task_1",
        "packet_type": "EngineeringTaskPacket",
        "allowed_paths": ["targetcompass_lite/canonical/**"],
        "forbidden_paths": list(REQUIRED_ENGINEERING_FORBIDDEN_PATHS),
        "expected_patch_summary": "Patch canonical code only.",
        "test_commands": ["python -m unittest tests.test_canonical_codex_worker_registry -v"],
    }


if __name__ == "__main__":
    unittest.main()
