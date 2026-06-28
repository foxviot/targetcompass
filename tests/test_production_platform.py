import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.engineering_release import build_engineering_release_gate, build_sbom_manifest
from targetcompass_lite.observability import build_observability_manifest
from targetcompass_lite.production_storage import build_production_storage_readiness
from targetcompass_lite.service_topology import build_service_topology
from targetcompass_lite.services import dispatch_service_request
from targetcompass_lite.v4 import compile_v4_work_orders, load_codex_task_packet
from targetcompass_lite.codex_engineering import record_codex_result, register_codex_patch, register_codex_test_result
from targetcompass_lite.review import record_review
from targetcompass_lite.webapp import _v4_work_order_panel


class ProductionPlatformTest(unittest.TestCase):
    def test_p8_p10_p11_artifacts_are_generated_and_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            storage = build_production_storage_readiness(project)
            obs = build_observability_manifest(project)
            topology = build_service_topology(project)

            self.assertEqual(storage["schema_version"], "v4.production_storage_readiness/0.1")
            self.assertEqual(obs["schema_version"], "v4.observability_manifest/0.1")
            self.assertEqual(topology["schema_version"], "v4.service_topology/0.1")
            self.assertTrue((project / "v4" / "production_storage_readiness.json").exists())
            self.assertTrue((project / "v4" / "observability_runbook.md").exists())
            self.assertTrue((project / "v4" / "service_topology.json").exists())

            service_storage = dispatch_service_request("evidence_service", "production_storage_readiness", project, caller="mcp_gateway")["result"]
            service_obs = dispatch_service_request("project_api", "observability_manifest", project, caller="mcp_gateway")["result"]
            service_topology = dispatch_service_request("project_api", "service_topology", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_storage["schema_version"], "v4.production_storage_readiness/0.1")
            self.assertEqual(service_obs["schema_version"], "v4.observability_manifest/0.1")
            self.assertEqual(service_topology["schema_version"], "v4.service_topology/0.1")

            html = _v4_work_order_panel(project)
            for text in ["Production storage readiness", "v4 production platform", "Observability signals", "Service topology"]:
                self.assertIn(text, html)

    def test_p9_release_gate_and_sbom_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            (project / "targetcompass_lite" / "db_adapters").mkdir(parents=True)
            (project / "targetcompass_lite" / "db_adapters" / "new_adapter.py").write_text("# adapter\n", encoding="utf-8")
            orders = compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_adapter",
                            "module": "new_external_adapter",
                            "dataset_id": "external",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["targetcompass_lite/db_adapters/new_adapter.py"],
                            "qc_checks": ["tests pass"],
                            "allowed_files": ["targetcompass_lite/db_adapters/new_adapter.py"],
                        }
                    ],
                },
            )
            packet = load_codex_task_packet(project, orders[0])
            patch_path = project / "change.patch"
            patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
            (project / "scoped.log").write_text("passed\n", encoding="utf-8")
            register_codex_patch(project, packet["codex_job_id"], "change.patch", summary="adapter")
            register_codex_test_result(project, packet["codex_job_id"], "python -m unittest old", "failed", stdout_ref="old.log")
            register_codex_test_result(project, packet["codex_job_id"], "python -m unittest scoped", "passed", stdout_ref="scoped.log")
            result = record_codex_result(project, packet["codex_job_id"], "success", artifacts=["targetcompass_lite/db_adapters/new_adapter.py", "scoped.log"])
            record_review(project, "codex_result", result["result_id"], "approve", reason="tests passed")

            gate = build_engineering_release_gate(project)
            sbom = build_sbom_manifest(project)
            service_gate = dispatch_service_request("engineering_service", "release_gate", project, caller="mcp_gateway")["result"]

            self.assertEqual(gate["schema_version"], "v4.codex_engineering_release_gate/0.1")
            self.assertEqual(gate["status"], "READY")
            self.assertEqual(gate["items"][0]["gate_status"], "READY_TO_MERGE")
            self.assertEqual(sbom["schema_version"], "v4.sbom_manifest/0.1")
            self.assertEqual(service_gate["status"], "READY")
            html = _v4_work_order_panel(project)
            self.assertIn("Codex engineering release gate", html)
            self.assertIn("SBOM contract", html)


def _write_project(project: Path) -> None:
    project.mkdir()
    (project / "reports").mkdir()
    (project / "results").mkdir()
    (project / "v4").mkdir()
    (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
    (project / "research_spec.json").write_text(json.dumps({"project_id": "demo"}), encoding="utf-8")
    (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")
    (project / "reports" / "target_report.html").write_text("<html>demo</html>", encoding="utf-8")
    con = sqlite3.connect(project / "evidence.sqlite")
    con.close()


if __name__ == "__main__":
    unittest.main()
