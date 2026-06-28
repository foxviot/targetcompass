from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .platform_config import load_platform_config, service_status


SERVICE_CONTROL_SCHEMA = "v5.service_control_manifest/0.1"


def build_service_control_manifest(project_dir: str | Path, *, preferred_port: int | None = None) -> dict[str, Any]:
    project_dir = Path(project_dir)
    cfg = load_platform_config(project_dir)
    configured_port = int(preferred_port or cfg.get("ui_port", 8801))
    selected_port = find_recoverable_port("127.0.0.1", configured_port)
    conflict = selected_port != configured_port
    status = service_status(project_dir, port=configured_port)
    commands = _commands(project_dir.name, configured_port, selected_port)
    payload = {
        "schema_version": SERVICE_CONTROL_SCHEMA,
        "project_id": project_dir.name,
        "configured_port": configured_port,
        "selected_port": selected_port,
        "port_conflict": conflict,
        "current_status": status,
        "commands": commands,
        "recovery": _recovery(conflict, configured_port, selected_port),
        "installer_contract": {
            "install_script": "packaging/windows_v5/Install-TargetCompassV5.ps1",
            "launch_script": "packaging/windows_v5/Launch-TargetCompassV5.ps1",
            "stop_script": "packaging/windows_v5/Stop-TargetCompassV5.ps1",
            "restart_script": "packaging/windows_v5/Restart-TargetCompassV5.ps1",
            "repair_script": "packaging/windows_v5/Repair-TargetCompassV5.ps1",
            "uninstall_script": "packaging/windows_v5/Uninstall-TargetCompassV5.ps1",
            "inno_setup_script": "packaging/windows_v5/TargetCompassV5.iss",
            "default_demo_project": "vascular_aging_demo",
        },
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "service_control_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def find_recoverable_port(host: str, preferred_port: int, *, attempts: int = 30) -> int:
    for port in range(preferred_port, preferred_port + attempts):
        if _port_available(host, port):
            return port
    raise OSError(f"No available port found from {preferred_port} to {preferred_port + attempts - 1}")


def _commands(project_id: str, configured_port: int, selected_port: int) -> dict[str, str]:
    return {
        "status": f"python tc_lite.py v5-service-control --project {project_id}",
        "start": f"python tc_lite.py serve --project {project_id} --host 127.0.0.1 --port {selected_port}",
        "stop_windows": f"powershell -NoProfile -ExecutionPolicy Bypass -File packaging/windows_v5/Stop-TargetCompassV5.ps1 -Port {configured_port}",
        "restart_windows": f"powershell -NoProfile -ExecutionPolicy Bypass -File packaging/windows_v5/Restart-TargetCompassV5.ps1 -Port {selected_port}",
        "doctor": f"python tc_lite.py v5-doctor --project {project_id}",
        "default_demo_init": f"python tc_lite.py v5-run-local --project {project_id} --question \"Are there SASP-high skeletal muscle background cells with characteristic surface markers in sarcopenia?\" --limit 1 --max-analysis-packets 2",
        "update_manifest": f"python tc_lite.py v5-service-control --project {project_id} && python tc_lite.py v5-doctor --project {project_id}",
        "uninstall_windows": "powershell -NoProfile -ExecutionPolicy Bypass -File packaging/windows_v5/Uninstall-TargetCompassV5.ps1",
    }


def _recovery(conflict: bool, configured_port: int, selected_port: int) -> list[dict[str, str]]:
    if not conflict:
        return [{"issue": "none", "action": "Configured port is available for a new service instance."}]
    return [
        {
            "issue": "port_conflict",
            "action": f"Use recovered port {selected_port}, or stop the process occupying {configured_port}.",
        },
        {
            "issue": "shortcut_update",
            "action": f"Update Launch-TargetCompassV5.ps1 shortcut argument from {configured_port} to {selected_port}.",
        },
    ]


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) != 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
