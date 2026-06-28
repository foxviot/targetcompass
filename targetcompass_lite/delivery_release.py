from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import ROOT
from .v4 import content_hash


DELIVERY_FREEZE_SCHEMA = "v5.delivery_freeze/0.1"


def freeze_v5_development_delivery(project_dir: str | Path, *, release_label: str = "v5-local-dev-acceptance") -> dict[str, Any]:
    project_dir = Path(project_dir)
    dist = ROOT / "dist"
    professor_bundle = _latest(dist, "targetcompass_v5_professor_demo_bundle_*.zip")
    installer_bundle = _latest(dist, "TargetCompassV5_Windows_Installer_*.zip")
    developer_bundle = _latest(dist, "targetcompass_v5_developer_bundle_*.zip")
    validation_10 = project_dir / "v5" / "validation" / "real_question_e2e_10" / "e2e10_summary.json"
    validation_50 = project_dir / "v5" / "validation" / "real_question_e2e_50" / "summary.json"
    quick = ROOT / "results" / "test_suites" / "quick_test_suite_report.json"
    full = ROOT / "results" / "test_suites" / "full_test_suite_report.json"
    e2e = ROOT / "results" / "test_suites" / "e2e_test_suite_report.json"
    package_acceptance = ROOT / "results" / "packaging" / "v5_package_acceptance_report.json"
    p1_readiness = project_dir / "v5" / "platform" / "p1_readiness.json"
    formal_exe = dist / "TargetCompassV5_Setup.exe"
    install_smoke = ROOT / "tmp" / "TargetCompassV5_install_smoke7_result.json"
    uninstall_smoke = ROOT / "tmp" / "TargetCompassV5_uninstall_smoke7_result.json"
    payload = {
        "schema_version": DELIVERY_FREEZE_SCHEMA,
        "release_label": release_label,
        "project_id": project_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recommended_delivery_files": {
            "professor_demo_bundle": _file_ref(professor_bundle),
            "windows_script_installer_bundle": _file_ref(installer_bundle),
            "developer_bundle": _file_ref(developer_bundle),
            "formal_setup_exe": _file_ref(formal_exe),
        },
        "validation_reports": {
            "real_question_10": _json_report_ref(validation_10),
            "real_question_50": _json_report_ref(validation_50),
            "quick": _json_report_ref(quick),
            "full": _json_report_ref(full),
            "e2e": _json_report_ref(e2e),
            "package_acceptance": _json_report_ref(package_acceptance),
            "platform_p1_readiness": _json_report_ref(p1_readiness),
            "windows_install_smoke": _json_report_ref(install_smoke),
            "windows_uninstall_smoke": _json_report_ref(uninstall_smoke),
        },
        "p0_status": {
            "formal_setup_exe_compiled": formal_exe.exists(),
            "clean_windows_install_test": _install_smoke_status(install_smoke, uninstall_smoke),
            "real_question_50_validation": "completed" if validation_50.exists() else "not_run",
            "metadata_manual_correction_ui": "implemented_with_required_field_status_and_locked_analysis_button",
            "delivery_version_manifest": "generated",
        },
        "known_blockers": _known_blockers(formal_exe.exists(), validation_50.exists()),
        "handoff_doc": _rel(ROOT / "docs" / "TargetCompass_v5_开发验收版使用说明.md"),
    }
    payload["delivery_hash"] = content_hash(payload)
    out = project_dir / "v5" / "delivery" / "v5_development_delivery_freeze.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _known_blockers(exe_exists: bool, validation_50_exists: bool) -> list[str]:
    blockers = []
    if not exe_exists:
        blockers.append("TargetCompassV5_Setup.exe is not compiled; install Inno Setup and run packaging/windows_v5/build_setup_exe.ps1.")
    if not validation_50_exists:
        blockers.append("50-question online LLM/resource-discovery validation has not been run; 10-question validation is available.")
    blockers.append("True clean Windows VM install/start/stop/restart/uninstall test is still recommended before external delivery.")
    return blockers


def _install_smoke_status(install_smoke: Path, uninstall_smoke: Path) -> str:
    install = _read_json(install_smoke)
    uninstall = _read_json(uninstall_smoke)
    if install.get("install_status") == "PASS" and uninstall.get("uninstall_status") == "PASS":
        return "isolated_local_smoke_pass"
    if install or uninstall:
        return "isolated_local_smoke_review"
    return "not_recorded"


def _latest(folder: Path, pattern: str) -> Path:
    files = sorted(folder.glob(pattern), key=lambda path: path.stat().st_mtime)
    return files[-1] if files else folder / pattern.replace("*", "MISSING")


def _file_ref(path: Path) -> dict[str, Any]:
    return {
        "path": _rel(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "last_modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat() if path.exists() else "",
    }


def _json_report_ref(path: Path) -> dict[str, Any]:
    ref = _file_ref(path)
    if path.exists():
        data = _read_json(path)
        ref.update(
            {
                "status": data.get("status", data.get("install_status", data.get("uninstall_status", ""))),
                "question_count": data.get("question_count", ""),
                "passed_count": data.get("passed_count", ""),
                "failed_count": data.get("failed_count", ""),
                "executed_count": data.get("executed_count", ""),
            }
        )
    return ref


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = {}
    for encoding in ("utf-8", "utf-16", "utf-8-sig"):
        try:
            data = json.loads(path.read_text(encoding=encoding))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return data if isinstance(data, dict) else {}


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
