import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.mcp_gateway import build_mcp_gateway, call_tool, load_call_audit, read_resource
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
            self.assertEqual(gateway["tools"]["schema_version"], "v4.mcp_tool_manifest/0.1")
            self.assertIn("resource.read", {row["tool_id"] for row in gateway["tools"]["tools"]})

            uri = "project://demo"
            result = read_resource(project, uri, actor="unit_test")
            self.assertEqual(result["uri"], uri)
            audit = load_call_audit(project)
            self.assertEqual(audit[-1]["tool_id"], "resource.read")
            self.assertEqual(audit[-1]["status"], "success")
            self.assertTrue((project / "v4" / "mcp_call_audit_summary.json").exists())

            html = _v4_work_order_panel(project)
            self.assertIn("Local MCP Gateway", html)
            self.assertIn("resource.read", html)

    def test_call_tool_records_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("demo", encoding="utf-8")
            build_mcp_gateway(project)

            out = call_tool(project, "resource.read", {"uri": "project://demo"}, actor="unit_test")
            self.assertEqual(out["text"], "demo")
            with self.assertRaises(ValueError):
                call_tool(project, "resource.read", {"uri": "missing://demo"}, actor="unit_test")
            audit = load_call_audit(project)
            self.assertEqual(audit[-2]["status"], "success")
            self.assertEqual(audit[-1]["status"], "failed")
            self.assertEqual(audit[-1]["tool_id"], "resource.read")


if __name__ == "__main__":
    unittest.main()
