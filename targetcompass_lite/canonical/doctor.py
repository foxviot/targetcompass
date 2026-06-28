from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .access_control import access_readiness
from .backend_writer import backend_write_summary, write_json_artifact
from .backend_access import backend_status_summary, load_artifact_registry_preferred
from .nextflow_execution import load_qc_reports, load_task_runs
from .report_manifest import build_canonical_report_manifest
from .schemas import now_iso
from .storage_primary_gate import build_storage_primary_gate


DOCTOR_SCHEMA_VERSION = "v5.doctor_report/0.1"


def run_v5_doctor(project_dir: str | Path, *, build_report: bool = True) -> dict[str, Any]:
    project_dir = Path(project_dir)
    checks: list[dict[str, Any]] = []
    v5_dir = project_dir / "v5"
    checks.append(_check("project_dir_exists", project_dir.exists(), str(project_dir), "Project directory is missing."))
    checks.append(_check("v5_dir_exists", v5_dir.exists(), "v5 directory exists.", "Run v5-run-local first."))
    checks.append(_check("project_state", (v5_dir / "project_state.json").exists(), "v5/project_state.json exists.", "Run v5-run-local first."))

    backend = backend_status_summary(project_dir)
    access = access_readiness(project_dir)
    storage_gate = build_storage_primary_gate(project_dir)
    checks.append(
        _check(
            "active_backends_manifest",
            bool(backend.get("source_ref")) and (project_dir / backend["source_ref"]).exists(),
            f"active backend manifest: {backend.get('source_ref')}",
            "Run v5-backends-activate or accept local fallback.",
        )
    )
    checks.append(
        _check(
            "backend_mode",
            backend.get("status") in {"ACTIVE", "FALLBACK"},
            f"backend status: {backend.get('status')}",
            "Rebuild v5/active_backends.json.",
            severity="warn",
        )
    )
    checks.append(
        _check(
            "access_control",
            access.get("status") in {"READY", "READY_WITH_WARNINGS"},
            f"access status: {access.get('status')}",
            "Initialize v5 access control and add at least one active project member.",
            severity="warn",
        )
    )
    checks.append(
        _check(
            "storage_primary_gate",
            storage_gate.get("status") in {"READY", "READY_WITH_WARNINGS"},
            f"storage primary gate: {storage_gate.get('status')}",
            "Activate PostgreSQL/MinIO or keep local fallback explicitly declared.",
            severity="warn",
        )
    )

    artifact_bundle = load_artifact_registry_preferred(project_dir)
    artifacts = artifact_bundle.get("artifacts", [])
    checks.append(_check("artifact_registry", bool(artifacts), f"{len(artifacts)} artifact(s)", "Run v5-run-local to generate artifacts."))
    checks.append(
        _check(
            "artifact_backend_preference",
            bool(artifact_bundle.get("source")),
            f"artifact source preference: {artifact_bundle.get('source')}",
            "Check v5/active_backends.json.",
            severity="warn",
        )
    )

    task_runs = load_task_runs(project_dir)
    qc_reports = load_qc_reports(project_dir)
    checks.append(_check("task_runs", bool(task_runs), f"{len(task_runs)} TaskRun record(s)", "Run v5-run-local."))
    checks.append(_check("qc_reports", bool(qc_reports), f"{len(qc_reports)} QCReport record(s)", "Run v5-run-local."))

    report_error = ""
    manifest: dict[str, Any] = {}
    try:
        manifest = build_canonical_report_manifest(project_dir) if build_report else _read_json(v5_dir / "reports" / "canonical_report_manifest.json", {})
    except Exception as exc:  # pragma: no cover - exercised through CLI failure mode
        report_error = str(exc)
    checks.append(
        _check(
            "canonical_report_manifest",
            bool(manifest) and not report_error,
            "v5/reports/canonical_report_manifest.json can be built.",
            f"Build failed: {report_error}" if report_error else "Run v5-report-manifest.",
        )
    )

    packaging_files = [
        Path("scripts/export_v5_local_bundle.py"),
        Path("scripts/build_windows_installer_v5.py"),
        Path("packaging/windows_v5/Install-TargetCompassV5.ps1"),
        Path("packaging/windows_v5/Launch-TargetCompassV5.ps1"),
        Path("packaging/windows_v5/Uninstall-TargetCompassV5.ps1"),
    ]
    missing_packaging = [str(path).replace("\\", "/") for path in packaging_files if not path.exists()]
    checks.append(_check("v5_packaging_files", not missing_packaging, "v5 packaging scripts exist.", "Missing: " + ", ".join(missing_packaging), severity="warn"))

    dist_packages = sorted(Path("dist").glob("*V5*Installer*.zip")) + sorted(Path("dist").glob("targetcompass_v5_local_bundle_*.zip"))
    checks.append(_check("v5_dist_packages", bool(dist_packages), f"{len(dist_packages)} package(s) in dist/", "Run scripts/build_windows_installer_v5.py.", severity="warn"))

    failed = [row for row in checks if row["status"] == "FAIL"]
    warnings = [row for row in checks if row["status"] == "WARN"]
    payload = {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "generated_at": now_iso(),
        "python": sys.version.split()[0],
        "status": "FAIL" if failed else "WARN" if warnings else "PASS",
        "backend_summary": backend,
        "access_summary": access.get("summary", {}),
        "access_status": access.get("status", ""),
        "storage_primary_gate": {
            "status": storage_gate.get("status"),
            "primary_path": storage_gate.get("primary_path", {}),
            "legacy_local_writer_count": len(storage_gate.get("legacy_local_writers", [])),
        },
        "backend_write_summary": backend_write_summary(project_dir),
        "artifact_query": {
            "source": artifact_bundle.get("source"),
            "backend_status": artifact_bundle.get("backend_status"),
            "artifact_count": len(artifacts),
            "registry_ref": artifact_bundle.get("registry_ref"),
        },
        "task_run_count": len(task_runs),
        "qc_report_count": len(qc_reports),
        "report_manifest_ref": "v5/reports/canonical_report_manifest.json" if manifest else "",
        "package_refs": [str(path).replace("\\", "/") for path in dist_packages],
        "checks": checks,
        "next_actions": _next_actions(checks),
    }
    out = v5_dir / "doctor" / "v5_doctor_report.json"
    write_json_artifact(project_dir, out.relative_to(project_dir), payload, producer="v5_doctor", artifact_type="doctor_report")
    return payload


def _check(check_id: str, ok: bool, message: str, remediation: str, *, severity: str = "fail") -> dict[str, Any]:
    status = "PASS" if ok else ("WARN" if severity == "warn" else "FAIL")
    return {"check_id": check_id, "status": status, "message": message if ok else remediation, "remediation": "" if ok else remediation}


def _next_actions(checks: list[dict[str, Any]]) -> list[str]:
    return [row["remediation"] for row in checks if row.get("status") in {"FAIL", "WARN"} and row.get("remediation")]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
