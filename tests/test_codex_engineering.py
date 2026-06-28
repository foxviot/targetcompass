import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.codex_engineering import (
    apply_approved_codex_result,
    create_isolated_workspace,
    load_codex_engineering,
    prepare_git_worktree,
    record_codex_result,
    register_codex_patch,
    register_codex_test_result,
    run_codex_task_tests,
)
from targetcompass_lite.consistency import run_consistency_check
from targetcompass_lite.review import build_review_queue, record_review
from targetcompass_lite.task_registry import build_task_registry
from targetcompass_lite.v4 import compile_v4_work_orders, load_codex_task_packet, load_v4_work_orders
from targetcompass_lite.webapp import _v4_work_order_panel


class CodexEngineeringTest(unittest.TestCase):
    def test_codex_engineering_result_is_registered_and_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "targetcompass_lite" / "db_adapters").mkdir(parents=True)
            (project / "targetcompass_lite" / "db_adapters" / "placeholder.py").write_text("# fixture\n", encoding="utf-8")
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_new_adapter_x",
                            "module": "new_external_adapter",
                            "dataset_id": "external_x",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/db_adapters/new_external_adapter.py"],
                            "qc_checks": ["schema validated"],
                            "allowed_files": ["targetcompass_lite/db_adapters/placeholder.py"],
                        }
                    ],
                },
            )
            order = orders[0]
            packet = load_codex_task_packet(project, order)
            workspace = create_isolated_workspace(project, order["work_order_id"], actor="test")
            self.assertTrue((project / workspace["workspace_path"] / "task_packet.json").exists())
            self.assertEqual(workspace["copied_inputs"][0]["source"], "targetcompass_lite/db_adapters/placeholder.py")

            patch_path = project / "v4" / "codex_engineering" / "workspaces" / packet["codex_job_id"] / "changes.patch"
            patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
            rel_patch = str(patch_path.relative_to(project))
            patch = register_codex_patch(project, packet["codex_job_id"], rel_patch, summary="add adapter", actor="test")
            test = register_codex_test_result(project, packet["codex_job_id"], "python -m unittest", "passed", actor="test")
            result = record_codex_result(
                project,
                packet["codex_job_id"],
                "success",
                artifacts=["targetcompass_lite/db_adapters/new_external_adapter.py"],
                actor="test",
            )

            data = load_codex_engineering(project)
            self.assertEqual(data["patches"][0]["patch_id"], patch["patch_id"])
            self.assertEqual(data["tests"][0]["test_id"], test["test_id"])
            self.assertEqual(data["results"][0]["result_id"], result["result_id"])
            self.assertTrue((project / "v4" / "codex_engineering" / "engineering_closure.json").exists())
            attempts = json.loads((project / "v4" / "work_order_attempts.json").read_text(encoding="utf-8"))["attempts"]
            self.assertTrue(any(row.get("metadata", {}).get("codex_result", {}).get("result_id") == result["result_id"] for row in attempts))
            updated_order = load_v4_work_orders(project)[0]
            self.assertEqual(updated_order["codex_result_status"], "success")
            self.assertEqual(updated_order["status"], "engineering_review_required")

            queue = build_review_queue(project)
            self.assertIn("codex_result", {row["item_type"] for row in queue["items"]})
            record_review(project, "codex_result", result["result_id"], "approve", reason="tests passed and patch is scoped")
            reviewed = load_codex_engineering(project)["results"][0]
            self.assertEqual(reviewed["merge_status"], "approved_for_merge")
            registry = build_task_registry(project)
            self.assertEqual(registry["tasks"][0]["status"], "ready_to_merge")
            consistency = run_consistency_check(project)
            checks = {row["check"]: row for row in consistency["checks"]}
            self.assertEqual(checks["codex_engineering_results_have_closure"]["status"], "PASS")
            self.assertIn("storage_backend_manifest_is_current", checks)
            self.assertIn("Codex engineering loop", _v4_work_order_panel(project))
            self.assertIn(result["result_id"], _v4_work_order_panel(project))
            self.assertIn("engineering_closure.json", _v4_work_order_panel(project))

    def test_approved_codex_task_can_prepare_git_worktree_and_run_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_new_adapter_x",
                            "module": "new_external_adapter",
                            "dataset_id": "external_x",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/db_adapters/new_external_adapter.py"],
                            "qc_checks": ["schema validated"],
                            "allowed_files": ["targetcompass_lite/db_adapters/**"],
                        }
                    ],
                },
            )
            order = orders[0]
            packet = load_codex_task_packet(project, order)
            packet["tests"] = ["python -m unittest tests.test_packaging -v"]
            packet_path = project / order["codex_task_packet"]
            packet_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
            record_review(project, "codex_task", packet["codex_job_id"], "approve", reason="safe lightweight packaging test")

            worktree = prepare_git_worktree(project, packet["codex_job_id"], actor="test")
            self.assertTrue(Path(worktree["git_worktree_path"]).exists())
            self.assertTrue(worktree["git_branch"].startswith("codex/task-"))

            result = run_codex_task_tests(project, packet["codex_job_id"], actor="test")
            self.assertEqual(result["status"], "success")
            data = load_codex_engineering(project)
            self.assertEqual(data["tests"][-1]["status"], "passed")
            self.assertEqual(data["results"][-1]["merge_status"], "pending_human_approval")
            updated = load_codex_task_packet(project, load_v4_work_orders(project)[0])
            self.assertEqual(updated["execution_status"], "success")

    def test_codex_engineering_rejects_outside_patch_and_shell_metachar_test_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo"
            project.mkdir()
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_guarded_task",
                            "module": "fix_code",
                            "dataset_id": "none",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/guarded.py"],
                            "qc_checks": ["unit test passed"],
                            "allowed_files": ["targetcompass_lite/**"],
                        }
                    ],
                },
            )
            packet = load_codex_task_packet(project, orders[0])
            outside_patch = root / "outside.patch"
            outside_patch.write_text("diff --git a/x b/x\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                register_codex_patch(project, packet["codex_job_id"], str(outside_patch), summary="outside", actor="test")

            packet["tests"] = ["python -m unittest tests.test_packaging -v & echo unsafe"]
            (project / orders[0]["codex_task_packet"]).write_text(json.dumps(packet, indent=2), encoding="utf-8")
            record_review(project, "codex_task", packet["codex_job_id"], "approve", reason="check command guard")

            result = run_codex_task_tests(project, packet["codex_job_id"], actor="test")
            self.assertEqual(result["status"], "failed")
            data = load_codex_engineering(project)
            self.assertEqual(data["tests"][-1]["status"], "skipped")
            self.assertIn("rejected by allowlist", data["tests"][-1]["stderr_ref"])

    def test_record_result_scopes_tests_to_current_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_new_adapter_x",
                            "module": "new_external_adapter",
                            "dataset_id": "external_x",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/db_adapters/new_external_adapter.py"],
                            "qc_checks": ["schema validated"],
                            "allowed_files": ["targetcompass_lite/db_adapters/**"],
                        }
                    ],
                },
            )
            packet = load_codex_task_packet(project, orders[0])
            patch_path = project / "change.patch"
            patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
            register_codex_patch(project, packet["codex_job_id"], "change.patch", summary="adapter", actor="test")
            failed = register_codex_test_result(project, packet["codex_job_id"], "python -m unittest old", "failed", stdout_ref="logs/old.out", actor="test")
            passed = register_codex_test_result(project, packet["codex_job_id"], "python -m unittest scoped", "passed", stdout_ref="logs/scoped.out", actor="test")

            result = record_codex_result(project, packet["codex_job_id"], "success", artifacts=["logs/scoped.out"], actor="test")

            self.assertIn(passed["test_id"], result["test_refs"])
            self.assertNotIn(failed["test_id"], result["test_refs"])

    def test_approved_result_can_apply_registered_patch_and_record_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
            target = repo / "targetcompass_lite" / "merge_fixture.py"
            target.parent.mkdir(parents=True)
            target.write_text("VALUE = 'old'\n", encoding="utf-8")

            project = root / "demo"
            project.mkdir()
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_patch_fixture",
                            "module": "fix_code",
                            "dataset_id": "none",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/merge_fixture.py"],
                            "qc_checks": ["unit test passed"],
                            "allowed_files": ["targetcompass_lite/merge_fixture.py"],
                        }
                    ],
                },
            )
            packet = load_codex_task_packet(project, orders[0])
            patch_text = """diff --git a/targetcompass_lite/merge_fixture.py b/targetcompass_lite/merge_fixture.py
--- a/targetcompass_lite/merge_fixture.py
+++ b/targetcompass_lite/merge_fixture.py
@@ -1 +1 @@
-VALUE = 'old'
+VALUE = 'new'
"""
            patch_path = project / "change.patch"
            patch_path.write_text(patch_text, encoding="utf-8")
            register_codex_patch(project, packet["codex_job_id"], "change.patch", summary="update fixture", actor="test")
            register_codex_test_result(project, packet["codex_job_id"], "python -m unittest tests.test_codex_engineering -v", "passed", actor="test")
            result = record_codex_result(project, packet["codex_job_id"], "success", artifacts=["change.patch"], actor="test")
            record_review(project, "codex_result", result["result_id"], "approve", reason="patch and tests are scoped", reviewer="reviewer")

            with patch("targetcompass_lite.codex_engineering._repo_root", return_value=repo):
                dry_run = apply_approved_codex_result(project, result["result_id"], actor="reviewer", dry_run=True)
                self.assertEqual(dry_run["status"], "dry_run_passed")
                self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 'old'\n")
                merged = apply_approved_codex_result(project, result["result_id"], actor="reviewer")

            self.assertEqual(merged["status"], "merged_to_working_tree")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 'new'\n")
            data = load_codex_engineering(project)
            self.assertEqual(data["merges"][-1]["result_id"], result["result_id"])
            self.assertEqual(data["results"][-1]["merge_status"], "merged")
            registry = build_task_registry(project)
            self.assertEqual(registry["tasks"][0]["status"], "engineering_merged")

    def test_merge_checks_all_patches_before_applying_any_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
            target = repo / "targetcompass_lite" / "merge_fixture.py"
            target.parent.mkdir(parents=True)
            target.write_text("VALUE = 'old'\n", encoding="utf-8")

            project = root / "demo"
            project.mkdir()
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_patch_fixture",
                            "module": "fix_code",
                            "dataset_id": "none",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/merge_fixture.py"],
                            "qc_checks": ["unit test passed"],
                            "allowed_files": ["targetcompass_lite/merge_fixture.py"],
                        }
                    ],
                },
            )
            packet = load_codex_task_packet(project, orders[0])
            valid_patch = """diff --git a/targetcompass_lite/merge_fixture.py b/targetcompass_lite/merge_fixture.py
--- a/targetcompass_lite/merge_fixture.py
+++ b/targetcompass_lite/merge_fixture.py
@@ -1 +1 @@
-VALUE = 'old'
+VALUE = 'new'
"""
            invalid_patch = """diff --git a/targetcompass_lite/missing.py b/targetcompass_lite/missing.py
--- a/targetcompass_lite/missing.py
+++ b/targetcompass_lite/missing.py
@@ -1 +1 @@
-MISSING = 'old'
+MISSING = 'new'
"""
            (project / "valid.patch").write_text(valid_patch, encoding="utf-8")
            (project / "invalid.patch").write_text(invalid_patch, encoding="utf-8")
            first_patch = register_codex_patch(project, packet["codex_job_id"], "valid.patch", summary="valid", actor="test")
            second_patch = register_codex_patch(project, packet["codex_job_id"], "invalid.patch", summary="invalid", actor="test")
            register_codex_test_result(project, packet["codex_job_id"], "python -m unittest tests.test_codex_engineering -v", "passed", actor="test")
            result = record_codex_result(project, packet["codex_job_id"], "success", artifacts=["valid.patch"], actor="test")
            result["patch_refs"] = [first_patch["patch_id"], second_patch["patch_id"]]
            result_registry = project / "v4" / "codex_engineering" / "result_registry.json"
            payload = json.loads(result_registry.read_text(encoding="utf-8"))
            payload["results"][0] = result
            result_registry.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            record_review(project, "codex_result", result["result_id"], "approve", reason="testing all-or-nothing merge", reviewer="reviewer")

            with patch("targetcompass_lite.codex_engineering._repo_root", return_value=repo):
                with self.assertRaises(RuntimeError):
                    apply_approved_codex_result(project, result["result_id"], actor="reviewer")

            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 'old'\n")
            data = load_codex_engineering(project)
            self.assertFalse(data["merges"])


if __name__ == "__main__":
    unittest.main()
