from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..local_backends import _s3_request, local_backend_env
from .schemas import now_iso


BACKEND_WRITE_SCHEMA = "v5.backend_write/0.1"


def write_json_artifact(
    project_dir: str | Path,
    relative_path: str | Path,
    payload: dict[str, Any] | list[Any],
    *,
    producer: str,
    artifact_type: str,
    strict: bool = False,
) -> dict[str, Any]:
    data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return write_bytes_artifact(project_dir, relative_path, data, producer=producer, artifact_type=artifact_type, strict=strict)


def write_bytes_artifact(
    project_dir: str | Path,
    relative_path: str | Path,
    data: bytes,
    *,
    producer: str,
    artifact_type: str,
    strict: bool = False,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    rel = str(relative_path).replace("\\", "/")
    local_path = project_dir / rel
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    active = _load_active_backends(project_dir)
    object_backend = (active.get("active_backends") or {}).get("object_store", "local_filesystem")
    write = {
        "schema_version": BACKEND_WRITE_SCHEMA,
        "project_id": project_dir.name,
        "relative_path": rel,
        "producer": producer,
        "artifact_type": artifact_type,
        "created_at": now_iso(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "local_copy": {"path": rel, "status": "written"},
        "primary_backend": object_backend,
        "primary_write": {"status": "SKIPPED", "reason": "object backend is local_filesystem"},
        "active_backend_ref": active.get("source_ref", ""),
    }
    if object_backend == "minio_local":
        write["primary_write"] = _put_minio(project_dir, rel, data)
        if strict and write["primary_write"].get("status") != "PASS":
            _append_backend_write(project_dir, write)
            raise RuntimeError(write["primary_write"].get("failure_reason") or "primary backend write failed")
    _append_backend_write(project_dir, write)
    return write


def load_backend_writes(project_dir: str | Path) -> list[dict[str, Any]]:
    path = _backend_writes_path(Path(project_dir))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def backend_write_summary(project_dir: str | Path) -> dict[str, Any]:
    rows = load_backend_writes(project_dir)
    primary = [row for row in rows if row.get("primary_backend") == "minio_local"]
    passed = [row for row in primary if row.get("primary_write", {}).get("status") == "PASS"]
    failed = [row for row in primary if row.get("primary_write", {}).get("status") not in {"PASS", "SKIPPED"}]
    return {
        "schema_version": "v5.backend_write_summary/0.1",
        "project_id": Path(project_dir).name,
        "write_count": len(rows),
        "minio_primary_write_count": len(primary),
        "minio_primary_pass_count": len(passed),
        "minio_primary_failure_count": len(failed),
        "latest_writes": rows[-20:],
    }


def _put_minio(project_dir: Path, rel: str, data: bytes) -> dict[str, Any]:
    env = local_backend_env(project_dir)
    key = env["TARGETCOMPASS_OBJECT_PREFIX"].rstrip("/") + "/" + rel
    try:
        _s3_request(
            "PUT",
            env["TARGETCOMPASS_MINIO_ENDPOINT"].rstrip("/"),
            f"{env['TARGETCOMPASS_OBJECT_BUCKET']}/{key}",
            env["TARGETCOMPASS_S3_ACCESS_KEY"],
            env["TARGETCOMPASS_S3_SECRET_KEY"],
            body=data,
            region=env["TARGETCOMPASS_S3_REGION"],
        )
    except Exception as exc:
        return {
            "status": "FAIL",
            "backend": "minio_local",
            "bucket": env["TARGETCOMPASS_OBJECT_BUCKET"],
            "object_key": key,
            "failure_reason": str(exc),
        }
    return {
        "status": "PASS",
        "backend": "minio_local",
        "bucket": env["TARGETCOMPASS_OBJECT_BUCKET"],
        "object_key": key,
        "uri": f"s3://{env['TARGETCOMPASS_OBJECT_BUCKET']}/{key}",
    }


def _load_active_backends(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "v5" / "active_backends.json"
    if not path.exists():
        return {"status": "FALLBACK", "active_backends": {"object_store": "local_filesystem"}, "source_ref": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "FALLBACK", "active_backends": {"object_store": "local_filesystem"}, "source_ref": "v5/active_backends.json"}
    payload.setdefault("active_backends", {})
    payload["source_ref"] = "v5/active_backends.json"
    return payload


def _append_backend_write(project_dir: Path, row: dict[str, Any]) -> None:
    path = _backend_writes_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _backend_writes_path(project_dir: Path) -> Path:
    return project_dir / "v5" / "storage" / "backend_writes.jsonl"
