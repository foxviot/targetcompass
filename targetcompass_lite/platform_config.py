from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .secrets import apply_project_secrets, load_secrets, save_llm_provider, save_openai_api_key


PLATFORM_CONFIG_SCHEMA = "v5.platform_config/0.1"
SERVICE_STATUS_SCHEMA = "v5.service_status/0.1"
UPDATE_MANIFEST_SCHEMA = "v5.update_manifest/0.1"
PRE_RELEASE_SCRIPT_SCHEMA = "v5.pre_release_scripts/0.1"


def platform_config_path(project_dir: Path) -> Path:
    return project_dir / "v5" / "platform" / "platform_config.json"


def load_platform_config(project_dir: Path) -> dict[str, Any]:
    path = platform_config_path(project_dir)
    if not path.exists():
        return default_platform_config(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    return {**default_platform_config(project_dir), **data}


def default_platform_config(project_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": PLATFORM_CONFIG_SCHEMA,
        "project_id": project_dir.name,
        "ui_port": 8801,
        "llm": {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key_status": "not_set"},
        "docker": {"enabled": False, "compose_project": f"targetcompass_{project_dir.name}"},
        "r": {"rscript_path": shutil.which("Rscript") or ""},
        "nextflow": {"nextflow_path": shutil.which("nextflow") or "nextflow"},
        "updated_at": "",
    }


def save_platform_config(
    project_dir: Path,
    *,
    provider: str = "",
    base_url: str = "",
    model: str = "",
    api_key: str = "",
    ui_port: int | str = 8801,
    docker_enabled: bool = False,
    rscript_path: str = "",
    nextflow_path: str = "",
) -> dict[str, Any]:
    cfg = load_platform_config(project_dir)
    llm = dict(cfg.get("llm", {}))
    if provider or base_url or model:
        save_llm_provider(project_dir, provider or llm.get("provider", "deepseek"), base_url or llm.get("base_url", ""), model or llm.get("model", ""))
    if api_key.strip():
        save_openai_api_key(project_dir, api_key.strip())
    secrets = load_secrets(project_dir)
    llm.update(
        {
            "provider": provider or secrets.get("TARGETCOMPASS_LLM_PROVIDER", llm.get("provider", "deepseek")),
            "base_url": (base_url or secrets.get("TARGETCOMPASS_LLM_BASE_URL", llm.get("base_url", ""))).rstrip("/"),
            "model": model or secrets.get("TARGETCOMPASS_OPENAI_MODEL", llm.get("model", "deepseek-chat")),
            "api_key_status": "set" if (api_key.strip() or secrets.get("OPENAI_API_KEY")) else "not_set",
        }
    )
    cfg.update(
        {
            "schema_version": PLATFORM_CONFIG_SCHEMA,
            "project_id": project_dir.name,
            "ui_port": int(ui_port or cfg.get("ui_port", 8801)),
            "llm": llm,
            "docker": {"enabled": bool(docker_enabled), "compose_project": f"targetcompass_{project_dir.name}"},
            "r": {"rscript_path": rscript_path.strip() or cfg.get("r", {}).get("rscript_path", "")},
            "nextflow": {"nextflow_path": nextflow_path.strip() or cfg.get("nextflow", {}).get("nextflow_path", "nextflow")},
            "updated_at": _now(),
        }
    )
    path = platform_config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def platform_readiness(project_dir: Path) -> dict[str, Any]:
    apply_project_secrets(project_dir)
    cfg = load_platform_config(project_dir)
    rscript_command = _resolve_rscript_command(project_dir, cfg)
    nextflow_command = _resolve_nextflow_command(project_dir, cfg)
    checks = [
        _check("llm_api_key", _llm_key_configured(project_dir, cfg), "LLM API key configured.", "Add DeepSeek/OpenAI-compatible key."),
        _check("ui_port", _ui_port_ready(int(cfg.get("ui_port", 8801))), f"Port {cfg.get('ui_port')} is available or serving TargetCompass.", "Choose another UI port or stop the existing service.", severity="warn"),
        _check("docker_cli", bool(shutil.which("docker")), "Docker CLI found.", "Install Docker Desktop for PostgreSQL/MinIO backend.", severity="warn"),
        _check("docker_daemon", _docker_daemon_running(), "Docker daemon running.", "Start Docker Desktop before activating backends.", severity="warn"),
        _check("rscript", _command_exists(rscript_command), f"Rscript available: {rscript_command}", "Install R or set Rscript path.", severity="warn"),
        _check("nextflow", _command_exists(nextflow_command), f"Nextflow available: {nextflow_command}", "Install Nextflow or set path.", severity="warn"),
    ]
    failed = [row for row in checks if row["status"] == "FAIL"]
    warnings = [row for row in checks if row["status"] == "WARN"]
    payload = {
        "schema_version": "v5.platform_readiness/0.1",
        "project_id": project_dir.name,
        "status": "FAIL" if failed else "WARN" if warnings else "PASS",
        "config_ref": _rel(platform_config_path(project_dir), project_dir),
        "checks": checks,
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "platform_readiness.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def build_post_install_setup_wizard(project_dir: Path) -> dict[str, Any]:
    cfg = load_platform_config(project_dir)
    readiness = platform_readiness(project_dir)
    backend_status = _read_json(project_dir / "v5" / "platform" / "backend_primary_status.json", {})
    service = service_status(project_dir)
    steps = [
        {
            "step_id": "llm_key",
            "label": "LLM key",
            "status": _check_status(readiness, "llm_api_key"),
            "field_names": ["llm_provider", "llm_base_url", "llm_model", "openai_api_key"],
            "help": "Use a DeepSeek/OpenAI-compatible key for real role execution; leave blank to keep an existing saved key.",
        },
        {
            "step_id": "ports",
            "label": "UI port",
            "status": _check_status(readiness, "ui_port"),
            "field_names": ["ui_port"],
            "help": "Choose the local web port used after installation.",
        },
        {
            "step_id": "runtime_paths",
            "label": "Docker / R / Nextflow paths",
            "status": _rollup_status([_check_status(readiness, "docker_daemon"), _check_status(readiness, "rscript"), _check_status(readiness, "nextflow")]),
            "field_names": ["docker_enabled", "rscript_path", "nextflow_path"],
            "help": "Configure local dependencies for PostgreSQL/MinIO, DEG/scRNA modules, and workflow execution.",
        },
        {
            "step_id": "backend_status",
            "label": "Backend status",
            "status": "PASS" if backend_status.get("overall_status") == "PRIMARY_READY" else "WARN",
            "field_names": [],
            "help": "PostgreSQL/MinIO should be active for platform-mode delivery; local filesystem remains a fallback.",
        },
    ]
    payload = {
        "schema_version": "v5.post_install_setup_wizard/0.1",
        "project_id": project_dir.name,
        "status": _rollup_status([row["status"] for row in steps]),
        "config_ref": _rel(platform_config_path(project_dir), project_dir),
        "readiness_ref": "v5/platform/platform_readiness.json",
        "service_status_ref": "v5/platform/service_status.json",
        "backend_status_ref": "v5/platform/backend_primary_status.json" if backend_status else "",
        "current_config": _redact_config(cfg),
        "service": service,
        "steps": steps,
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "post_install_setup_wizard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def write_pre_release_scripts(project_dir: Path, *, question_count: int = 50) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    scripts_dir = root / "packaging" / "windows_v5"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    ps1 = scripts_dir / "run_v5_pre_release_acceptance.ps1"
    bat = scripts_dir / "run_v5_pre_release_acceptance.bat"
    ps1_text = f"""param(
  [string]$Project = "{project_dir.name}",
  [int]$QuestionCount = {question_count}
)
$ErrorActionPreference = "Stop"
Set-Location "{root}"
python tc_lite.py v5-doctor --project $Project
python tc_lite.py test-suite --suite quick --project $Project
python tc_lite.py test-suite --suite full --project $Project
python tc_lite.py test-suite --suite e2e --project $Project
python tc_lite.py v5-real-question-validation --project $Project --question-count $QuestionCount --isolated-projects
python tc_lite.py v5-release-acceptance --project $Project --question-count $QuestionCount
python tc_lite.py v5-production-acceptance --project $Project --target all
"""
    bat_text = f"""@echo off
cd /d "{root}"
powershell -ExecutionPolicy Bypass -File "{ps1}" -Project %1
"""
    ps1.write_text(ps1_text, encoding="utf-8")
    bat.write_text(bat_text, encoding="utf-8")
    payload = {
        "schema_version": PRE_RELEASE_SCRIPT_SCHEMA,
        "project_id": project_dir.name,
        "question_count": question_count,
        "scripts": {
            "powershell": str(ps1).replace("\\", "/"),
            "cmd": str(bat).replace("\\", "/"),
        },
        "commands": [
            f"powershell -ExecutionPolicy Bypass -File {ps1} -Project {project_dir.name} -QuestionCount {question_count}",
            f"python tc_lite.py v5-release-acceptance --project {project_dir.name} --question-count {question_count}",
        ],
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "pre_release_scripts.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def service_status(project_dir: Path, port: int | None = None) -> dict[str, Any]:
    cfg = load_platform_config(project_dir)
    port = int(port or cfg.get("ui_port", 8801))
    running = not _port_available("127.0.0.1", port)
    logs = project_dir.parent.parent / "logs"
    payload = {
        "schema_version": SERVICE_STATUS_SCHEMA,
        "project_id": project_dir.name,
        "ui": {"port": port, "url": f"http://127.0.0.1:{port}/", "running": running},
        "logs": {
            "install_logs": str(logs).replace("\\", "/"),
            "project_run_status": _rel(project_dir / "results" / "run_status.json", project_dir),
        },
        "health": "RUNNING" if running else "STOPPED",
        "recovery": [] if not running else ["If the browser does not open, manually visit the UI URL."],
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "service_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def write_update_manifest(project_dir: Path, version: str = "0.5.0-local") -> dict[str, Any]:
    dist = Path(__file__).resolve().parents[1] / "dist"
    packages = sorted(dist.glob("TargetCompassV5_Windows_Installer_*.zip"), key=lambda p: p.stat().st_mtime) if dist.exists() else []
    payload = {
        "schema_version": UPDATE_MANIFEST_SCHEMA,
        "project_id": project_dir.name,
        "current_version": version,
        "latest_package": str(packages[-1]).replace("\\", "/") if packages else "",
        "package_count": len(packages),
        "policy": {
            "preserve_user_projects": True,
            "backup_before_update": True,
            "upgrade_mode": "install new package over app runtime, keep projects unless user chooses reset",
        },
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "update_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _check(check_id: str, ok: bool, message: str, remediation: str, *, severity: str = "fail") -> dict[str, str]:
    status = "PASS" if ok else ("WARN" if severity == "warn" else "FAIL")
    return {"check_id": check_id, "status": status, "message": message if ok else remediation, "remediation": "" if ok else remediation}


def _command_exists(command: str) -> bool:
    if not command:
        return False
    if Path(command).exists():
        return True
    return shutil.which(command) is not None


def _llm_key_configured(project_dir: Path, cfg: dict[str, Any]) -> bool:
    if os.environ.get("OPENAI_API_KEY"):
        return True
    if load_secrets(project_dir).get("OPENAI_API_KEY"):
        return True
    return cfg.get("llm", {}).get("api_key_status") == "set"


def _ui_port_ready(port: int) -> bool:
    if _port_available("127.0.0.1", port):
        return True
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def _resolve_rscript_command(project_dir: Path, cfg: dict[str, Any]) -> str:
    configured = str(cfg.get("r", {}).get("rscript_path") or "").strip()
    if configured:
        return configured
    found = shutil.which("Rscript")
    if found:
        return found
    for root in [Path("C:/Program Files/R"), project_dir / "tools" / "R"]:
        if root.exists():
            candidates = sorted(root.glob("R-*/bin/Rscript.exe"), reverse=True)
            if candidates:
                return str(candidates[0])
    return "Rscript"


def _resolve_nextflow_command(project_dir: Path, cfg: dict[str, Any]) -> str:
    configured = str(cfg.get("nextflow", {}).get("nextflow_path") or "").strip()
    if configured and configured != "nextflow":
        return configured
    found = shutil.which("nextflow")
    if found:
        return found
    for name in ["nextflow", "nextflow.bat"]:
        local = project_dir / "tools" / "nextflow" / name
        if local.exists():
            return str(local)
    return configured or "nextflow"


def _docker_daemon_running() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=8)
        return result.returncode == 0
    except Exception:
        return False


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) != 0


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _check_status(readiness: dict[str, Any], check_id: str) -> str:
    for row in readiness.get("checks", []):
        if row.get("check_id") == check_id:
            return row.get("status", "WARN")
    return "WARN"


def _rollup_status(statuses: list[str]) -> str:
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status == "WARN" for status in statuses):
        return "WARN"
    return "PASS"


def _redact_config(cfg: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(cfg))
    redacted.get("llm", {}).pop("api_key", None)
    return redacted
