import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.codex_engineering import (
    create_isolated_workspace,
    load_codex_engineering,
    record_codex_result,
    register_codex_patch,
    register_codex_test_result,
)
from targetcompass_lite.review import build_review_queue, record_review
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
            updated_order = load_v4_work_orders(project)[0]
            self.assertEqual(updated_order["codex_result_status"], "success")
            self.assertEqual(updated_order["status"], "engineering_review_required")

            queue = build_review_queue(project)
            self.assertIn("codex_result", {row["item_type"] for row in queue["items"]})
            record_review(project, "codex_result", result["result_id"], "approve", reason="tests passed and patch is scoped")
            reviewed = load_codex_engineering(project)["results"][0]
            self.assertEqual(reviewed["merge_status"], "approved_for_merge")
            self.assertIn("Codex engineering loop", _v4_work_order_panel(project))
            self.assertIn(result["result_id"], _v4_work_order_panel(project))


if __name__ == "__main__":
    unittest.main()
