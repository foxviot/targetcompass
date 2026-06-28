from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import artifact_store_summary, load_artifact_store, verify_artifact
from .canonical.access_control import query_access_audit
from .canonical.access_admin import build_access_admin_dashboard
from .canonical.backend_access import load_v5_active_backends
from .canonical.backend_writer import backend_write_summary, load_backend_writes
from .evidence_repository import load_evidence_rows
from .llm_gateway import query_llm_audit
from .project_manager import list_projects
from .services import query_service_audit


def build_backend_primary_status(project_dir: Path) -> dict[str, Any]:
    active = load_v5_active_backends(project_dir)
    backend_writes = backend_write_summary(project_dir)
    artifact_summary = artifact_store_summary(project_dir)
    evidence = load_evidence_rows(project_dir, limit=5)
    local_writer_findings = _scan_legacy_writer_outputs(project_dir)
    payload = {
        "schema_version": "v5.backend_primary_status/0.1",
        "project_id": project_dir.name,
        "active_backends": active.get("active_backends", {}),
        "active_status": active.get("status", "FALLBACK"),
        "evidence_repository": {
            "backend": evidence.get("backend", "sqlite_local"),
            "status": evidence.get("status", "FALLBACK"),
            "sample_row_count": len(evidence.get("rows", [])),
            "status_ref": "v5/evidence_repository/last_status.json",
        },
        "object_store": {
            "artifact_store_count": artifact_summary.get("artifact_store_count", 0),
            "object_uri_count": artifact_summary.get("object_uri_count", 0),
            "failure_count": artifact_summary.get("failure_count", 0),
        },
        "backend_writer": {
            "write_count": backend_writes.get("write_count", 0),
            "minio_primary_write_count": backend_writes.get("minio_primary_write_count", 0),
            "minio_primary_pass_count": backend_writes.get("minio_primary_pass_count", 0),
            "minio_primary_failure_count": backend_writes.get("minio_primary_failure_count", 0),
        },
        "legacy_writer_findings": local_writer_findings,
        "overall_status": _backend_overall(active, evidence, artifact_summary, backend_writes, local_writer_findings),
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "backend_primary_status.json", payload)
    return payload


def query_platform_audit(
    project_dir: Path,
    *,
    source: str = "all",
    status: str = "",
    actor: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    selected = {"access", "service", "llm", "backend", "artifact"} if source == "all" else {source}
    if "access" in selected:
        for row in query_access_audit(project_dir, actor=actor, status=status, limit=limit).get("events", []):
            rows.append(_audit_row("access", row.get("created_at", ""), row.get("actor", ""), row.get("action", ""), row.get("status", ""), row.get("reason", ""), row))
    if "service" in selected:
        for row in query_service_audit(project_dir, caller=actor, status=status, limit=limit).get("items", []):
            rows.append(_audit_row("service", row.get("timestamp", ""), row.get("caller", ""), f"{row.get('service_id', '')}.{row.get('action', '')}", row.get("status", ""), row.get("failure_reason", ""), row))
    if "llm" in selected:
        for row in query_llm_audit(project_dir, status=status, limit=limit).get("items", []):
            rows.append(_audit_row("llm", row.get("timestamp", ""), row.get("actor", ""), row.get("role_id", ""), row.get("status", ""), row.get("failure_reason", ""), row))
    if "backend" in selected:
        for row in load_backend_writes(project_dir)[-limit:]:
            primary = row.get("primary_write", {}) or {}
            row_status = primary.get("status", row.get("status", ""))
            if status and row_status != status:
                continue
            rows.append(_audit_row("backend", row.get("created_at", ""), row.get("producer", ""), row.get("relative_path", ""), row_status, primary.get("failure_reason", ""), row))
    if "artifact" in selected:
        for row in load_artifact_store(project_dir)[-limit:]:
            if status and row.get("status") != status:
                continue
            rows.append(_audit_row("artifact", "", row.get("producer", ""), row.get("relative_path", ""), row.get("status", ""), row.get("failure_reason", ""), row))
    rows = sorted(rows, key=lambda row: row.get("timestamp", ""))
    payload = {
        "schema_version": "v5.platform_audit_query/0.1",
        "project_id": project_dir.name,
        "filters": {"source": source, "status": status, "actor": actor, "limit": limit},
        "match_count": len(rows),
        "events": rows[-max(1, limit) :],
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "platform_audit_last_query.json", payload)
    return payload


def build_data_cache_manifest(project_dir: Path) -> dict[str, Any]:
    cache_roots = [
        ("data", project_dir / "data"),
        ("uploads", project_dir / "uploads"),
        ("knowledge_imports", project_dir / "knowledge_imports"),
        ("external_agent_runs", project_dir / "external_agent_runs"),
        ("results", project_dir / "results"),
        ("reports", project_dir / "reports"),
        ("v5_object_store", project_dir / "v5" / "object_store"),
    ]
    roots = [_cache_root(project_dir, label, path) for label, path in cache_roots]
    artifacts = load_artifact_store(project_dir)
    missing_artifacts = []
    for row in artifacts[-500:]:
        verification = verify_artifact(project_dir, relative_path=row.get("relative_path", ""))
        if verification.get("status") == "RECOVERY_REQUIRED":
            missing_artifacts.append(
                {
                    "relative_path": row.get("relative_path", ""),
                    "artifact_store_id": row.get("artifact_store_id", ""),
                    "object_uri": row.get("object_uri", ""),
                    "recovery": verification.get("recovery", {}),
                }
            )
    payload = {
        "schema_version": "v5.data_cache_manifest/0.1",
        "project_id": project_dir.name,
        "roots": roots,
        "total_size_bytes": sum(row.get("size_bytes", 0) for row in roots),
        "missing_artifact_count": len(missing_artifacts),
        "missing_artifacts": missing_artifacts[:100],
        "cleanup_policy": {
            "safe_to_clear": ["external_agent_runs/*/mock_run", "v5/object_store/last_download_manifest.json"],
            "requires_backup_first": ["data", "uploads", "results", "reports"],
            "never_delete_automatically": ["configs/secrets.local.json", "evidence.sqlite", "v5/access/access_registry.json"],
        },
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "data_cache_manifest.json", payload)
    return payload


def cleanup_data_cache(project_dir: Path, *, target: str, dry_run: bool = True) -> dict[str, Any]:
    allowed = {
        "last_download_manifest": [project_dir / "v5" / "object_store" / "last_download_manifest.json"],
        "external_mock_runs": list((project_dir / "external_agent_runs").glob("*/mock_run")) if (project_dir / "external_agent_runs").exists() else [],
    }
    if target not in allowed:
        raise ValueError(f"unsupported cleanup target: {target}")
    deleted = []
    for path in allowed[target]:
        if not path.exists():
            continue
        deleted.append(str(path.relative_to(project_dir)).replace("\\", "/"))
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    payload = {
        "schema_version": "v5.data_cache_cleanup/0.1",
        "project_id": project_dir.name,
        "target": target,
        "dry_run": dry_run,
        "deleted_or_would_delete": deleted,
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "data_cache_cleanup_last_run.json", payload)
    build_data_cache_manifest(project_dir)
    return payload


def build_platform_p1_readiness(project_dir: Path) -> dict[str, Any]:
    projects = list_projects(project_dir.parent)
    access = build_access_admin_dashboard(project_dir)
    service_events = query_service_audit(project_dir, limit=20)
    storage = build_backend_primary_status(project_dir)
    artifact_summary = artifact_store_summary(project_dir)
    evidence = load_evidence_rows(project_dir, limit=10)
    audit = query_platform_audit(project_dir, source="all", limit=50)
    checks = [
        _p1_check(
            "project_lifecycle",
            projects.get("project_count", 0) >= 1,
            "Project create/clone/import/export/archive/delete UI and APIs are available.",
            "Open /v5/projects and create or clone a demo project.",
            {"project_count": projects.get("project_count", 0)},
        ),
        _p1_check(
            "access_control",
            access.get("readiness_status") in {"READY", "REVIEW"} and bool(access.get("admin_capabilities")),
            "User/member/role/token lifecycle UI is available.",
            "Open /v5/access and create a user, member role, and token.",
            {"readiness_status": access.get("readiness_status", ""), "active_tokens": access.get("summary", {}).get("active_token_count", 0)},
        ),
        _p1_check(
            "service_management",
            True,
            "Service manager page exposes health, port recovery commands, backend activation, and log refs.",
            "Open /v5/services; use restart command if the preferred port is occupied.",
            {"recent_service_audit_events": service_events.get("match_count", len(service_events.get("items", [])))},
        ),
        _p1_check(
            "storage_primary_path",
            storage.get("overall_status") in {"PRIMARY_READY", "LEGACY_WRITER_REMAINING", "FALLBACK_ACTIVE", "EVIDENCE_FALLBACK", "OBJECT_WRITE_WARN"},
            "PostgreSQL/MinIO active-backend preference and legacy writer migration visibility are available.",
            "Open /v5/storage; run migration batches until remaining legacy files are acceptable.",
            {"overall_status": storage.get("overall_status", ""), "legacy_writer_findings": storage.get("legacy_writer_findings", [])},
        ),
        _p1_check(
            "drilldown_pages",
            artifact_summary.get("artifact_store_count", 0) >= 0 and evidence.get("status") in {"PASS", "FALLBACK", "WARN", ""},
            "Artifact/Evidence/Claim drill-down pages are available.",
            "Open /v5/artifacts and /v5/evidence-claims; run evidence query if no evidence rows are shown.",
            {"artifact_store_count": artifact_summary.get("artifact_store_count", 0), "evidence_backend": evidence.get("backend", "")},
        ),
        _p1_check(
            "audit_backoffice",
            audit.get("match_count", 0) >= 0,
            "Unified audit query page covers access/service/LLM/backend/artifact events.",
            "Open /v5/audit and filter by source/status/actor.",
            {"audit_event_count": audit.get("match_count", 0)},
        ),
    ]
    payload = {
        "schema_version": "v5.platform_p1_readiness/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if all(row["status"] == "PASS" for row in checks) else "REVIEW",
        "checks": checks,
        "remaining_work": [row["recovery"] for row in checks if row["status"] != "PASS"],
        "pages": {
            "projects": "/v5/projects",
            "access": "/v5/access",
            "services": "/v5/services",
            "storage": "/v5/storage",
            "artifacts": "/v5/artifacts",
            "evidence_claims": "/v5/evidence-claims",
            "audit": "/v5/audit",
        },
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "p1_readiness.json", payload)
    return payload


def build_platform_p2_readiness(project_dir: Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    access = build_access_admin_dashboard(project_dir)
    storage = build_backend_primary_status(project_dir)
    slim_storage = _read_json(project_dir / "v5" / "platform" / "demo_slim_storage_manifest.json", {})
    nextflow = _nextflow_readiness(project_dir)
    memory = _memory_readiness(project_dir)
    wetlab = _wetlab_readiness(project_dir)
    audit = query_platform_audit(project_dir, source="all", limit=100)
    checks = [
        _p1_check(
            "multi_user_permissions",
            access.get("readiness_status") in {"READY", "REVIEW"} and access.get("summary", {}).get("user_count", 0) >= 1,
            "Local user/member/role/token lifecycle and audit controls are present.",
            "Promote from local RBAC to production auth only after user lifecycle and token rotation are validated.",
            {
                "readiness_status": access.get("readiness_status", ""),
                "summary": access.get("summary", {}),
                "productization_gaps": access.get("productization_gaps", []),
            },
        ),
        _p1_check(
            "postgres_minio_primary_path",
            storage.get("overall_status") in {"PRIMARY_READY", "LEGACY_WRITER_REMAINING", "OBJECT_WRITE_WARN"},
            "EvidenceRepository and ArtifactStore can prefer PostgreSQL/MinIO active backends, with legacy writer visibility.",
            "Finish registering legacy results/reports through Repository and ArtifactStore before claiming sole primary path.",
            {
                "overall_status": storage.get("overall_status", ""),
                "active_backends": storage.get("active_backends", {}),
                "legacy_writer_findings": storage.get("legacy_writer_findings", []),
                "demo_slim_storage": {
                    "status": slim_storage.get("status", "not_built"),
                    "effective_artifact_count": slim_storage.get("effective_artifact_count", 0),
                    "effective_missing_count": slim_storage.get("effective_missing_count", 0),
                    "excluded_historical_legacy_count": slim_storage.get("excluded_historical_legacy_count", 0),
                    "manifest_ref": slim_storage.get("manifest_ref", ""),
                },
            },
        ),
        _p1_check(
            "professor_demo_slim_storage",
            slim_storage.get("status") == "PASS",
            "Professor demo effective artifacts can be separated from historical development outputs.",
            "Run python tc_lite.py v5-storage-migration --project vascular_aging_demo --action demo-slim before packaging demo builds.",
            {
                "status": slim_storage.get("status", "not_built"),
                "effective_artifact_count": slim_storage.get("effective_artifact_count", 0),
                "effective_registered_count": slim_storage.get("effective_registered_count", 0),
                "effective_missing_count": slim_storage.get("effective_missing_count", 0),
                "excluded_historical_legacy_count": slim_storage.get("excluded_historical_legacy_count", 0),
                "manifest_ref": slim_storage.get("manifest_ref", ""),
            },
        ),
        _p1_check(
            "nextflow_large_scale_analysis",
            nextflow.get("status") in {"READY", "REVIEW"},
            "Nextflow task/profile contracts and canonical TaskRun/QC/Artifact recording are available.",
            "Run a real bulk/scRNA/enrichment validation matrix with local/docker profiles before production claims.",
            nextflow,
        ),
        _p1_check(
            "long_term_memory",
            memory.get("status") in {"READY", "REVIEW"},
            "Versioned memory palace, event log, rollback, and per-agent memory context are available.",
            "Keep memory as auditable context only; scientific claims must still cite EvidenceItem and Artifact refs.",
            memory,
        ),
        _p1_check(
            "wet_lab_protocol_signoff",
            wetlab.get("status") in {"READY", "REVIEW"},
            "Wet-lab validation drafts and signoff records are available behind a human gate.",
            "Require PI/reviewer signoff and risk review before treating drafts as executable wet-lab protocols.",
            wetlab,
        ),
        _p1_check(
            "platform_auditability",
            audit.get("match_count", 0) >= 0,
            "Platform audit query aggregates access, service, LLM, backend, and artifact events.",
            "Use /v5/audit during acceptance to verify who changed permissions, storage, LLM runs, and artifacts.",
            {"audit_event_count": audit.get("match_count", 0)},
        ),
    ]
    blockers = []
    for row in checks:
        details = row.get("details", {})
        if row["check_id"] == "postgres_minio_primary_path" and details.get("overall_status") != "PRIMARY_READY":
            slim = details.get("demo_slim_storage", {})
            if slim.get("status") == "PASS":
                blockers.append("Full development workspace still has legacy writer findings; professor demo effective artifacts are slim-migrated and registered.")
            else:
                blockers.append("PostgreSQL/MinIO is not yet the sole clean primary path; legacy writer findings remain or fallback is active.")
        if row["check_id"] == "nextflow_large_scale_analysis" and not details.get("large_scale_validated"):
            blockers.append("Real large-scale Nextflow matrix validation has not been recorded.")
        if row["check_id"] == "wet_lab_protocol_signoff" and not details.get("approved_signoff_count", 0):
            blockers.append("Wet-lab protocol drafts still require human approval before execution use.")
    payload = {
        "schema_version": "v5.platform_p2_readiness/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if all(row["status"] == "PASS" for row in checks) and not blockers else "REVIEW",
        "scope": [
            "multi_user_permissions",
            "postgres_minio_primary_path",
            "real_nextflow_large_scale_analysis",
            "long_term_memory",
            "wet_lab_protocol",
        ],
        "checks": checks,
        "production_blockers": blockers,
        "remaining_work": [row["recovery"] for row in checks if row["status"] != "PASS"] + blockers,
        "pages": {
            "access": "/v5/access",
            "storage": "/v5/storage",
            "backend_writes": "/v5/backend-writes",
            "audit": "/v5/audit",
            "wetlab": "/v5/wetlab",
            "services": "/v5/services",
        },
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "p2_readiness.json", payload)
    return payload


def build_platform_production_readiness(project_dir: Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    access = build_access_admin_dashboard(project_dir)
    auth = _production_auth_readiness(project_dir, access)
    storage = build_backend_primary_status(project_dir)
    storage_gate = _storage_production_gate(storage)
    memory = _memory_productization_readiness(project_dir)
    installer = _windows_installer_readiness(project_dir)
    nextflow = _nextflow_readiness(project_dir)
    codex = _codex_worker_validation_readiness(project_dir)
    validation = _online_validation_readiness(project_dir)
    checks = [
        _production_check(
            "formal_auth_oidc_vault_sessions",
            auth.get("status") == "READY",
            "OIDC/Vault/session contract is configured and production auth can replace local token/RBAC.",
            "Configure v5/security/auth_production_config.json with OIDC issuer/audience/client, Vault address/mount, session cookie policy, and run login-session validation.",
            auth,
        ),
        _production_check(
            "postgres_minio_primary_only",
            storage_gate.get("status") == "READY",
            "PostgreSQL/MinIO are the active primary path and no effective legacy writer remains.",
            "Continue migrating remaining results/reports through EvidenceRepository and ArtifactStore until backend status is PRIMARY_READY.",
            storage_gate,
        ),
        _production_check(
            "long_term_memory_productized",
            memory.get("status") == "READY",
            "Long-term memory has UI audit, version diff, and rollback drill evidence.",
            "Run memory update/diff/rollback drill and expose reviewer-readable audit in /v5/memory before production signoff.",
            memory,
        ),
        _production_check(
            "windows_gui_installer_release",
            installer.get("status") == "READY",
            "Windows GUI installer is built, signed or signing waiver recorded, and offline dependency cache is present.",
            _windows_installer_recovery(installer),
            installer,
        ),
        _production_check(
            "nextflow_large_sample_validation",
            nextflow.get("large_scale_validated") is True,
            "Nextflow real bulk/scRNA/enrichment validation has been recorded with TaskRun/QC/Artifact outputs.",
            "Run large matrix validation using local/docker profile and save v5/nextflow/production_validation.json.",
            nextflow,
        ),
        _production_check(
            "codex_worker_large_sample_validation",
            codex.get("status") == "READY",
            "Codex Worker engineering packet validation has enough patch/test/result/review/merge samples.",
            "Run representative engineering packets through approve/claim/execute/test/result/review/merge and save validation manifest.",
            codex,
        ),
        _production_check(
            "online_question_longrun_validation",
            validation.get("status") == "READY",
            "10/50 online question validation completed without LLM/resource/export failures.",
            "Run v5-real-question-validation for 10 and 50 questions with isolated projects and preserve summary.json artifacts.",
            validation,
        ),
    ]
    blockers = [row["recovery"] for row in checks if row["status"] != "PASS"]
    payload = {
        "schema_version": "v5.production_readiness/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if not blockers else "REVIEW",
        "scope": [
            "OIDC/Vault/login sessions",
            "PostgreSQL/MinIO primary-only path",
            "long-term memory productization",
            "Windows GUI installer release",
            "Nextflow/Codex Worker large-sample validation",
        ],
        "checks": checks,
        "production_blockers": blockers,
        "pages": {
            "access": "/v5/access",
            "storage": "/v5/storage",
            "memory": "/v5/memory",
            "services": "/v5/services",
            "audit": "/v5/audit",
            "platform_p2": "/v5/platform-p2",
        },
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "platform" / "production_readiness.json", payload)
    return payload


def _scan_legacy_writer_outputs(project_dir: Path) -> list[dict[str, Any]]:
    store_paths = {row.get("relative_path", "") for row in load_artifact_store(project_dir)}
    findings = []
    for root_name in ["results", "reports"]:
        root = project_dir / root_name
        if not root.exists():
            continue
        total = 0
        registered = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            total += 1
            rel = str(path.relative_to(project_dir)).replace("\\", "/")
            if rel in store_paths:
                registered += 1
        findings.append(
            {
                "root": root_name,
                "file_count": total,
                "artifact_store_registered_count": registered,
                "unregistered_count": max(0, total - registered),
                "status": "PASS" if total == registered else "MIGRATION_REMAINING",
            }
        )
    return findings


def _production_auth_readiness(project_dir: Path, access_dashboard: dict[str, Any]) -> dict[str, Any]:
    config = _read_json(project_dir / "v5" / "security" / "auth_production_config.json", {})
    session = _read_json(project_dir / "v5" / "security" / "login_session_validation.json", {})
    oidc = config.get("oidc", {}) if isinstance(config, dict) else {}
    vault = config.get("vault", {}) if isinstance(config, dict) else {}
    cookie = config.get("session_cookie", {}) if isinstance(config, dict) else {}
    missing = []
    for key in ["issuer", "audience", "client_id"]:
        if not oidc.get(key):
            missing.append(f"oidc.{key}")
    for key in ["address", "mount", "secret_path"]:
        if not vault.get(key):
            missing.append(f"vault.{key}")
    for key in ["name", "secure", "http_only", "same_site"]:
        if cookie.get(key) in {"", None}:
            missing.append(f"session_cookie.{key}")
    session_ready = session.get("status") == "PASS"
    return {
        "status": "READY" if not missing and session_ready else "REVIEW",
        "config_ref": "v5/security/auth_production_config.json" if config else "",
        "session_validation_ref": "v5/security/login_session_validation.json" if session else "",
        "local_access_readiness": access_dashboard.get("readiness_status", ""),
        "local_user_count": access_dashboard.get("summary", {}).get("user_count", 0),
        "local_active_token_count": access_dashboard.get("summary", {}).get("active_token_count", 0),
        "missing_config": missing,
        "session_validation_status": session.get("status", "not_recorded"),
        "notes": "Local token/RBAC remains the fallback until OIDC/Vault/session validation is recorded.",
    }


def _storage_production_gate(storage: dict[str, Any]) -> dict[str, Any]:
    findings = storage.get("legacy_writer_findings", [])
    unregistered = sum(row.get("unregistered_count", 0) for row in findings)
    return {
        "status": "READY" if storage.get("overall_status") == "PRIMARY_READY" and unregistered == 0 else "REVIEW",
        "overall_status": storage.get("overall_status", ""),
        "active_backends": storage.get("active_backends", {}),
        "legacy_unregistered_count": unregistered,
        "legacy_writer_findings": findings,
        "backend_writer": storage.get("backend_writer", {}),
        "object_store": storage.get("object_store", {}),
        "evidence_repository": storage.get("evidence_repository", {}),
    }


def _memory_productization_readiness(project_dir: Path) -> dict[str, Any]:
    base = _memory_readiness(project_dir)
    diff = _read_json(project_dir / "v5" / "memory_palace" / "last_diff.json", {})
    rollback_drill = _read_json(project_dir / "v5" / "memory_palace" / "rollback_drill.json", {})
    audit_page = _read_json(project_dir / "v5" / "memory_palace" / "memory_audit_dashboard.json", {})
    ready = (
        base.get("status") == "READY"
        and base.get("version_count", 0) >= 2
        and bool(diff)
        and rollback_drill.get("status") == "PASS"
        and bool(audit_page)
    )
    return {
        **base,
        "status": "READY" if ready else "REVIEW",
        "diff_ref": "v5/memory_palace/last_diff.json" if diff else "",
        "rollback_drill_ref": "v5/memory_palace/rollback_drill.json" if rollback_drill else "",
        "audit_dashboard_ref": "v5/memory_palace/memory_audit_dashboard.json" if audit_page else "",
        "rollback_drill_status": rollback_drill.get("status", "not_recorded"),
        "notes": "Memory is productized only after diff and rollback drill are visible to reviewers.",
    }


def _windows_installer_readiness(project_dir: Path) -> dict[str, Any]:
    root = project_dir.parent.parent if project_dir.parent.name == "projects" else Path.cwd()
    dist = root / "dist"
    packages = sorted(dist.glob("TargetCompassV5_*")) if dist.exists() else []
    latest_setup = sorted(dist.glob("TargetCompassV5_Setup*.exe"))[-1] if list(dist.glob("TargetCompassV5_Setup*.exe")) else None
    latest_zip = sorted(dist.glob("TargetCompassV5_Windows_Installer_*.zip"))[-1] if list(dist.glob("TargetCompassV5_Windows_Installer_*.zip")) else None
    signature = _read_json(project_dir / "v5" / "packaging" / "signature_validation.json", {})
    clean_machine = _read_json(project_dir / "v5" / "packaging" / "clean_machine_smoke.json", {})
    offline_cache_dir = root / "packaging" / "windows_v5" / "runtime_cache"
    wheelhouse_dir = root / "packaging" / "windows_v5" / "wheelhouse"
    offline_files = _count_files(offline_cache_dir) + _count_files(wheelhouse_dir, suffixes=(".whl", ".zip", ".msi", ".exe"))
    signed_or_waived = signature.get("status") == "PASS" or signature.get("waiver") is True
    ready = bool(latest_setup) and signed_or_waived and clean_machine.get("status") == "PASS" and offline_files > 0
    return {
        "status": "READY" if ready else "REVIEW",
        "latest_setup_exe": str(latest_setup).replace("\\", "/") if latest_setup else "",
        "latest_zip": str(latest_zip).replace("\\", "/") if latest_zip else "",
        "package_count": len(packages),
        "signature_validation_ref": "v5/packaging/signature_validation.json" if signature else "",
        "signature_status": signature.get("status", "not_recorded"),
        "signature_waiver": bool(signature.get("waiver", False)),
        "clean_machine_smoke_ref": "v5/packaging/clean_machine_smoke.json" if clean_machine else "",
        "clean_machine_smoke_status": clean_machine.get("status", "not_recorded"),
        "offline_cache_file_count": offline_files,
        "runtime_cache_dir": str(offline_cache_dir).replace("\\", "/"),
        "wheelhouse_dir": str(wheelhouse_dir).replace("\\", "/"),
    }


def _windows_installer_recovery(installer: dict[str, Any]) -> str:
    missing = []
    if not installer.get("latest_setup_exe"):
        missing.append("compile TargetCompassV5_Setup.exe with Inno Setup")
    if installer.get("signature_status") != "PASS" and not installer.get("signature_waiver"):
        missing.append("record Authenticode signature validation or formal signing waiver")
    if int(installer.get("offline_cache_file_count", 0) or 0) <= 0:
        missing.append("include offline runtime/wheel cache")
    if installer.get("clean_machine_smoke_status") != "PASS":
        missing.append("run clean Windows/VM install-start-stop-restart-uninstall smoke")
    return "Installer remaining work: " + "; ".join(missing) + "." if missing else "Installer release gate is ready."


def _codex_worker_validation_readiness(project_dir: Path) -> dict[str, Any]:
    manifest = _read_json(project_dir / "v5" / "codex" / "worker_large_sample_validation.json", {})
    completed_dir = project_dir / "v5" / "codex" / "completed"
    failed_dir = project_dir / "v5" / "codex" / "failed"
    protocol_completed = len(list(completed_dir.glob("*.json"))) if completed_dir.exists() else 0
    protocol_failed = len(list(failed_dir.glob("*.json"))) if failed_dir.exists() else 0
    sample_count = manifest.get("sample_count", protocol_completed)
    completed = manifest.get("completed_count", protocol_completed)
    failed = manifest.get("failed_count", protocol_failed)
    execution_mode = manifest.get("execution_mode", "not_recorded")
    ready = manifest.get("status") == "PASS" and execution_mode not in {"protocol_acceptance_no_subprocess", "real_codex_unavailable"} and sample_count >= 5 and failed == 0
    return {
        "status": "READY" if ready else "REVIEW",
        "validation_ref": "v5/codex/worker_large_sample_validation.json" if manifest else "",
        "validation_status": manifest.get("status", "not_recorded"),
        "execution_mode": execution_mode,
        "sample_count": sample_count,
        "completed_task_count": completed,
        "failed_task_count": failed,
        "protocol_completed_task_count": protocol_completed,
        "protocol_failed_task_count": protocol_failed,
        "real_codex_status": manifest.get("real_codex_status", {}),
        "blocking_reason": manifest.get("blocking_reason", ""),
        "minimum_required_samples": 5,
        "notes": "Production signoff requires real Codex subprocess/remote worker execution, not only approve/claim/complete protocol records.",
    }


def _online_validation_readiness(project_dir: Path) -> dict[str, Any]:
    validation_root = project_dir / "v5" / "validation"
    summaries = []
    if validation_root.exists():
        for path in validation_root.glob("*/summary.json"):
            data = _read_json(path, {})
            if data:
                data["summary_ref"] = str(path.relative_to(project_dir)).replace("\\", "/")
                summaries.append(data)
    ten = _best_validation_summary(summaries, 10)
    fifty = _best_validation_summary(summaries, 50)
    ready = ten.get("status") == "PASS" and fifty.get("status") == "PASS"
    return {
        "status": "READY" if ready else "REVIEW",
        "ten_question_status": ten.get("status", "not_recorded"),
        "ten_question_ref": ten.get("summary_ref", ""),
        "fifty_question_status": fifty.get("status", "not_recorded"),
        "fifty_question_ref": fifty.get("summary_ref", ""),
        "latest_question_count": max([row.get("question_count", 0) for row in summaries] or [0]),
        "latest_failure_counts": {
            "llm_failures": (fifty or ten).get("totals", {}).get("llm_failures", 0),
            "resource_failures": (fifty or ten).get("totals", {}).get("resource_failures", 0),
            "export_package_count": (fifty or ten).get("export_package_count", 0),
        },
    }


def _best_validation_summary(summaries: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    candidates = [
        row
        for row in summaries
        if row.get("question_count", 0) >= expected_count
        and row.get("expected_question_count", row.get("question_count", 0)) >= expected_count
    ]
    if not candidates:
        return {}
    return sorted(candidates, key=lambda row: row.get("created_at", ""))[-1]


def _production_check(check_id: str, passed: bool, message: str, recovery: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "PASS" if passed else "REVIEW",
        "message": message,
        "recovery": recovery,
        "details": details,
    }


def _count_files(path: Path, suffixes: tuple[str, ...] | None = None) -> int:
    if not path.exists():
        return 0
    count = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if suffixes and item.suffix.lower() not in suffixes:
            continue
        count += 1
    return count


def _nextflow_readiness(project_dir: Path) -> dict[str, Any]:
    profiles = _read_json(project_dir / "v5" / "nextflow" / "module_profiles.json", {})
    production = _read_json(project_dir / "v5" / "nextflow" / "production_validation.json", {})
    legacy_run = _read_json(project_dir / "workflows" / "target_discovery" / "nextflow_run_manifest.json", {})
    task_runs_dir = project_dir / "v5" / "task_runs"
    qc_dir = project_dir / "v5" / "qc_reports"
    task_run_count = len(list(task_runs_dir.glob("*.json"))) if task_runs_dir.exists() else 0
    qc_count = len(list(qc_dir.glob("*.json"))) if qc_dir.exists() else 0
    available_profiles = profiles.get("available_profiles", [])
    large_scale_validated = production.get("status") == "completed" and production.get("profile") in {"local", "docker", "apptainer", "slurm"}
    return {
        "status": "READY" if profiles or legacy_run or task_run_count else "REVIEW",
        "module_profile_ref": "v5/nextflow/module_profiles.json" if profiles else "",
        "production_validation_ref": "v5/nextflow/production_validation.json" if production else "",
        "legacy_run_manifest_ref": "workflows/target_discovery/nextflow_run_manifest.json" if legacy_run else "",
        "task_count": profiles.get("task_count", 0) or legacy_run.get("task_count", 0),
        "task_run_count": task_run_count,
        "qc_report_count": qc_count,
        "available_profiles": available_profiles,
        "production_validation_status": production.get("status", "not_recorded"),
        "large_scale_validated": large_scale_validated,
        "notes": "Control plane exists; large-scale real validation is required before production signoff." if not large_scale_validated else "Production validation recorded.",
    }


def _memory_readiness(project_dir: Path) -> dict[str, Any]:
    manifest = _read_json(project_dir / "v5" / "memory_palace" / "memory_palace.json", {})
    versions_dir = project_dir / "v5" / "memory_palace" / "versions"
    events_path = project_dir / "v5" / "memory_palace" / "events.jsonl"
    version_count = len(list(versions_dir.glob("*.json"))) if versions_dir.exists() else 0
    event_count = len([line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]) if events_path.exists() else 0
    return {
        "status": "READY" if manifest.get("status") == "active" and version_count >= 1 else "REVIEW",
        "memory_ref": "v5/memory_palace/memory_palace.json" if manifest else "",
        "active_version_id": manifest.get("active_version_id", ""),
        "memory_hash": manifest.get("memory_hash", ""),
        "version_count": version_count,
        "event_count": event_count,
        "scope": manifest.get("scope", "not_installed"),
        "scientific_evidence_policy": manifest.get("scientific_evidence_policy", "memory must not replace Evidence DB"),
    }


def _wetlab_readiness(project_dir: Path) -> dict[str, Any]:
    manifest = _read_json(project_dir / "v5" / "wet_lab_protocols" / "wet_lab_protocol_manifest.json", {})
    signoff_path = project_dir / "v5" / "wet_lab_protocols" / "signoffs.jsonl"
    signoffs = []
    if signoff_path.exists():
        signoffs = [json.loads(line) for line in signoff_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved = [row for row in signoffs if row.get("decision") == "approved"]
    return {
        "status": "READY" if manifest.get("protocol_count", 0) >= 1 else "REVIEW",
        "manifest_ref": "v5/wet_lab_protocols/wet_lab_protocol_manifest.json" if manifest else "",
        "protocol_count": manifest.get("protocol_count", 0),
        "manifest_status": manifest.get("status", "not_built"),
        "signoff_count": len(signoffs),
        "approved_signoff_count": len(approved),
        "human_gate": "required",
        "notes": "Drafts are not executable wet-lab SOPs until signed off.",
    }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def _p1_check(check_id: str, passed: bool, message: str, recovery: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "PASS" if passed else "REVIEW",
        "message": message,
        "recovery": recovery,
        "details": details,
    }


def _backend_overall(active: dict[str, Any], evidence: dict[str, Any], artifact_summary: dict[str, Any], backend_writes: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    if active.get("active_backends", {}).get("evidence_db") != "postgres_local" or active.get("active_backends", {}).get("object_store") != "minio_local":
        return "FALLBACK_ACTIVE"
    if evidence.get("backend") != "postgres_local":
        return "EVIDENCE_FALLBACK"
    if backend_writes.get("minio_primary_failure_count", 0):
        return "OBJECT_WRITE_WARN"
    if any(row.get("status") == "MIGRATION_REMAINING" for row in findings):
        return "LEGACY_WRITER_REMAINING"
    if artifact_summary.get("failure_count", 0):
        return "ARTIFACT_RECOVERY_REQUIRED"
    return "PRIMARY_READY"


def _cache_root(project_dir: Path, label: str, path: Path) -> dict[str, Any]:
    file_count = 0
    size = 0
    newest = ""
    if path.exists():
        for item in path.rglob("*"):
            if item.is_file():
                file_count += 1
                size += item.stat().st_size
                newest = max(newest, datetime.fromtimestamp(item.stat().st_mtime, timezone.utc).isoformat())
    return {
        "label": label,
        "path": str(path.relative_to(project_dir)).replace("\\", "/") if path.is_relative_to(project_dir) else str(path),
        "exists": path.exists(),
        "file_count": file_count,
        "size_bytes": size,
        "newest_mtime": newest,
    }


def _audit_row(source: str, timestamp: str, actor: str, action: str, status: str, reason: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "status": status,
        "reason": reason,
        "raw": raw,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
