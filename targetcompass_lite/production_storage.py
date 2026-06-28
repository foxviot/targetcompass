import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage_manifest import build_storage_manifest
from .v4 import content_hash, read_json, v4_dir


STORAGE_READINESS_SCHEMA = "v4.production_storage_readiness/0.1"


def build_production_storage_readiness(project_dir: Path) -> dict[str, Any]:
    manifest = build_storage_manifest(project_dir)
    local_check = read_json(v4_dir(project_dir) / "local_backend_check.json", {})
    local_sync = read_json(v4_dir(project_dir) / "local_backend_sync.json", {})
    postgres_enabled = bool(os.environ.get("TARGETCOMPASS_POSTGRES_DSN"))
    postgres_active = local_check.get("postgres", {}).get("schema_ready") is True
    object_endpoint = os.environ.get("TARGETCOMPASS_S3_ENDPOINT") or os.environ.get("TARGETCOMPASS_MINIO_ENDPOINT")
    object_bucket = os.environ.get("TARGETCOMPASS_OBJECT_BUCKET", "")
    object_active = local_check.get("minio", {}).get("bucket_ready") is True
    checks = [
        _check("sqlite_fallback_available", manifest.get("sqlite_local", {}).get("exists", False), "Local SQLite Evidence DB exists.", "Run Evidence DB migration/import before production export."),
        _check("postgres_contract_configured", postgres_enabled or postgres_active, "PostgreSQL DSN env or local backend is configured.", "Run python tc_lite.py local-backends-prepare, start Docker Compose, then run local-backends-check.", severity="warning"),
        _check("postgres_backend_active", postgres_active, "PostgreSQL local backend is migrated and reachable.", "Run python tc_lite.py local-backends-check --project <project> after starting Docker Compose.", severity="warning"),
        _check("object_endpoint_configured", bool(object_endpoint) or object_active, "S3/MinIO endpoint env or local backend is configured.", "Run local-backends-prepare and start MinIO.", severity="warning"),
        _check("object_bucket_configured", bool(object_bucket) or object_active, "Object bucket env or local backend bucket is configured.", "Set TARGETCOMPASS_OBJECT_BUCKET or use the default local MinIO bucket.", severity="warning"),
        _check("object_backend_active", object_active, "MinIO bucket is reachable and writable.", "Run python tc_lite.py local-backends-check --project <project> after starting Docker Compose.", severity="warning"),
        _check("project_prefix_isolated", _prefix_isolated(project_dir, manifest), "Object storage prefix is project scoped.", "Use targetcompass/<project_id>/ object prefixes only."),
        _check("local_paths_project_scoped", _local_paths_scoped(project_dir), "Project files are scoped below the project root.", "Move generated artifacts under the project root before packaging."),
    ]
    warnings = [row for row in checks if row["status"] == "WARN"]
    failures = [row for row in checks if row["status"] == "FAIL"]
    payload = {
        "schema_version": STORAGE_READINESS_SCHEMA,
        "project_id": project_dir.name,
        "status": "BLOCKED" if failures else ("READY_WITH_WARNINGS" if warnings else "READY"),
        "active_backends": manifest.get("active_backends", {}),
        "postgres": {
            "enabled": postgres_enabled or postgres_active,
            "active": postgres_active,
            "dsn_env": "TARGETCOMPASS_POSTGRES_DSN",
            "migration_command": f"python tc_lite.py service-call --project {project_dir.name} --service-id evidence_service --action migrate",
            "local_backend_check": "v4/local_backend_check.json" if local_check else "",
            "production_gap": "" if postgres_active else "No live PostgreSQL backend verified for this project.",
        },
        "object_store": {
            "enabled": bool((object_endpoint and object_bucket) or object_active),
            "active": object_active,
            "endpoint_env": "TARGETCOMPASS_S3_ENDPOINT or TARGETCOMPASS_MINIO_ENDPOINT",
            "bucket_env": "TARGETCOMPASS_OBJECT_BUCKET",
            "prefix": f"targetcompass/{project_dir.name}/",
            "local_backend_check": "v4/local_backend_check.json" if local_check else "",
            "local_sync_manifest": local_sync.get("object_store_sync", {}).get("manifest", "") if local_sync else "",
            "production_gap": "" if object_active else "No live S3/MinIO backend verified for this project.",
        },
        "cross_project_isolation": {
            "project_id": project_dir.name,
            "local_project_root": str(project_dir.resolve()),
            "object_prefix": f"targetcompass/{project_dir.name}/",
            "policy": "Evidence rows, report artifacts, and object-store keys must be scoped by project_id.",
        },
        "checks": checks,
        "artifact_refs": {
            "storage_manifest": "v4/storage_backend_manifest.json",
            "readiness": "v4/production_storage_readiness.json",
        },
        "generated_at": _now(),
    }
    payload["readiness_hash"] = content_hash(payload)
    path = production_storage_readiness_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def production_storage_readiness_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "production_storage_readiness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _check(check_id: str, passed: bool, ok: str, remediation: str, severity: str = "error") -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "PASS" if passed else ("WARN" if severity == "warning" else "FAIL"),
        "severity": severity,
        "message": ok if passed else remediation,
        "remediation": "" if passed else remediation,
    }


def _prefix_isolated(project_dir: Path, manifest: dict[str, Any]) -> bool:
    prefix = manifest.get("object_store_contract", {}).get("artifact_prefix", "")
    return prefix == f"targetcompass/{project_dir.name}/"


def _local_paths_scoped(project_dir: Path) -> bool:
    root = project_dir.resolve()
    for rel in ["evidence.sqlite", "reports", "results", "v4"]:
        path = (project_dir / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
