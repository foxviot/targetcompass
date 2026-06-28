import io
import json
import threading
import urllib.error
import urllib.request
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.mcp_http_server import McpHttpHandler
from targetcompass_lite.mcp_server import _read_framed_message, _write_framed_message, handle_jsonrpc, run_stdio_server
from targetcompass_lite.mcp_sessions import build_external_auth_manifest, build_mcp_client_config, build_mcp_server_config, check_external_auth_readiness, create_token, load_sessions, load_token_from_sources, query_mcp_audit, update_policy
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

    def test_mcp_server_filters_external_reader_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            token = json.dumps(
                {
                    "principal": "reader-agent",
                    "role": "agent_reader",
                    "project": "demo",
                    "scopes": ["resource:read", "tool:read"],
                    "token_id": "tok_reader",
                }
            )

            tools = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=token)
            tool_names = {row["name"] for row in tools["result"]["tools"]}
            self.assertIn("method.config.read", tool_names)
            self.assertNotIn("method.config.update", tool_names)

            denied = handle_jsonrpc(
                project,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "method.config.update", "arguments": {"config": {"dataset_scout": "x"}}},
                },
                token=token,
            )
            self.assertTrue(denied["result"]["isError"])
            self.assertIn("missing required scope", denied["result"]["structuredContent"]["error"])

    def test_token_sources_policy_and_audit_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            token = create_token(project, "reader-agent", "agent_reader", scopes=["resource:read", "tool:read"])
            token_file = project / "reader_token.json"
            token_file.write_text(json.dumps(token), encoding="utf-8")
            auth = build_external_auth_manifest(project)
            readiness = check_external_auth_readiness(project)
            server_config = build_mcp_server_config(project, token_file=str(token_file))
            self.assertEqual(auth["project_isolation"]["cross_project_token_rejected"], True)
            self.assertEqual(readiness["schema_version"], "v4.mcp_external_auth_readiness/0.1")
            self.assertTrue((project / "v4" / "mcp_external_auth_readiness.json").exists())
            self.assertIn(readiness["status"], {"READY", "READY_WITH_WARNINGS"})
            self.assertEqual(server_config["external_auth_manifest"], "v4/mcp_external_auth_manifest.json")
            self.assertTrue((project / "v4" / "mcp_external_auth_manifest.json").exists())

            loaded = load_token_from_sources(token_file=str(token_file))
            self.assertEqual(json.loads(loaded)["principal"], "reader-agent")
            update_policy(project, require_token=True)
            missing = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            self.assertIn("MCP token is required", missing["error"]["message"])

            tools = handle_jsonrpc(project, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=loaded)
            self.assertFalse(any(row["name"] == "method.config.update" for row in tools["result"]["tools"]))
            denied = handle_jsonrpc(
                project,
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "method.config.update", "arguments": {"config": {}}}},
                token=loaded,
            )
            self.assertTrue(denied["result"]["isError"])
            audit = query_mcp_audit(project, principal="reader-agent", status="failed")
            self.assertEqual(audit["call_count"], 1)

    def test_stdio_server_records_client_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            stream_in = io.BytesIO()
            _write_framed_message(stream_in, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            stream_in.seek(0)
            stream_out = io.BytesIO()

            run_stdio_server(str(project), stdin=stream_in, stdout=stream_out, client_id="unit-client")
            sessions = load_sessions(project)["sessions"]
            self.assertEqual(sessions[-1]["client_id"], "unit-client")
            self.assertEqual(sessions[-1]["status"], "closed")

    def test_http_mcp_requires_token_filters_tools_and_records_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            token = create_token(project, "reader-agent", "agent_reader", scopes=["resource:read", "tool:read"])
            server, base = _start_http_server()
            project_url = base + "/mcp/" + urllib.parse.quote(str(project), safe="")
            try:
                missing = _post_json(project_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, expect_status=401)
                self.assertIn("token is required", missing["error"]["message"])

                init, headers = _post_json(
                    project_url,
                    {"jsonrpc": "2.0", "id": 2, "method": "initialize"},
                    token=token,
                    client_id="reader-http",
                    return_headers=True,
                )
                self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
                self.assertTrue(headers.get("X-MCP-Session-ID"))
                tools = _post_json(
                    project_url,
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                    token=token,
                    client_id="reader-http",
                    session_id=headers.get("X-MCP-Session-ID"),
                )
                names = {row["name"] for row in tools["result"]["tools"]}
                self.assertIn("method.config.read", names)
                self.assertNotIn("method.config.update", names)
                sessions = load_sessions(project)["sessions"]
                self.assertEqual(sessions[-1]["transport"], "http")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_mcp_rejects_cross_project_token_and_serves_sse_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_a = Path(tmp) / "demo_a"
            project_b = Path(tmp) / "demo_b"
            project_a.mkdir()
            project_b.mkdir()
            (project_a / "research_interest.md").write_text("a\n", encoding="utf-8")
            (project_b / "research_interest.md").write_text("b\n", encoding="utf-8")
            token_a = create_token(project_a, "reader-agent", "agent_reader", scopes=["resource:read", "tool:read"])
            server, base = _start_http_server()
            project_a_url = base + "/mcp/" + urllib.parse.quote(str(project_a), safe="")
            project_b_url = base + "/mcp/" + urllib.parse.quote(str(project_b), safe="")
            try:
                denied = _post_json(
                    project_b_url,
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    token=token_a,
                    expect_status=401,
                )
                self.assertIn("project scope", denied["error"]["message"])

                events = urllib.request.urlopen(project_a_url + "/events", timeout=5).read().decode("utf-8")
                self.assertIn("event: mcp.ready", events)
                config = urllib.request.urlopen(project_a_url + "/client-config", timeout=5).read().decode("utf-8")
                self.assertIn("http_jsonrpc", config)
                client_config = build_mcp_client_config(project_a, base_url=project_a_url)
                self.assertEqual(client_config["transports"]["http_jsonrpc"]["url"], project_a_url)
            finally:
                server.shutdown()
                server.server_close()

    def test_content_length_framing_roundtrip(self):
        stream = io.BytesIO()
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        _write_framed_message(stream, message)
        stream.seek(0)
        self.assertEqual(_read_framed_message(stream), message)


if __name__ == "__main__":
    unittest.main()


def _start_http_server():
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), McpHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def _post_json(url, payload, token=None, client_id="unit-http", session_id="", expect_status=200, return_headers=False):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-MCP-Client-ID": client_id}
    if token:
        headers["X-MCP-Token"] = urllib.parse.quote(json.dumps(token), safe="")
    if session_id:
        headers["X-MCP-Session-ID"] = session_id
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=5)
        body = json.loads(response.read().decode("utf-8"))
        if expect_status != response.status:
            raise AssertionError(f"expected HTTP {expect_status}, got {response.status}: {body}")
        return (body, response.headers) if return_headers else body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        if expect_status != exc.code:
            raise AssertionError(f"expected HTTP {expect_status}, got {exc.code}: {body}")
        return (body, exc.headers) if return_headers else body
