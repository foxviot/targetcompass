import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, file_hash, read_json, v4_dir


STORAGE_MANIFEST_SCHEMA = "v4.storage_backend_manifest/0.1"


def build_storage_manifest(project_dir: Path) -> dict[str, Any]:
    evidence_db = project_dir / "evidence.sqlite"
    report_dir = project_dir / "reports"
    local_check = read_json(v4_dir(project_dir) / "local_backend_check.json", {})
    local_sync = read_json(v4_dir(project_dir) / "local_backend_sync.json", {})
    postgres_active = local_check.get("postgres", {}).get("schema_ready") is True
    minio_active = local_check.get("minio", {}).get("bucket_ready") is True
    payload = {
        "schema_version": STORAGE_MANIFEST_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_backends": {
            "evidence_db": "postgres_local" if postgres_active else "sqlite_local",
            "report_artifacts": "local_filesystem",
            "object_store": "minio_local" if minio_active else _object_store_backend(),
        },
        "sqlite_local": {
            "path": "evidence.sqlite",
            "exists": evidence_db.exists(),
            "hash": file_hash(evidence_db) if evidence_db.exists() else "",
        },
        "postgres_contract": {
            "enabled": bool(os.environ.get("TARGETCOMPASS_POSTGRES_DSN") or postgres_active),
            "dsn_env": "TARGETCOMPASS_POSTGRES_DSN",
            "migration_mode": "active_local_docker" if postgres_active else "planned_not_active",
            "required_tables": ["evidence_item", "evidence_metadata", "evidence_migration"],
            "local_backend_check": "v4/local_backend_check.json" if local_check else "",
        },
        "object_store_contract": {
            "enabled": bool(os.environ.get("TARGETCOMPASS_S3_ENDPOINT") or os.environ.get("TARGETCOMPASS_MINIO_ENDPOINT") or minio_active),
            "endpoint_env": "TARGETCOMPASS_S3_ENDPOINT or TARGETCOMPASS_MINIO_ENDPOINT",
            "bucket_env": "TARGETCOMPASS_OBJECT_BUCKET",
            "artifact_prefix": f"targetcompass/{project_dir.name}/",
            "required_objects": _report_objects(project_dir),
            "local_backend_check": "v4/local_backend_check.json" if local_check else "",
            "local_sync_manifest": local_sync.get("object_store_sync", {}).get("manifest", "") if local_sync else "",
        },
        "isolation": {
            "project_root": str(project_dir),
            "project_id": project_dir.name,
            "path_policy": "all local artifacts must stay below project root; external object paths use project prefix",
        },
    }
    payload["storage_hash"] = content_hash(payload)
    out = storage_manifest_path(project_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def storage_manifest_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "storage_backend_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _object_store_backend() -> str:
    if os.environ.get("TARGETCOMPASS_MINIO_ENDPOINT"):
        return "minio_contract"
    if os.environ.get("TARGETCOMPASS_S3_ENDPOINT"):
        return "s3_contract"
    return "local_filesystem"


def _report_objects(project_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((project_dir / "reports").glob("*")):
        if path.is_file():
            rows.append({"path": str(path.relative_to(project_dir)).replace("\\", "/"), "hash": file_hash(path), "bytes": path.stat().st_size})
    return rows
