import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .mcp_server import SERVER_INFO, handle_jsonrpc
from .mcp_sessions import build_mcp_client_config, start_session, touch_session
from .paths import project_path


def run_http_server(host: str = "127.0.0.1", port: int = 8790) -> None:
    server = ThreadingHTTPServer((host, port), McpHttpHandler)
    print(f"Serving TargetCompass MCP HTTP at http://{host}:{port}/")
    server.serve_forever()


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = "TargetCompassMCPHTTP/0.1"

    def do_GET(self) -> None:
        route = _parse_route(self.path)
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "serverInfo": SERVER_INFO})
            return
        if route and route["suffix"] == "events":
            self._send_sse(route["project"])
            return
        if route and route["suffix"] == "client-config":
            project_dir = project_path(route["project"])
            base_url = f"http://{self.headers.get('Host', '127.0.0.1:8790')}/mcp/{route['project']}"
            self._send_json(200, build_mcp_client_config(project_dir, base_url=base_url))
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        route = _parse_route(self.path)
        if not route or route["suffix"]:
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"invalid JSON: {exc}"}})
            return
        project_dir = project_path(route["project"])
        token = _token_from_headers(self.headers)
        if not token:
            self._send_json(
                401,
                {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32001, "message": "MCP token is required for HTTP clients"}},
            )
            return
        client_id = self.headers.get("X-MCP-Client-ID", "http_client")
        session_id = self.headers.get("X-MCP-Session-ID", "")
        try:
            if session_id:
                touch_session(project_dir, session_id)
            else:
                session = start_session(project_dir, token, client_id=client_id, transport="http")
                session_id = session["session_id"]
            response = handle_jsonrpc(project_dir, message, token=token, session_id=session_id)
            if response is None:
                response = {"jsonrpc": "2.0", "id": message.get("id"), "result": {"ack": True}}
            self._send_json(200, response, extra_headers={"X-MCP-Session-ID": session_id})
        except PermissionError as exc:
            self._send_json(
                401,
                {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32001, "message": str(exc)}},
            )
        except Exception as exc:
            self._send_json(
                500,
                {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32603, "message": str(exc)}},
            )

    def _send_json(self, status: int, payload: dict[str, Any], extra_headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, project: str) -> None:
        project_dir = project_path(project)
        payload = {
            "event": "mcp.ready",
            "project": project_dir.name,
            "serverInfo": SERVER_INFO,
            "transport": "sse-lite",
        }
        raw = ("event: mcp.ready\n" + "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _parse_route(path: str) -> dict[str, str] | None:
    parsed = urllib.parse.urlparse(path)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "mcp":
        return None
    suffix = "/".join(parts[2:]) if len(parts) > 2 else ""
    return {"project": urllib.parse.unquote(parts[1]), "suffix": suffix}


def _token_from_headers(headers: Any) -> str | None:
    raw = headers.get("X-MCP-Token", "").strip()
    if raw:
        return urllib.parse.unquote(raw)
    auth = headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return urllib.parse.unquote(auth.split(" ", 1)[1].strip())
    return None
