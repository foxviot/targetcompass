import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.mcp_gateway import build_mcp_gateway, call_tool, load_call_audit, read_resource
from targetcompass_lite.mcp_policy import load_policy_decisions, parse_token
from targetcompass_lite.services import query_service_audit
from targetcompass_lite.v4 import build_v4_manifest
from targetcompass_lite.webapp import _v4_work_order_panel


class McpGatewayTest(unittest.TestCase):
    def test_gateway_writes_resource_tool_and_audit_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging", encoding="utf-8")
            (project / "research_spec.json").write_text(json.dumps({"project_id": "demo", "disease_scope": {"canonical": "vascular aging"}}), encoding="utf-8")
            (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")

            manifest = build_v4_manifest(project)
            self.assertTrue(manifest["objects"]["mcp_tools"]["exists"])
            gateway = build_mcp_gateway(project)
            self.assertEqual(gateway["tools"]["schema_version"], "v4.mcp_tool_manifest/0.2")
            self.assertEqual(gateway["resources"]["schema_version"], "v4.mcp_resource_manifest/0.3")
            self.assertIn("resource.read", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.index.build", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.trace.query", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("method.registry.list", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("role.runs.list", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence://demo/review-report-index/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertIn("mcp-policy://demo/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertIn("service-boundary://demo/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertTrue((project / "v4" / "mcp_policy.json").exists())
            self.assertTrue((project / "v4" / "service_boundaries.json").exists())

            uri = "project://demo"
            result = read_resource(project, uri, actor="unit_test")
            self.assertEqual(result["uri"], uri)
            audit = load_call_audit(project)
            self.assertEqual(audit[-1]["tool_id"], "resource.read")
            self.assertEqual(audit[-1]["status"], "success")
            self.assertEqual(audit[-1]["role"], "local_admin")
            self.assertTrue((project / "v4" / "mcp_call_audit_summary.json").exists())
            self.assertTrue(load_policy_decisions(project))

            methods = call_tool(project, "method.registry.list", actor="unit_test")
            self.assertIn("dataset_scout", methods["methods"])
            service_audit = query_service_audit(project)
            self.assertTrue(any(row["service_id"] == "registry_service" for row in service_audit["items"]))
            config = call_tool(
                project,
                "method.config.update",
                {"config": {"dataset_scout": "local_dataset_scout_v0"}},
                actor="unit_test",
            )
            self.assertEqual(config["config"]["dataset_scout"], "local_dataset_scout_v0")

            html = _v4_work_order_panel(project)
            self.assertIn("Local MCP Gateway", html)
            self.assertIn("resource.read", html)
            self.assertIn("Evidence -> Review -> Report index", html)
            self.assertIn("Search trace", html)

    def test_reader_token_filters_tools_and_denies_registry_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging", encoding="utf-8")
            token = json.dumps(
                {
                    "principal": "reader-agent",
                    "role": "agent_reader",
                    "project": "demo",
                    "scopes": ["resource:read", "tool:read"],
                    "token_id": "tok_reader",
                }
            )

            gateway = build_mcp_gateway(project)
            self.assertIn("method.config.update", {row["tool_id"] for row in gateway["tools"]["tools"]})
            reader_gateway = build_mcp_gateway(project, principal=parse_token(project, token))
            reader_tools = {row["tool_id"] for row in reader_gateway["tools"]["tools"]}
            self.assertIn("evidence.trace.query", reader_tools)
            self.assertIn("method.config.read", reader_tools)
            self.assertNotIn("method.config.update", reader_tools)

            read = read_resource(project, "project://demo", actor="external_client", token=token)
            self.assertEqual(read["uri"], "project://demo")
            with self.assertRaises(PermissionError):
                call_tool(project, "method.config.update", {"config": {"dataset_scout": "x"}}, actor="external_client", token=token)

            audit = load_call_audit(project)
            self.assertEqual(audit[-1]["status"], "failed")
            self.assertEqual(audit[-1]["principal"], "reader-agent")
            decisions = load_policy_decisions(project)
            self.assertFalse(decisions[-1]["allow"])
            self.assertEqual(decisions[-1]["required_scope"], "registry:write")

    def test_call_tool_records_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("demo", encoding="utf-8")
            build_mcp_gateway(project)

            out = call_tool(project, "resource.read", {"uri": "project://demo"}, actor="unit_test")
            self.assertEqual(out["text"], "demo")
            call_tool(project, "v4.build_manifest", actor="unit_test")
            service_audit = query_service_audit(project, service_id="project_api")
            self.assertGreaterEqual(service_audit["match_count"], 1)
            with self.assertRaises(ValueError):
                call_tool(project, "resource.read", {"uri": "missing://demo"}, actor="unit_test")
            audit = load_call_audit(project)
            self.assertEqual(audit[-2]["status"], "success")
            self.assertEqual(audit[-1]["status"], "failed")
            self.assertEqual(audit[-1]["tool_id"], "resource.read")


if __name__ == "__main__":
    unittest.main()
