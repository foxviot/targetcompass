import io
import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.mcp_server import _read_framed_message, _write_framed_message, handle_jsonrpc
from targetcompass_lite.v4 import build_v4_manifest


class McpServerTest(unittest.TestCase):
    def test_mcp_server_exposes_resources_tools_and_calls_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            (project / "research_spec.json").write_text(
                json.dumps(
                    {
                        "project_id": "demo",
                        "research_theme": "vascular aging",
                        "disease_scope": {"canonical": "vascular aging"},
                        "organisms": ["human"],
                        "priority_tissues": ["artery"],
                        "priority_cells": ["endothelial cell"],
                        "target_routes": ["secreted"],
                    }
                ),
                encoding="utf-8",
            )
            (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")
            build_v4_manifest(project)

            init = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
            self.assertIn("tools", init["result"]["capabilities"])

            resources = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
            uris = {row["uri"] for row in resources["result"]["resources"]}
            self.assertIn("project://demo", uris)
            self.assertIn("evidence://demo/review-report-index/latest", uris)

            read = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "project://demo"}})
            self.assertIn("vascular aging", read["result"]["contents"][0]["text"])

            tools = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
            tool_names = {row["name"] for row in tools["result"]["tools"]}
            self.assertIn("review.queue.build", tool_names)
            self.assertIn("evidence.index.build", tool_names)
            self.assertIn("evidence.trace.query", tool_names)

            called = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "review.queue.build", "arguments": {}}})
            self.assertFalse(called["result"]["isError"])
            self.assertEqual(called["result"]["structuredContent"]["queue_count"], 0)
            indexed = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "evidence.index.build", "arguments": {}}})
            self.assertFalse(indexed["result"]["isError"])
            self.assertIn("index_id", indexed["result"]["structuredContent"])
            self.assertTrue((project / "v4" / "mcp_call_audit.jsonl").exists())

    def test_content_length_framing_roundtrip(self):
        stream = io.BytesIO()
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        _write_framed_message(stream, message)
        stream.seek(0)
        self.assertEqual(_read_framed_message(stream), message)


if __name__ == "__main__":
    unittest.main()
