import json
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .consistency import run_consistency_check
from .evidence_db import import_evidence
from .evidence_index import build_evidence_review_report_index, evidence_trace_detail, query_evidence_trace
from .package import export_run_package
from .paths import project_path
from .registry_snapshots import build_registry_snapshots, load_registry_snapshots
from .reporting import build_report
from .review import final_signoff
from .service_boundaries import build_service_boundaries
from .system_status import system_status
from .v4 import content_hash, v4_dir


SERVICE_RUNTIME_SCHEMA = "v4.service_runtime/0.1"
SERVICE_AUDIT_SCHEMA = "v4.service_request_audit/0.1"

SERVICE_IDENTITIES = {
    "project_api": {"can_call": ["registry_service", "evidence_service", "report_service"]},
    "evidence_service": {"can_call": ["registry_service"]},
    "registry_service": {"can_call": []},
    "report_service": {"can_call": ["evidence_service", "project_api"]},
    "mcp_gateway": {"can_call": ["project_api", "evidence_service", "registry_service", "report_service"]},
    "test_harness": {"can_call": ["project_api", "evidence_service", "registry_service", "report_service"]},
}

SERVICE_ENDPOINTS = {
    "project_api": ["health", "status", "boundaries", "consistency_check"],
    "evidence_service": ["health", "import", "trace_index", "trace_query", "trace_detail"],
    "registry_service": ["health", "snapshot", "snapshot_read"],
    "report_service": ["health", "build", "export_package", "signoff", "validate"],
}


def dispatch_service_request(
    service_id: str,
    action: str,
    project_dir: Path,
    payload: dict[str, Any] | None = None,
    caller: str = "mcp_gateway",
    trace_id: str = "",
) -> dict[str, Any]:
    payload = payload or {}
    trace_id = trace_id or "trace_" + content_hash({"service": service_id, "action": action, "time": _now()})[:16]
    started = _now()
    try:
        _authorize_service_call(service_id, caller)
        result = _dispatch_authorized(service_id, action, project_dir, payload)
        status = "success"
        failure_reason = ""
    except Exception as exc:
        result = {}
        status = "failed"
        failure_reason = str(exc)
    response = {
        "schema_version": SERVICE_RUNTIME_SCHEMA,
        "service_id": service_id,
        "action": action,
        "project_id": project_dir.name,
        "caller": caller,
        "trace_id": trace_id,
        "status": status,
        "started_at": started,
        "finished_at": _now(),
        "result": result,
        "failure_reason": failure_reason,
    }
    _write_service_audit(project_dir, response, payload)
    if status != "success":
        raise RuntimeError(f"{service_id}.{action} failed: {failure_reason}")
    return response


def run_service(service_id: str, host: str = "127.0.0.1", port: int = 8800) -> None:
    if service_id not in SERVICE_ENDPOINTS:
        raise ValueError(f"unknown service_id: {service_id}")

    class Handler(ServiceHttpHandler):
        target_service_id = service_id

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving TargetCompass {service_id} at http://{host}:{port}/")
    server.serve_forever()


class ServiceHttpHandler(BaseHTTPRequestHandler):
    server_version = "TargetCompassService/0.1"
    target_service_id = ""

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service_id": self.target_service_id, "endpoints": SERVICE_ENDPOINTS.get(self.target_service_id, [])})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[0] != "v1":
            self._send_json(404, {"error": "not found"})
            return
        action = parts[1]
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:
            self._send_json(400, {"error": f"invalid JSON: {exc}"})
            return
        project = body.get("project", "")
        if not project:
            self._send_json(400, {"error": "project is required"})
            return
        caller = self.headers.get("X-TargetCompass-Caller", "mcp_gateway")
        trace_id = self.headers.get("X-TargetCompass-Trace-ID", "")
        try:
            response = dispatch_service_request(self.target_service_id, action, project_path(project), body.get("payload", {}), caller=caller, trace_id=trace_id)
            self._send_json(200, response)
        except PermissionError as exc:
            self._send_json(403, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:
        return


def service_runtime_manifest(project_dir: Path) -> dict[str, Any]:
    boundaries = build_service_boundaries(project_dir)
    payload = {
        "schema_version": SERVICE_RUNTIME_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "mode": "local_standalone_services",
        "external_tool_entrypoint": "mcp_gateway",
        "services": [
            {
                "service_id": service_id,
                "endpoints": endpoints,
                "identity": {"allowed_callers": _allowed_callers(service_id)},
            }
            for service_id, endpoints in SERVICE_ENDPOINTS.items()
        ],
        "service_boundary_hash": boundaries.get("boundary_hash", ""),
        "runtime_hash": content_hash({"services": SERVICE_ENDPOINTS, "identities": SERVICE_IDENTITIES}),
    }
    path = service_runtime_manifest_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def service_runtime_manifest_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "service_runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def service_audit_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "service_request_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def query_service_audit(project_dir: Path, service_id: str = "", caller: str = "", status: str = "", limit: int = 50) -> dict[str, Any]:
    rows = []
    path = service_audit_path(project_dir)
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if service_id and row.get("service_id") != service_id:
                    continue
                if caller and row.get("caller") != caller:
                    continue
                if status and row.get("status") != status:
                    continue
                rows.append(row)
    return {
        "schema_version": "v4.service_audit_query/0.1",
        "project_id": project_dir.name,
        "query": {"service_id": service_id, "caller": caller, "status": status, "limit": limit},
        "match_count": len(rows),
        "items": rows[-limit:],
    }


def _dispatch_authorized(service_id: str, action: str, project_dir: Path, payload: dict[str, Any]) -> Any:
    if action not in SERVICE_ENDPOINTS.get(service_id, []):
        raise ValueError(f"{service_id} does not expose action: {action}")
    if action == "health":
        return {"status": "ok", "service_id": service_id}
    if service_id == "project_api":
        return _project_action(action, project_dir)
    if service_id == "evidence_service":
        return _evidence_action(action, project_dir, payload)
    if service_id == "registry_service":
        return _registry_action(action, project_dir)
    if service_id == "report_service":
        return _report_action(action, project_dir, payload)
    raise ValueError(f"unknown service_id: {service_id}")


def _project_action(action: str, project_dir: Path) -> Any:
    if action == "status":
        return {"items": system_status(project_dir)}
    if action == "boundaries":
        return build_service_boundaries(project_dir)
    if action == "consistency_check":
        return run_consistency_check(project_dir)
    raise ValueError(f"unsupported project action: {action}")


def _evidence_action(action: str, project_dir: Path, payload: dict[str, Any]) -> Any:
    if action == "import":
        return {"path": _rel(import_evidence(project_dir), project_dir)}
    if action == "trace_index":
        return build_evidence_review_report_index(project_dir)
    if action == "trace_query":
        return query_evidence_trace(project_dir, gene=payload.get("gene", ""), evidence_id=payload.get("evidence_id", ""), review_status=payload.get("review_status", ""))
    if action == "trace_detail":
        return evidence_trace_detail(project_dir, gene=payload.get("gene", ""), evidence_id=payload.get("evidence_id", ""))
    raise ValueError(f"unsupported evidence action: {action}")


def _registry_action(action: str, project_dir: Path) -> Any:
    if action == "snapshot":
        return build_registry_snapshots(project_dir)
    if action == "snapshot_read":
        return load_registry_snapshots(project_dir)
    raise ValueError(f"unsupported registry action: {action}")


def _report_action(action: str, project_dir: Path, payload: dict[str, Any]) -> Any:
    if action == "build":
        html_path, docx_path = build_report(project_dir)
        return {"html": _rel(html_path, project_dir), "docx": _rel(docx_path, project_dir)}
    if action == "export_package":
        return {"package": _rel(export_run_package(project_dir), project_dir)}
    if action == "signoff":
        return final_signoff(project_dir, signer=payload.get("signer", "service"), reason=payload.get("reason", ""), status=payload.get("status", "signed_off"))
    if action == "validate":
        return run_consistency_check(project_dir)
    raise ValueError(f"unsupported report action: {action}")


def _authorize_service_call(service_id: str, caller: str) -> None:
    if caller == service_id:
        return
    identity = SERVICE_IDENTITIES.get(caller)
    if not identity:
        raise PermissionError(f"unknown service caller: {caller}")
    if service_id not in identity.get("can_call", []):
        raise PermissionError(f"{caller} is not allowed to call {service_id}")


def _allowed_callers(service_id: str) -> list[str]:
    return sorted(caller for caller, identity in SERVICE_IDENTITIES.items() if service_id in identity.get("can_call", []) or caller == service_id)


def _write_service_audit(project_dir: Path, response: dict[str, Any], payload: dict[str, Any]) -> None:
    row = {
        "schema_version": SERVICE_AUDIT_SCHEMA,
        "timestamp": _now(),
        "project_id": project_dir.name,
        "service_id": response.get("service_id", ""),
        "action": response.get("action", ""),
        "caller": response.get("caller", ""),
        "trace_id": response.get("trace_id", ""),
        "status": response.get("status", ""),
        "failure_reason": response.get("failure_reason", ""),
        "request_hash": content_hash(payload),
        "response_hash": content_hash(response.get("result", {})),
    }
    with service_audit_path(project_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
