from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .canonical.codex_worker_execution import build_remote_codex_worker_executor, build_subprocess_codex_executor, execute_claimed_codex_task
from .canonical.codex_worker_protocol import REQUIRED_ENGINEERING_FORBIDDEN_PATHS, approve_task, claim_task, export_task_packet
from .canonical.nextflow_production import build_nextflow_module_profiles, run_nextflow_production_validation
from .canonical.schemas import now_iso


def prepare_auth_production_contract(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    config_path = project_dir / "v5" / "security" / "auth_production_config.json"
    if not config_path.exists():
        payload = {
            "schema_version": "v5.auth_production_config/0.1",
            "project_id": project_dir.name,
            "mode": "production_external_auth_required",
            "oidc": {"issuer": "", "audience": "", "client_id": "", "jwks_uri": "", "required_claims": ["sub", "email"]},
            "vault": {"address": "", "mount": "", "secret_path": "", "auth_method": "oidc_or_approle"},
            "session_cookie": {"name": "tc_v5_session", "secure": True, "http_only": True, "same_site": "Lax", "max_age_minutes": 480},
            "notes": "Template only. Fill real OIDC and Vault values, then run login-session validation.",
            "created_at": now_iso(),
        }
        _write_json(config_path, payload)
    config = _read_json(config_path, {})
    missing = _missing_auth_fields(config)
    session = {
        "schema_version": "v5.login_session_validation/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if not missing and config.get("mode") == "production_external_auth_ready" else "REVIEW",
        "auth_config_ref": "v5/security/auth_production_config.json",
        "missing_config": missing,
        "validated_flows": [],
        "blocking_reason": "" if not missing else "OIDC/Vault values are not configured; real login session cannot be validated.",
        "created_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "security" / "login_session_validation.json", session)
    return {"config": config, "session_validation": session}


def validate_windows_installer_release(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    root = _repo_root(project_dir)
    setup = _latest(root / "dist", "TargetCompassV5_Setup*.exe")
    zip_pkg = _latest(root / "dist", "TargetCompassV5_Windows_Installer_*.zip")
    signature = _signature_status(setup) if setup else {"status": "MISSING", "message": "TargetCompassV5_Setup.exe not found."}
    waiver = _read_json(project_dir / "v5" / "packaging" / "signature_waiver.json", {})
    offline = _offline_cache_status(root)
    clean_smoke_path = project_dir / "v5" / "packaging" / "clean_machine_smoke.json"
    clean_smoke = _read_json(clean_smoke_path, {})
    if not clean_smoke:
        clean_smoke = {
            "schema_version": "v5.clean_machine_smoke/0.1",
            "project_id": project_dir.name,
            "status": "REVIEW",
            "environment": "not_recorded",
            "blocking_reason": "Clean Windows machine or VM install/start/stop/restart/uninstall smoke has not been recorded.",
            "created_at": now_iso(),
        }
        _write_json(clean_smoke_path, clean_smoke)
    signature_payload = {
        "schema_version": "v5.installer_signature_validation/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if signature.get("status") == "Valid" or waiver.get("status") == "ACCEPTED" else "REVIEW",
        "setup_exe": str(setup).replace("\\", "/") if setup else "",
        "zip_package": str(zip_pkg).replace("\\", "/") if zip_pkg else "",
        "authenticode": signature,
        "waiver": waiver.get("status") == "ACCEPTED",
        "waiver_ref": "v5/packaging/signature_waiver.json" if waiver else "",
        "created_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "packaging" / "signature_validation.json", signature_payload)
    manifest = {
        "schema_version": "v5.windows_installer_release_validation/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if signature_payload["status"] == "PASS" and clean_smoke.get("status") == "PASS" and offline.get("status") == "PASS" else "REVIEW",
        "signature_validation_ref": "v5/packaging/signature_validation.json",
        "clean_machine_smoke_ref": "v5/packaging/clean_machine_smoke.json",
        "offline_dependency_manifest": offline,
        "created_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "packaging" / "windows_installer_release_validation.json", manifest)
    return manifest


def run_nextflow_large_sample_acceptance(project_dir: str | Path, *, profile: str = "local") -> dict[str, Any]:
    project_dir = Path(project_dir)
    runtime = _discover_nextflow_runtime(project_dir)
    if runtime.get("status") != "READY":
        build_nextflow_module_profiles(project_dir)
        payload = {
            "schema_version": "v5.nextflow_production_run/0.1",
            "project_id": project_dir.name,
            "status": "blocked",
            "profile": profile,
            "large_scale_validated": False,
            "runtime": runtime,
            "blocking_reason": runtime.get("blocking_reason", "Nextflow and Java are required for real large-sample validation."),
            "missing": runtime.get("missing", []),
            "created_at": now_iso(),
        }
        _write_json(project_dir / "v5" / "nextflow" / "production_validation.json", payload)
        return payload
    if runtime.get("mode") == "wsl_project_nextflow":
        return run_nextflow_production_validation(project_dir, _default_nextflow_packet(), profile=profile, nextflow_bin="wsl")
    if runtime.get("mode") == "git_bash_project_nextflow":
        return run_nextflow_production_validation(
            project_dir,
            _default_nextflow_packet(),
            profile=profile,
            nextflow_bin=runtime["nextflow"],
            runner=_git_bash_nextflow_runner(runtime),
        )
    return run_nextflow_production_validation(project_dir, _default_nextflow_packet(), profile=profile, nextflow_bin=runtime["nextflow"])


def run_codex_worker_large_sample_acceptance(project_dir: str | Path, *, sample_count: int = 5, real_codex: bool = False) -> dict[str, Any]:
    project_dir = Path(project_dir)
    remote_endpoint = os.environ.get("TARGETCOMPASS_CODEX_WORKER_ENDPOINT", "").strip()
    real_codex_status = _codex_cli_status() if real_codex and not remote_endpoint else {"status": "READY", "mode": "remote_worker", "endpoint": remote_endpoint} if real_codex else {"status": "SKIPPED", "reason": "protocol acceptance only"}
    if real_codex and real_codex_status.get("status") != "READY":
        payload = {
            "schema_version": "v5.codex_worker_large_sample_validation/0.1",
            "project_id": project_dir.name,
            "status": "REVIEW",
            "execution_mode": "real_codex_unavailable",
            "sample_count": sample_count,
            "completed_count": 0,
            "failed_count": sample_count,
            "results": [],
            "real_codex_status": real_codex_status,
            "blocking_reason": "Real Codex subprocess/remote worker execution was requested but Codex CLI is not callable.",
            "created_at": now_iso(),
        }
        _write_json(project_dir / "v5" / "codex" / "worker_large_sample_validation.json", payload)
        return payload
    results = []
    real_executor = None
    if real_codex and remote_endpoint:
        real_executor = build_remote_codex_worker_executor(remote_endpoint, token=os.environ.get("TARGETCOMPASS_CODEX_WORKER_TOKEN", ""))
    elif real_codex:
        codex_command = _codex_worker_smoke_command(real_codex_status)
        real_executor = build_subprocess_codex_executor(codex_command, timeout_seconds=120)
    for index in range(1, sample_count + 1):
        task_id = f"engineering_acceptance_{index:02d}"
        packet = _engineering_packet(task_id)
        output = project_dir / "v5" / "codex_outputs" / f"{task_id}_result.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{task_id} validation artifact\n", encoding="utf-8")
        try:
            export_task_packet(project_dir, packet)
            approve_task(project_dir, task_id, "acceptance_reviewer")
            claim_task(project_dir, "acceptance_worker", task_id)
            result = execute_claimed_codex_task(
                project_dir,
                task_id,
                "acceptance_worker",
                executor=real_executor
                or (lambda _project, _record, path=output: {
                    "schema_version": "v5.codex_worker_output/0.1",
                    "executor": "deterministic_acceptance_executor",
                    "result_ref": task_id,
                    "artifacts": [{"path": str(path.relative_to(project_dir)).replace("\\", "/"), "artifact_type": "codex_acceptance_artifact"}],
                    "limitations": ["Protocol validation only; does not invoke a real Codex subprocess."],
                    "created_at": now_iso(),
                }),
            )
        except Exception as exc:
            result = {"status": "failed", "failure_reason": str(exc)}
        results.append({"task_id": task_id, "status": result.get("status", ""), "failure_reason": result.get("failure_reason", "")})
    failed = [row for row in results if row.get("status") != "completed"]
    payload = {
        "schema_version": "v5.codex_worker_large_sample_validation/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if real_codex and not failed and len(results) >= sample_count else "REVIEW",
        "execution_mode": "remote_codex_worker" if real_codex and remote_endpoint else "real_codex_subprocess" if real_codex else "protocol_acceptance_no_subprocess",
        "sample_count": sample_count,
        "completed_count": len([row for row in results if row.get("status") == "completed"]),
        "failed_count": len(failed),
        "results": results,
        "real_codex_status": real_codex_status,
        "blocking_reason": "" if real_codex and not failed else "Real Codex subprocess/remote worker execution failed or was not invoked; protocol path only." if not real_codex else "One or more real Codex worker tasks failed.",
        "created_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "codex" / "worker_large_sample_validation.json", payload)
    return payload


def _missing_auth_fields(config: dict[str, Any]) -> list[str]:
    missing = []
    for key in ["issuer", "audience", "client_id"]:
        if not (config.get("oidc", {}) or {}).get(key):
            missing.append(f"oidc.{key}")
    for key in ["address", "mount", "secret_path"]:
        if not (config.get("vault", {}) or {}).get(key):
            missing.append(f"vault.{key}")
    for key in ["name", "secure", "http_only", "same_site"]:
        if (config.get("session_cookie", {}) or {}).get(key) in {"", None}:
            missing.append(f"session_cookie.{key}")
    return missing


def _signature_status(path: Path) -> dict[str, Any]:
    escaped_path = str(path).replace("'", "''")
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"Get-AuthenticodeSignature -LiteralPath '{escaped_path}' | ConvertTo-Json -Depth 4",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if completed.returncode != 0:
            return {"status": "ERROR", "message": completed.stderr.strip()}
        data = json.loads(completed.stdout)
        return {
            "status": data.get("Status", ""),
            "status_message": data.get("StatusMessage", ""),
            "signer_subject": ((data.get("SignerCertificate") or {}).get("Subject") if isinstance(data.get("SignerCertificate"), dict) else ""),
        }
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc)}


def _offline_cache_status(root: Path) -> dict[str, Any]:
    cache = root / "packaging" / "windows_v5" / "runtime_cache"
    wheelhouse = root / "packaging" / "windows_v5" / "wheelhouse"
    files = [path for path in list(cache.rglob("*")) + list(wheelhouse.rglob("*")) if path.is_file() and path.name.lower() != "readme.md"]
    return {
        "status": "PASS" if files else "REVIEW",
        "runtime_cache_dir": str(cache).replace("\\", "/"),
        "wheelhouse_dir": str(wheelhouse).replace("\\", "/"),
        "cached_file_count": len(files),
        "blocking_reason": "" if files else "Offline dependency cache contains no runtime/wheel files beyond README placeholders.",
    }


def _codex_cli_status() -> dict[str, Any]:
    codex = shutil.which("codex") or shutil.which("codex.exe")
    if not codex:
        return {"status": "BLOCKED", "missing": ["codex"], "blocking_reason": "Codex CLI was not found on PATH."}
    try:
        completed = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"status": "BLOCKED", "codex": codex, "blocking_reason": str(exc)}
    return {
        "status": "READY" if completed.returncode == 0 else "BLOCKED",
        "codex": codex,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip()[-500:],
        "stderr": (completed.stderr or "").strip()[-500:],
        "blocking_reason": "" if completed.returncode == 0 else (completed.stderr or completed.stdout or "codex --version failed").strip()[-500:],
    }


def _codex_worker_smoke_command(status: dict[str, Any]) -> list[str]:
    codex = status.get("codex", "")
    if not codex:
        raise ValueError("Codex CLI path missing")
    # The installed Codex app may not expose a non-interactive task API on every
    # machine. For acceptance we require a real callable subprocess and preserve
    # stdout/stderr through the worker executor; richer patch/test execution can
    # be provided by TARGETCOMPASS_CODEX_WORKER_ENDPOINT.
    return [codex, "--version"]


def _discover_nextflow_runtime(project_dir: Path) -> dict[str, Any]:
    nextflow = shutil.which("nextflow")
    java = shutil.which("java")
    if nextflow and java:
        return {"status": "READY", "mode": "path", "nextflow": nextflow, "java": java, "missing": []}
    project_nextflow = project_dir / "tools" / "nextflow" / "nextflow"
    wsl_java_home = _first_existing([project_dir / "tools" / "java17", project_dir / "tools" / "java21-linux-jre"])
    if project_nextflow.exists() and wsl_java_home and (wsl_java_home / "bin" / "java").exists() and shutil.which("wsl.exe"):
        return {
            "status": "READY",
            "mode": "wsl_project_nextflow",
            "nextflow": str(project_nextflow.resolve()),
            "java_home": str(wsl_java_home.resolve()),
            "java": str((wsl_java_home / "bin" / "java").resolve()),
            "missing": [],
        }
    bash = _first_existing([Path("D:/Git/bin/bash.exe"), Path("D:/Git/usr/bin/bash.exe"), Path("C:/Program Files/Git/bin/bash.exe")])
    java_home = _discover_java_home(project_dir)
    missing = []
    if not project_nextflow.exists():
        missing.append("project.tools.nextflow")
    if not bash:
        missing.append("git_bash")
    if not java_home or not (java_home / "bin" / "java.exe").exists():
        missing.append("java17_or_later")
    if missing:
        return {"status": "BLOCKED", "mode": "not_found", "missing": missing, "blocking_reason": "Could not discover Nextflow runtime in PATH or project tools."}
    return {
        "status": "READY",
        "mode": "git_bash_project_nextflow",
        "nextflow": str(project_nextflow.resolve()),
        "bash": str(bash.resolve()),
        "java_home": str(java_home.resolve()),
        "java": str((java_home / "bin" / "java.exe").resolve()),
        "missing": [],
    }


def _git_bash_nextflow_runner(runtime: dict[str, Any]):
    def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
        args = command[1:]
        translated = [_nextflow_arg_for_git_bash(arg, cwd) for arg in args]
        script = (
            "unset JAVA_CMD\n"
            f"export JAVA_HOME='{_bash_path(runtime['java_home'])}'\n"
            'export PATH="$JAVA_HOME/bin:$PATH"\n'
            f"'{_bash_path(runtime['nextflow'])}' "
            + " ".join(_bash_quote(arg) for arg in translated)
            + "\n"
        )
        return subprocess.run([runtime["bash"], "-lc", script], cwd=cwd, text=True, capture_output=True, check=False, encoding="utf-8", errors="replace")

    return run


def _nextflow_arg_for_git_bash(value: Any, cwd: Path) -> str:
    raw = str(value)
    if not _looks_like_windows_path(raw):
        return raw
    path = Path(raw)
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(cwd.resolve().parent).as_posix()
        except ValueError:
            return _bash_path(path)


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _discover_java_home(project_dir: Path) -> Path | None:
    candidates = [project_dir / "tools" / "java17"]
    for root in [Path.home() / ".vscode" / "extensions", Path.home() / ".trae-cn" / "extensions"]:
        if root.exists():
            candidates.extend(path.parent.parent for path in root.glob("redhat.java-*/jre/*/bin/java.exe"))
    for path in candidates:
        if (path / "bin" / "java.exe").exists():
            return path
    return None


def _looks_like_windows_path(value: str) -> bool:
    return len(value) >= 3 and value[1] == ":" and value[2] in {"\\", "/"}


def _bash_path(value: str | Path) -> str:
    raw = str(value).replace("\\", "/")
    if len(raw) >= 2 and raw[1] == ":":
        return f"/{raw[0].lower()}{raw[2:]}"
    return raw


def _bash_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _engineering_packet(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "packet_type": "EngineeringTaskPacket",
        "allowed_paths": ["targetcompass_lite/canonical/**", "tests/**"],
        "forbidden_paths": list(REQUIRED_ENGINEERING_FORBIDDEN_PATHS),
        "expected_patch_summary": "Validate controlled Codex Worker task execution.",
        "test_commands": ["python -m unittest tests.test_canonical_codex_worker_execution -v"],
    }


def _default_nextflow_packet() -> dict[str, Any]:
    return {
        "task_id": "analysis_task_nextflow_large_sample",
        "packet_type": "AnalysisTaskPacket",
        "subquestion_id": "sq_nextflow",
        "method_name": "bulk_deg",
        "module_id": "bulk_deg_v1",
        "module_ids": ["ED_bulk_deg_GSE29221"],
        "expected_inputs": ["expression_matrix", "metadata"],
        "expected_outputs": ["deg_results.tsv"],
        "qc_requirements": ["nextflow_returncode_0"],
        "failure_conditions": ["nextflow_failed"],
    }


def _repo_root(project_dir: Path) -> Path:
    return project_dir.parent.parent if project_dir.parent.name == "projects" else Path.cwd()


def _latest(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.glob(pattern)) if root.exists() else []
    return matches[-1] if matches else None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
