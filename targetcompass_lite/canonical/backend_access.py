from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import load_artifact_registry


ACTIVE_BACKENDS_PATH = Path("v5") / "active_backends.json"


def load_v5_active_backends(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    path = project_dir / ACTIVE_BACKENDS_PATH
    if not path.exists():
        return {
            "status": "FALLBACK",
            "active_backends": {"evidence_db": "sqlite_local", "object_store": "local_filesystem"},
            "source_ref": "",
            "fallback_reason": "v5/active_backends.json is missing.",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "status": "FALLBACK",
            "active_backends": {"evidence_db": "sqlite_local", "object_store": "local_filesystem"},
            "source_ref": str(ACTIVE_BACKENDS_PATH).replace("\\", "/"),
            "fallback_reason": f"active_backends.json is invalid JSON: {exc}",
        }
    payload.setdefault("active_backends", {})
    payload["source_ref"] = str(ACTIVE_BACKENDS_PATH).replace("\\", "/")
    return payload


def backend_status_summary(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    active = load_v5_active_backends(project_dir)
    backend_check = _read_json(project_dir / "v4" / "local_backend_check.json", {})
    backend_sync = _read_json(project_dir / "v4" / "local_backend_sync.json", {})
    storage_readiness = _read_json(project_dir / "v4" / "production_storage_readiness.json", {})
    return {
        "status": active.get("status", "FALLBACK"),
        "source_ref": active.get("source_ref", ""),
        "active_backends": active.get("active_backends", {}),
        "read_preference": (active.get("policy") or {}).get("read_preference", "sqlite_local"),
        "artifact_write_preference": (active.get("policy") or {}).get("artifact_write_preference", "local_filesystem"),
        "fallback_reason": active.get("fallback_reason", ""),
        "backend_check_ref": active.get("backend_check_ref", "v4/local_backend_check.json" if backend_check else ""),
        "backend_sync_ref": active.get("backend_sync_ref", "v4/local_backend_sync.json" if backend_sync else ""),
        "backend_check_status": backend_check.get("status", "not_checked"),
        "backend_sync_status": backend_sync.get("status", "not_synced"),
        "storage_readiness_status": storage_readiness.get("status", "not_built"),
    }


def load_artifact_registry_preferred(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    active = load_v5_active_backends(project_dir)
    active_backends = active.get("active_backends", {})
    rows = load_artifact_registry(project_dir)
    minio_manifest = _read_json(project_dir / "v4" / "minio_artifact_manifest.json", {})
    minio_objects = _minio_object_lookup(minio_manifest)
    source_backend = active_backends.get("object_store", "local_filesystem")
    enriched = []
    for row in rows:
        item = dict(row)
        item["source_backend"] = source_backend
        item["backend_preference_ref"] = active.get("source_ref", "")
        object_info = minio_objects.get(item.get("path", ""))
        if source_backend == "minio_local" and object_info:
            item["object_store_ref"] = object_info.get("object_key", "") or object_info.get("key", "") or object_info.get("uri", "")
            item["object_store_bucket"] = object_info.get("bucket", "")
            item["object_store_synced"] = object_info.get("status", "").upper() in {"PASS", "SYNCED", "OK"} or bool(item["object_store_ref"])
        else:
            item.setdefault("object_store_synced", False)
        enriched.append(item)
    return {
        "source": source_backend,
        "backend_status": active.get("status", "FALLBACK"),
        "backend_preference_ref": active.get("source_ref", ""),
        "active_backends": active_backends,
        "registry_ref": "v5/artifact_registry.jsonl",
        "artifacts": enriched,
        "fallback_reason": active.get("fallback_reason", ""),
    }


def _minio_object_lookup(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in manifest.get("objects", []) or manifest.get("artifacts", []) or []:
        if isinstance(row, dict):
            path = row.get("path") or row.get("relative_path") or row.get("artifact_path")
            if path:
                out[str(path).replace("\\", "/")] = row
    return out


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
