import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

from .mcp_gateway import build_mcp_gateway, call_tool, read_resource
from .mcp_policy import parse_token
from .paths import project_path


PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "targetcompass-lite", "version": "0.1.0"}


def handle_jsonrpc(project_dir: Path, message: dict[str, Any], token: str | None = None) -> dict[str, Any] | None:
    if isinstance(message, list):
        return _error(None, -32600, "JSON-RPC batch requests are not supported")
    msg_id = message.get("id")
    method = message.get("method", "")
    params = message.get("params") or {}
    try:
        if method == "initialize":
            return _response(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "resources": {"listChanged": False},
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": SERVER_INFO,
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "resources/list":
            return _response(msg_id, {"resources": _mcp_resources(project_dir, token)})
        if method == "resources/read":
            uri = params.get("uri", "")
            if not uri:
                return _error(msg_id, -32602, "resources/read requires params.uri")
            result = read_resource(project_dir, uri, actor="mcp_server", token=token)
            return _response(
                msg_id,
                {
                    "contents": [
                        {
                            "uri": result["uri"],
                            "name": Path(result["path"]).name,
                            "mimeType": _mime_type(result["path"]),
                            "text": result["text"],
                        }
                    ]
                },
            )
        if method == "tools/list":
            return _response(msg_id, {"tools": _mcp_tools(project_dir, token)})
        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if not name:
                return _error(msg_id, -32602, "tools/call requires params.name")
            try:
                result = call_tool(project_dir, name, arguments, actor="mcp_server", token=token)
                return _response(msg_id, _tool_result(result, is_error=False))
            except Exception as exc:
                return _response(msg_id, _tool_result({"error": str(exc), "tool": name}, is_error=True))
        return _error(msg_id, -32601, f"Method not found: {method}")
    except Exception as exc:
        return _error(msg_id, -32603, str(exc))


def run_stdio_server(project: str, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None, token: str | None = None) -> None:
    project_dir = project_path(project)
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while True:
        message = _read_framed_message(stdin)
        if message is None:
            return
        response = handle_jsonrpc(project_dir, message, token=token)
        if response is not None:
            _write_framed_message(stdout, response)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run TargetCompass Lite as an MCP stdio server.")
    parser.add_argument("--project", default="vascular_aging_demo")
    parser.add_argument("--token-json", default="")
    args = parser.parse_args()
    run_stdio_server(args.project, token=args.token_json or None)


def _mcp_resources(project_dir: Path, token: str | None = None) -> list[dict[str, Any]]:
    principal = parse_token(project_dir, token, actor="mcp_server")
    gateway = build_mcp_gateway(project_dir, principal=principal)
    resources = []
    for row in gateway["resources"]["resources"]:
        resources.append(
            {
                "uri": row["uri"],
                "name": Path(row["path"]).name,
                "title": row["uri"],
                "description": f"TargetCompass project resource: {row.get('resource_type', '')}",
                "mimeType": _mime_type(row["path"]),
                "annotations": {"audience": ["assistant"], "priority": 0.8},
            }
        )
    return resources


def _mcp_tools(project_dir: Path, token: str | None = None) -> list[dict[str, Any]]:
    principal = parse_token(project_dir, token, actor="mcp_server")
    gateway = build_mcp_gateway(project_dir, principal=principal)
    tools = []
    for row in gateway["tools"]["tools"]:
        tools.append(
            {
                "name": row["tool_id"],
                "title": row["tool_id"],
                "description": row.get("purpose", ""),
                "inputSchema": _json_schema(row.get("input_schema", {})),
                "annotations": {
                    "readOnlyHint": row.get("risk") == "read_only",
                    "destructiveHint": False,
                    "openWorldHint": row.get("risk") != "read_only",
                },
            }
        )
    return tools


def _json_schema(simple_schema: dict[str, Any]) -> dict[str, Any]:
    properties = {}
    required = []
    for name, typ in simple_schema.items():
        properties[name] = {"type": typ if typ in {"string", "number", "integer", "boolean", "object", "array"} else "string"}
        required.append(name)
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": True}


def _tool_result(result: Any, is_error: bool) -> dict[str, Any]:
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    payload = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if isinstance(result, (dict, list)):
        payload["structuredContent"] = result
    return payload


def _response(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def _read_framed_message(stream: BinaryIO) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == b"":
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    raw = stream.read(length)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _write_framed_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
    stream.flush()


def _mime_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".json", ".jsonl"}:
        return "application/json"
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix in {".csv", ".tsv", ".txt", ".yaml", ".yml"}:
        return "text/plain"
    if suffix in {".html", ".htm"}:
        return "text/html"
    return "text/plain"


if __name__ == "__main__":
    main()
