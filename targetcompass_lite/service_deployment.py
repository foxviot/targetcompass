import json
import sys
from pathlib import Path
from typing import Any

from .services import SERVICE_ENDPOINTS, SERVICE_IDENTITIES
from .v4 import content_hash, v4_dir


SERVICE_DEPLOYMENT_SCHEMA = "v4.service_deployment/0.1"

DEFAULT_PORTS = {
    "project_api": 8810,
    "orchestrator_service": 8811,
    "agent_service": 8812,
    "engineering_service": 8813,
    "evidence_service": 8814,
    "registry_service": 8815,
    "report_service": 8816,
}


def build_service_deployment(project_dir: Path, host: str = "127.0.0.1", base_port: int = 8810) -> dict[str, Any]:
    from .mcp_sessions import build_external_auth_manifest

    ports = _ports(base_port)
    auth = build_external_auth_manifest(project_dir)
    services = []
    for service_id, endpoints in SERVICE_ENDPOINTS.items():
        if service_id == "mcp_gateway":
            continue
        port = ports.get(service_id, base_port + len(services))
        services.append(
            {
                "service_id": service_id,
                "host": host,
                "port": port,
                "base_url": f"http://{host}:{port}",
                "health_url": f"http://{host}:{port}/health",
                "endpoints": [f"/v1/{endpoint}" for endpoint in endpoints if endpoint != "health"],
                "start_command": f"{_python_command()} tc_lite.py service-run --project {project_dir.name} --service-id {service_id} --host {host} --port {port}",
                "identity": {
                    "allowed_callers": _allowed_callers(service_id),
                    "may_call": SERVICE_IDENTITIES.get(service_id, {}).get("can_call", []),
                },
            }
        )
    payload = {
        "schema_version": SERVICE_DEPLOYMENT_SCHEMA,
        "project_id": project_dir.name,
        "mode": "multi_process_local_services",
        "external_entrypoint": {
            "service_id": "mcp_gateway",
            "policy": "external clients call MCP Gateway; Gateway dispatches to service contracts",
            "auth_manifest": "v4/mcp_external_auth_manifest.json",
            "active_auth_mode": auth.get("active_auth_mode", "local_project_token"),
        },
        "services": services,
        "launcher": {
            "powershell": "scripts/start_v4_services.ps1",
            "stop_policy": "Ctrl+C each service terminal or stop spawned process group",
        },
        "deployment_hash": content_hash({"services": services, "identities": SERVICE_IDENTITIES}),
        "production_contract": {
            "long_running_processes": True,
            "project_level_isolation": True,
            "external_clients_must_use_mcp_gateway": True,
            "oidc_or_vault_ready_when_env_configured": True,
        },
    }
    out = service_deployment_path(project_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_powershell_launcher(project_dir, payload)
    return payload


def service_deployment_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "service_deployment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_powershell_launcher(project_dir: Path, deployment: dict[str, Any]) -> None:
    scripts = project_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"Set-Location -LiteralPath '{Path.cwd()}'",
        f"$env:TARGETCOMPASS_PROJECT = '{project_dir.name}'",
    ]
    for service in deployment.get("services", []):
        command = service["start_command"].replace("'", "''")
        lines.append(f"Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoExit', '-Command', '{command}'")
    (scripts / "start_v4_services.ps1").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _python_command() -> str:
    return f"& '{sys.executable}'"


def _ports(base_port: int) -> dict[str, int]:
    if base_port == 8810:
        return dict(DEFAULT_PORTS)
    return {service_id: base_port + idx for idx, service_id in enumerate(DEFAULT_PORTS)}


def _allowed_callers(service_id: str) -> list[str]:
    return sorted(caller for caller, identity in SERVICE_IDENTITIES.items() if service_id in identity.get("can_call", []) or caller == service_id)
