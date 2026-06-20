import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.v4 import build_v4_manifest, compile_v4_work_orders


class V4ManifestTest(unittest.TestCase):
    def test_v4_manifest_writes_state_resources_and_evidence_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            (project / "research_spec.json").write_text(
                json.dumps(
                    {
                        "project_id": "demo",
                        "research_theme": "vascular aging",
                        "disease_scope": {"canonical": "vascular aging", "related_phenotypes": ["endothelial senescence"]},
                        "organisms": ["human"],
                        "priority_tissues": ["artery"],
                        "priority_cells": ["endothelial cell"],
                        "target_routes": ["secreted"],
                        "constraints": {"claim_policy": "association_only"},
                    }
                ),
                encoding="utf-8",
            )
            plan = {
                "plan_version": "0.3",
                "project_id": "demo",
                "module_registry": "analysis_module_registry.json",
                "modules": [
                    {
                        "module_id": "P4_bulk_deg_ds1",
                        "module": "bulk_deg",
                        "dataset_id": "ds1",
                        "command": "python tc_lite.py run-deg --project demo --dataset ds1",
                        "inputs": {"dataset_card": "dataset_cards/ds1.yaml"},
                        "parameters": {"case": "aged", "control": "young"},
                        "expected_outputs": ["results/bulk_deg_ds1/deg_results.tsv"],
                        "qc_checks": ["manifest exists"],
                        "allowed_files": ["targetcompass_lite/deg.py"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            manifest = build_v4_manifest(project, plan)
            self.assertEqual(manifest["schema_version"], "v4.object_manifest/0.1")
            self.assertTrue((project / "v4" / "state_machine.json").exists())
            self.assertTrue((project / "v4" / "mcp_resources.json").exists())
            self.assertTrue((project / "v4" / "evidence_snapshot.json").exists())
            resources = json.loads((project / "v4" / "mcp_resources.json").read_text(encoding="utf-8"))["resources"]
            self.assertIn("plan://demo/latest", {row["uri"] for row in resources})

    def test_missing_registered_module_generates_codex_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text("{}", encoding="utf-8")
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
            orders = compile_v4_work_orders(project, plan)
            self.assertEqual(orders[0]["work_order_type"], "BUILD_ADAPTER")
            self.assertTrue(orders[0]["requires_codex"])
            packet = project / orders[0]["codex_task_packet"]
            self.assertTrue(packet.exists())
            payload = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(payload["task_type"], "BUILD_ADAPTER")
            self.assertIn("targetcompass_lite/db_adapters/**", payload["allowed_paths"])


if __name__ == "__main__":
    unittest.main()
