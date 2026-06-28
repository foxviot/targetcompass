import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm_gateway import execute_llm_task_packet
from .nextflow_plane import build_nextflow_execution_plane, validate_nextflow_execution_plane
from .nextflow_runner import build_nextflow_tasks, run_nextflow_local, run_nextflow_smoke
from .nextflow_bootstrap import bootstrap_nextflow, resolve_nextflow_bin
from .service_deployment import build_service_deployment
from .services import SERVICE_ENDPOINTS
from .v4 import content_hash, v4_dir


LOCAL_DELIVERY_SCHEMA = "v4.local_delivery_verification/0.1"


def prepare_local_v4_delivery(project_dir: Path, host: str = "127.0.0.1", base_port: int = 8810) -> dict[str, Any]:
    deployment = build_service_deployment(project_dir, host=host, base_port=base_port)
    _write_root_launcher(project_dir, deployment, host)
    plane = build_nextflow_execution_plane(project_dir)
    tasks = build_nextflow_tasks(project_dir)
    report = {
        "schema_version": LOCAL_DELIVERY_SCHEMA,
        "project_id": project_dir.name,
        "mode": "prepare",
        "generated_at": _now(),
        "service_deployment": "v4/service_deployment.json",
        "service_launcher": "scripts/start_v4_services.ps1",
        "root_launcher": "一键启动_v4本地服务.ps1",
        "nextflow_plane": plane.get("entrypoint", ""),
        "nextflow_tasks": "workflows/target_discovery/tasks.json",
        "task_count": tasks.get("task_count", 0),
        "next_steps": [
            "Run scripts/start_v4_services.ps1 or double-click the root launcher.",
            "Run python tc_lite.py local-v4-verify --project <project> --deepseek-test --nextflow-run.",
        ],
    }
    return _write_report(project_dir, report)


def verify_local_v4_delivery(
    project_dir: Path,
    host: str = "127.0.0.1",
    base_port: int = 8810,
    deepseek_test: bool = False,
    nextflow_run: bool = False,
    nextflow_analysis_run: bool = False,
    start_services: bool = False,
    wait_seconds: int = 10,
) -> dict[str, Any]:
    deployment = build_service_deployment(project_dir, host=host, base_port=base_port)
    _write_root_launcher(project_dir, deployment, host)
    service_processes = []
    try:
        if start_services:
            service_processes = _start_services(project_dir, deployment)
            time.sleep(max(1, wait_seconds))
        service_checks = _check_services(deployment)
        llm_result = _run_deepseek_agent_check(project_dir) if deepseek_test else _skipped("DeepSeek test not requested.")
        if nextflow_analysis_run:
            nextflow_result = _run_nextflow_check(project_dir)
        elif nextflow_run:
            nextflow_result = _run_nextflow_smoke_check(project_dir)
        else:
            nextflow_result = _nextflow_prepare_check(project_dir)
        status = _overall_status(service_checks, llm_result, nextflow_result)
        report = {
            "schema_version": LOCAL_DELIVERY_SCHEMA,
            "project_id": project_dir.name,
            "mode": "verify",
            "status": status,
            "generated_at": _now(),
            "service_deployment": "v4/service_deployment.json",
            "service_launcher": "scripts/start_v4_services.ps1",
            "root_launcher": "一键启动_v4本地服务.ps1",
            "services": service_checks,
            "deepseek_agent": llm_result,
            "nextflow": nextflow_result,
            "summary": _summary(status, service_checks, llm_result, nextflow_result),
        }
        return _write_report(project_dir, report)
    finally:
        if start_services:
            _stop_services(service_processes)


def local_delivery_report_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "local_v4_delivery_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_root_launcher(project_dir: Path, deployment: dict[str, Any], host: str) -> None:
    root_launcher = project_dir / "一键启动_v4本地服务.ps1"
    script = project_dir / "scripts" / "start_v4_services.ps1"
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"Set-Location -LiteralPath '{Path.cwd()}'",
        f"& '{script}'",
        "Start-Sleep -Seconds 3",
        f"Start-Process 'http://{host}:{deployment.get('services', [{}])[0].get('port', 8810)}/health'",
        "Write-Host 'TargetCompass v4 local services requested.'",
        "Write-Host 'Main UI: run {0} tc_lite.py serve --project {1} --port 8781'".format(sys.executable, project_dir.name),
        "Write-Host 'Service deployment: projects/{0}/v4/service_deployment.json'".format(project_dir.name),
    ]
    root_launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _start_services(project_dir: Path, deployment: dict[str, Any]) -> list[subprocess.Popen]:
    processes = []
    for service in deployment.get("services", []):
        command = [
            sys.executable,
            "tc_lite.py",
            "service-run",
            "--project",
            project_dir.name,
            "--service-id",
            service["service_id"],
            "--host",
            service["host"],
            "--port",
            str(service["port"]),
        ]
        processes.append(subprocess.Popen(command, cwd=Path.cwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    return processes


def _stop_services(processes: list[subprocess.Popen]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 5
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()


def _check_services(deployment: dict[str, Any]) -> dict[str, Any]:
    checks = []
    for service in deployment.get("services", []):
        health_url = service.get("health_url", "")
        status = "PASS" if _http_ok(health_url) else "REVIEW"
        checks.append(
            {
                "service_id": service.get("service_id", ""),
                "status": status,
                "health_url": health_url,
                "port": service.get("port", ""),
                "endpoints": service.get("endpoints", []),
                "suggested_action": "" if status == "PASS" else "Start local services with scripts/start_v4_services.ps1 or rerun with --start-services.",
            }
        )
    return {
        "status": "PASS" if checks and all(row["status"] == "PASS" for row in checks) else "REVIEW",
        "service_count": len(checks),
        "pass_count": len([row for row in checks if row["status"] == "PASS"]),
        "items": checks,
    }


def _run_deepseek_agent_check(project_dir: Path) -> dict[str, Any]:
    _apply_deepseek_defaults()
    if not os.environ.get("OPENAI_API_KEY"):
        return {
            "status": "REVIEW",
            "reason": "OPENAI_API_KEY is not set. Configure the DeepSeek key in UI or environment.",
            "artifacts": {},
        }
    prompt = (
        "Return a minimal result_reviewer JSON object. Review the current TargetCompass v4 local delivery run. "
        "Do not invent data. Mention that this is a local delivery verification."
    )
    output = execute_llm_task_packet(
        project_dir,
        role_id="result_reviewer",
        prompt=prompt,
        input_refs={
            "system_status": "python tc_lite.py system-status --project " + project_dir.name,
            "evidence_db": "evidence.sqlite",
            "report": "reports/target_report.html",
        },
        model=os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat"),
        purpose="Verify real DeepSeek-compatible LLM role execution for local v4 delivery.",
        actor="local_v4_delivery",
    )
    return {
        "status": "PASS" if output.get("status") == "executed" else "REVIEW",
        "execution_status": output.get("status", ""),
        "schema_validation": output.get("schema_validation", {}),
        "artifacts": output.get("artifacts", {}),
        "failure_reason": output.get("failure_reason", ""),
    }


def _nextflow_prepare_check(project_dir: Path) -> dict[str, Any]:
    plane = build_nextflow_execution_plane(project_dir)
    validation = validate_nextflow_execution_plane(project_dir)
    tasks = build_nextflow_tasks(project_dir)
    bootstrap = bootstrap_nextflow(project_dir, download=False)
    executable = resolve_nextflow_bin(project_dir) if bootstrap.get("status") == "ready" else ""
    return {
        "status": "PASS" if validation.get("status") == "pass" else "REVIEW",
        "mode": "prepared_not_run",
        "nextflow_executable": executable or "",
        "bootstrap_status": bootstrap.get("status", ""),
        "bootstrap": "workflows/target_discovery/nextflow_bootstrap.json",
        "plane_status": validation.get("status", ""),
        "task_count": tasks.get("task_count", 0),
        "artifacts": {
            "plane": "workflows/target_discovery/nextflow_execution_plane.json",
            "tasks": "workflows/target_discovery/tasks.json",
            "validation": "workflows/target_discovery/nextflow_validation.json",
        },
        "suggested_action": " ".join(bootstrap.get("recovery", [])) if bootstrap.get("status") != "ready" else "Rerun with --nextflow-run to execute local profile.",
    }


def _run_nextflow_check(project_dir: Path) -> dict[str, Any]:
    bootstrap = bootstrap_nextflow(project_dir, download=False)
    nextflow_bin = resolve_nextflow_bin(project_dir)
    manifest = run_nextflow_local(project_dir, profile="local", nextflow_bin=nextflow_bin, resume=False)
    return {
        "status": "PASS" if manifest.get("status") == "success" else "REVIEW",
        "mode": "local_run",
        "bootstrap_status": bootstrap.get("status", ""),
        "run_status": manifest.get("status", ""),
        "returncode": manifest.get("returncode", ""),
        "failure_reason": manifest.get("failure_reason", ""),
        "recovery": manifest.get("recovery", {}),
        "artifacts": {
            "manifest": "workflows/target_discovery/nextflow_run_manifest.json",
            "run_dir": manifest.get("run_dir", ""),
            "run_artifacts": manifest.get("artifacts", []),
        },
    }


def _run_nextflow_smoke_check(project_dir: Path) -> dict[str, Any]:
    bootstrap = bootstrap_nextflow(project_dir, download=False)
    nextflow_bin = resolve_nextflow_bin(project_dir)
    smoke = run_nextflow_smoke(project_dir, nextflow_bin=nextflow_bin)
    return {
        "status": "PASS" if smoke.get("status") == "success" else "REVIEW",
        "mode": "engine_smoke",
        "bootstrap_status": bootstrap.get("status", ""),
        "run_status": smoke.get("status", ""),
        "returncode": smoke.get("returncode", ""),
        "failure_reason": smoke.get("failure_reason", ""),
        "artifacts": {
            "manifest": "workflows/target_discovery/smoke/nextflow_smoke_manifest.json",
            "smoke_workflow": "workflows/target_discovery/smoke/smoke.nf",
        },
        "suggested_action": " ".join(bootstrap.get("recovery", [])) if smoke.get("status") != "success" else "",
    }


def _apply_deepseek_defaults() -> None:
    os.environ.setdefault("TARGETCOMPASS_LLM_PROVIDER", "deepseek")
    os.environ.setdefault("TARGETCOMPASS_LLM_BASE_URL", "https://api.deepseek.com")
    os.environ.setdefault("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat")


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _overall_status(service_checks: dict[str, Any], llm_result: dict[str, Any], nextflow_result: dict[str, Any]) -> str:
    statuses = [service_checks.get("status"), llm_result.get("status"), nextflow_result.get("status")]
    if all(status == "PASS" for status in statuses):
        return "PASS"
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    return "REVIEW"


def _summary(status: str, service_checks: dict[str, Any], llm_result: dict[str, Any], nextflow_result: dict[str, Any]) -> list[str]:
    return [
        f"Overall status: {status}",
        f"Services: {service_checks.get('pass_count', 0)}/{service_checks.get('service_count', 0)} health checks passed.",
        f"DeepSeek Agent: {llm_result.get('status')} ({llm_result.get('execution_status', llm_result.get('reason', ''))}).",
        f"Nextflow: {nextflow_result.get('status')} ({nextflow_result.get('mode', '')}; {nextflow_result.get('run_status', nextflow_result.get('plane_status', ''))}).",
    ]


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "SKIPPED", "reason": reason}


def _write_report(project_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    report["report_hash"] = content_hash(report)
    path = local_delivery_report_path(project_dir)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["path"] = str(path.relative_to(project_dir)).replace("\\", "/")
    return report


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
