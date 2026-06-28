import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "v5.package_acceptance/0.1"


def run_acceptance(
    *,
    suite: str = "quick",
    build_bundle: bool = True,
    build_installer: bool = True,
    validate_zip: bool = True,
    timeout: int = 600,
) -> dict[str, Any]:
    started = time.monotonic()
    steps: list[dict[str, Any]] = []
    bundle_path = ""
    installer_path = ""

    if build_bundle:
        row = _run_step(steps, "export_v5_local_bundle", [sys.executable, "scripts/export_v5_local_bundle.py"], timeout=min(timeout, 300))
        bundle_path = _last_line(row.get("stdout", ""))
    else:
        bundle_path = _latest("dist", "targetcompass_v5_local_bundle_*.zip")

    if build_installer:
        row = _run_step(steps, "build_windows_installer_v5", [sys.executable, "scripts/build_windows_installer_v5.py"], timeout=min(timeout, 300))
        installer_path = _last_line(row.get("stdout", ""))
    else:
        installer_path = _latest("dist", "TargetCompassV5_Windows_Installer_*.zip")

    if validate_zip:
        steps.append(_validate_installer_zip(Path(installer_path) if installer_path else Path()))

    test_timeout = timeout if suite == "e2e" else min(timeout, 240)
    _run_step(steps, f"test_suite_{suite}", [sys.executable, "tc_lite.py", "test-suite", "--suite", suite], timeout=test_timeout)
    _run_step(steps, "v5_doctor", [sys.executable, "tc_lite.py", "v5-doctor", "--project", "vascular_aging_demo"], timeout=min(timeout, 180))

    status = "PASS" if all(row.get("status") == "PASS" for row in steps) else "FAIL"
    report = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "suite": suite,
        "duration_seconds": round(time.monotonic() - started, 3),
        "bundle_path": _display(bundle_path),
        "installer_path": _display(installer_path),
        "steps": steps,
        "artifacts": {
            "test_suite_report": f"results/test_suites/{suite}_test_suite_report.json",
            "doctor_report": "projects/vascular_aging_demo/v5/doctor/v5_doctor_report.json",
        },
        "next_actions": [row.get("failure_reason", "") for row in steps if row.get("status") != "PASS"],
    }
    out = ROOT / "results" / "packaging" / "v5_package_acceptance_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _run_step(steps: list[dict[str, Any]], step_id: str, command: list[str], timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        status = "PASS" if completed.returncode == 0 else "FAIL"
        failure = "" if status == "PASS" else _tail(completed.stderr or completed.stdout)
        row = {
            "step_id": step_id,
            "status": status,
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout,
            "command": " ".join(command),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
            "failure_reason": failure,
        }
    except subprocess.TimeoutExpired as exc:
        row = {
            "step_id": step_id,
            "status": "TIMEOUT",
            "returncode": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout,
            "command": " ".join(command),
            "stdout": _decode(exc.stdout),
            "stderr": _decode(exc.stderr),
            "stdout_tail": _tail(_decode(exc.stdout)),
            "stderr_tail": _tail(_decode(exc.stderr)),
            "failure_reason": f"{step_id} exceeded {timeout}s timeout",
        }
    steps.append(row)
    return row


def _validate_installer_zip(path: Path) -> dict[str, Any]:
    required = {
        "Install-TargetCompassV5.ps1",
        "Launch-TargetCompassV5.ps1",
        "TargetCompassV5-Launcher.cmd",
        "Repair-TargetCompassV5.ps1",
        "Uninstall-TargetCompassV5.ps1",
        "README_CN.md",
        "TargetCompassV5.iss",
        "build_setup_exe.ps1",
        "installer_manifest.json",
        "packaging_profile.json",
        "dependency_cache_manifest.json",
        "runtime_repair_plan.json",
        "payload/targetcompass_v5_local_bundle.zip",
        "runtime_cache/README.md",
        "wheelhouse/README.md",
    }
    if not path.exists():
        return {"step_id": "validate_installer_zip", "status": "FAIL", "failure_reason": f"installer missing: {path}", "path": str(path)}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            missing = sorted(required - names)
            manifest = json.loads(zf.read("installer_manifest.json").decode("utf-8"))
    except Exception as exc:
        return {"step_id": "validate_installer_zip", "status": "FAIL", "failure_reason": str(exc), "path": str(path)}
    checks = [
        not missing,
        manifest.get("requires_preinstalled_python") is False,
        manifest.get("runtime_strategy") == "embedded_python_with_optional_offline_cache",
        manifest.get("default_demo_project") == "vascular_aging_demo",
        "v5-doctor" in manifest.get("install_self_checks", []),
        manifest.get("diagnostic_repair", {}).get("script") == "Repair-TargetCompassV5.ps1",
        manifest.get("launcher_cmd") == "TargetCompassV5-Launcher.cmd",
        manifest.get("service_management", {}).get("start") == "TargetCompassV5-Launcher.cmd",
    ]
    return {
        "step_id": "validate_installer_zip",
        "status": "PASS" if all(checks) else "FAIL",
        "path": _display(path),
        "size_bytes": path.stat().st_size,
        "missing_entries": missing,
        "manifest": manifest,
        "failure_reason": "" if all(checks) else "Installer zip validation failed.",
    }


def _latest(folder: str, pattern: str) -> str:
    files = sorted((ROOT / folder).glob(pattern), key=lambda path: path.stat().st_mtime)
    return str(files[-1]) if files else ""


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _display(value: str | Path) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _tail(text: str, limit: int = 3000) -> str:
    return (text or "")[-limit:]


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["quick", "full", "e2e"], default="quick")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--no-build-bundle", action="store_true")
    parser.add_argument("--no-build-installer", action="store_true")
    args = parser.parse_args()
    report = run_acceptance(
        suite=args.suite,
        build_bundle=not args.no_build_bundle,
        build_installer=not args.no_build_installer,
        timeout=args.timeout,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    raise SystemExit(0 if report.get("status") == "PASS" else 1)


if __name__ == "__main__":
    main()
