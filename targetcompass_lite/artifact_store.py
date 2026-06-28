from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from .canonical.backend_access import load_v5_active_backends
from .local_backends import _s3_request, local_backend_env


ARTIFACT_STORE_SCHEMA = "v5.artifact_store_record/0.1"


def put_artifact(project_dir: Path, path: str | Path, *, producer: str, artifact_type: str) -> dict[str, Any]:
    rel, absolute = _resolve(project_dir, path)
    if not absolute.exists() or not absolute.is_file():
        record = _record(project_dir, rel, producer, artifact_type, status="MISSING_LOCAL", failure_reason="local file does not exist")
        _append_record(project_dir, record)
        return record
    checksum = _sha256(absolute)
    size = absolute.stat().st_size
    active = load_v5_active_backends(project_dir)
    object_backend = active.get("active_backends", {}).get("object_store", "local_filesystem")
    primary = {"status": "SKIPPED", "backend": object_backend, "reason": "object backend is local filesystem"}
    if object_backend == "minio_local":
        primary = _put_minio(project_dir, rel, absolute.read_bytes())
    record = _record(
        project_dir,
        rel,
        producer,
        artifact_type,
        status="PASS" if primary.get("status") in {"PASS", "SKIPPED"} else "WARN",
        checksum_sha256=checksum,
        size_bytes=size,
        object_backend=object_backend,
        object_uri=primary.get("uri", ""),
        object_key=primary.get("object_key", ""),
        bucket=primary.get("bucket", ""),
        primary_write=primary,
    )
    _append_record(project_dir, record)
    return record


def load_artifact_store(project_dir: Path) -> list[dict[str, Any]]:
    path = _registry_path(project_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_artifact(project_dir: Path, *, relative_path: str = "", artifact_store_id: str = "") -> dict[str, Any]:
    record = _find_record(project_dir, relative_path=relative_path, artifact_store_id=artifact_store_id)
    if not record:
        return {"status": "MISSING_RECORD", "reason": "artifact store record not found"}
    local_path = project_dir / record["relative_path"]
    local_ok = local_path.exists() and local_path.is_file() and _sha256(local_path) == record.get("checksum_sha256")
    object_status = "NOT_CHECKED"
    object_reason = ""
    if record.get("object_backend") == "minio_local" and record.get("object_key"):
        env = local_backend_env(project_dir)
        try:
            _s3_request("GET", env["TARGETCOMPASS_MINIO_ENDPOINT"].rstrip("/"), f"{record.get('bucket')}/{record.get('object_key')}", env["TARGETCOMPASS_S3_ACCESS_KEY"], env["TARGETCOMPASS_S3_SECRET_KEY"], region=env["TARGETCOMPASS_S3_REGION"])
            object_status = "PASS"
        except Exception as exc:
            object_status = "FAIL"
            object_reason = str(exc)
    return {
        "schema_version": "v5.artifact_store_verification/0.1",
        "project_id": project_dir.name,
        "artifact_store_id": record.get("artifact_store_id", ""),
        "relative_path": record.get("relative_path", ""),
        "local_cache_status": "PASS" if local_ok else "FAIL",
        "object_status": object_status,
        "object_reason": object_reason,
        "checksum_sha256": record.get("checksum_sha256", ""),
        "object_uri": record.get("object_uri", ""),
        "status": "PASS" if local_ok and object_status in {"PASS", "NOT_CHECKED"} else "RECOVERY_REQUIRED",
        "recovery": _recovery_advice(record, local_ok, object_status),
    }


def build_download_manifest(project_dir: Path, *, relative_path: str = "", artifact_store_id: str = "") -> dict[str, Any]:
    record = _find_record(project_dir, relative_path=relative_path, artifact_store_id=artifact_store_id)
    if not record:
        return {"status": "MISSING_RECORD", "reason": "artifact store record not found"}
    manifest = {
        "schema_version": "v5.artifact_download_manifest/0.1",
        "project_id": project_dir.name,
        "artifact_store_id": record.get("artifact_store_id", ""),
        "relative_path": record.get("relative_path", ""),
        "local_path": str((project_dir / record.get("relative_path", "")).resolve()),
        "object_uri": record.get("object_uri", ""),
        "checksum_sha256": record.get("checksum_sha256", ""),
        "download_modes": ["local_cache"] + (["object_store_uri"] if record.get("object_uri") else []),
        "signed_url": "",
        "signed_url_status": "not_available",
        "expires_at": "",
        "status": "READY" if record.get("status") in {"PASS", "WARN"} else "RECOVERY_REQUIRED",
    }
    if record.get("object_backend") == "minio_local" and record.get("bucket") and record.get("object_key"):
        signed = _presigned_get_url(project_dir, record["bucket"], record["object_key"])
        manifest["signed_url"] = signed["url"]
        manifest["signed_url_status"] = signed["status"]
        manifest["expires_at"] = signed["expires_at"]
    out = project_dir / "v5" / "object_store" / "last_download_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def artifact_store_summary(project_dir: Path) -> dict[str, Any]:
    rows = load_artifact_store(project_dir)
    return {
        "schema_version": "v5.artifact_store_summary/0.1",
        "project_id": project_dir.name,
        "artifact_store_count": len(rows),
        "object_uri_count": len([row for row in rows if row.get("object_uri")]),
        "failure_count": len([row for row in rows if row.get("status") not in {"PASS", "WARN"}]),
        "latest_records": rows[-20:],
    }


def _record(
    project_dir: Path,
    rel: str,
    producer: str,
    artifact_type: str,
    *,
    status: str,
    checksum_sha256: str = "",
    size_bytes: int = 0,
    object_backend: str = "local_filesystem",
    object_uri: str = "",
    object_key: str = "",
    bucket: str = "",
    primary_write: dict[str, Any] | None = None,
    failure_reason: str = "",
) -> dict[str, Any]:
    payload = {
        "project_id": project_dir.name,
        "relative_path": rel,
        "producer": producer,
        "artifact_type": artifact_type,
        "checksum_sha256": checksum_sha256,
        "size_bytes": size_bytes,
        "object_backend": object_backend,
        "object_uri": object_uri,
        "object_key": object_key,
        "bucket": bucket,
        "primary_write": primary_write or {},
        "status": status,
        "failure_reason": failure_reason,
    }
    payload["artifact_store_id"] = "astore_" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    payload["schema_version"] = ARTIFACT_STORE_SCHEMA
    return payload


def _put_minio(project_dir: Path, rel: str, data: bytes) -> dict[str, Any]:
    env = local_backend_env(project_dir)
    key = env["TARGETCOMPASS_OBJECT_PREFIX"].rstrip("/") + "/" + rel
    try:
        _s3_request("PUT", env["TARGETCOMPASS_MINIO_ENDPOINT"].rstrip("/"), f"{env['TARGETCOMPASS_OBJECT_BUCKET']}/{key}", env["TARGETCOMPASS_S3_ACCESS_KEY"], env["TARGETCOMPASS_S3_SECRET_KEY"], body=data, region=env["TARGETCOMPASS_S3_REGION"])
    except Exception as exc:
        return {"status": "FAIL", "backend": "minio_local", "bucket": env["TARGETCOMPASS_OBJECT_BUCKET"], "object_key": key, "failure_reason": str(exc)}
    return {"status": "PASS", "backend": "minio_local", "bucket": env["TARGETCOMPASS_OBJECT_BUCKET"], "object_key": key, "uri": f"s3://{env['TARGETCOMPASS_OBJECT_BUCKET']}/{key}"}


def _presigned_get_url(project_dir: Path, bucket: str, object_key: str, expires_seconds: int = 900) -> dict[str, str]:
    env = local_backend_env(project_dir)
    endpoint = env["TARGETCOMPASS_MINIO_ENDPOINT"].rstrip("/")
    region = env["TARGETCOMPASS_S3_REGION"]
    access_key = env["TARGETCOMPASS_S3_ACCESS_KEY"]
    secret_key = env["TARGETCOMPASS_S3_SECRET_KEY"]
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=expires_seconds)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    credential = f"{access_key}/{credential_scope}"
    path = f"/{bucket}/" + "/".join(quote(part, safe="") for part in object_key.split("/") if part)
    host = endpoint.split("://", 1)[-1]
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": credential,
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = urlencode(sorted(query.items()), quote_via=quote, safe="")
    canonical_request = "\n".join(["GET", path, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, credential_scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(_signing_key(secret_key, date_stamp, region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "status": "ready",
        "url": f"{endpoint}{path}?{canonical_query}&X-Amz-Signature={signature}",
        "expires_at": expires_at.isoformat(),
    }


def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def _append_record(project_dir: Path, record: dict[str, Any]) -> None:
    path = _registry_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _registry_path(project_dir: Path) -> Path:
    return project_dir / "v5" / "object_store" / "artifact_store.jsonl"


def _find_record(project_dir: Path, *, relative_path: str = "", artifact_store_id: str = "") -> dict[str, Any]:
    rows = load_artifact_store(project_dir)
    for row in reversed(rows):
        if artifact_store_id and row.get("artifact_store_id") == artifact_store_id:
            return row
        if relative_path and row.get("relative_path") == relative_path.replace("\\", "/"):
            return row
    return {}


def _recovery_advice(record: dict[str, Any], local_ok: bool, object_status: str) -> dict[str, Any]:
    if local_ok and object_status in {"PASS", "NOT_CHECKED"}:
        return {"required": False, "steps": []}
    steps = []
    if not local_ok:
        steps.append("restore local cache from object_uri or rerun the producing task")
    if object_status == "FAIL":
        steps.append("rerun artifact publication or check MinIO connectivity")
    return {"required": True, "steps": steps, "source_object_uri": record.get("object_uri", "")}


def _resolve(project_dir: Path, path: str | Path) -> tuple[str, Path]:
    candidate = Path(path)
    absolute = candidate if candidate.is_absolute() else project_dir / candidate
    try:
        rel = str(absolute.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        rel = str(path).replace("\\", "/")
    return rel, absolute


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
