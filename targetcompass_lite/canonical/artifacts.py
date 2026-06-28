from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any

from .backend_writer import write_bytes_artifact
from .ids import make_stable_id
from .schemas import CANONICAL_SCHEMA_VERSION, now_iso


ARTIFACT_REGISTRY_SCHEMA_VERSION = "v5.artifact_registry/0.1"
DEFAULT_CHECKSUM_CHUNK_BYTES = 1024 * 1024
DEFAULT_REGISTRY_WRITE_RETRIES = 5
DEFAULT_REGISTRY_RETRY_SLEEP_SECONDS = 0.08


def compute_file_sha256(path: str | Path) -> str:
    import hashlib

    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(DEFAULT_CHECKSUM_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_artifact_manifest(
    project_dir: str | Path,
    relative_path: str | Path,
    producer: str,
    artifact_type: str,
    expected_by_task_ids: list[str],
    supports_subquestion_ids: list[str],
    *,
    producer_run_id: str = "",
    schema_name: str = "",
    evidence_item_refs: list[str] | None = None,
    qc_status: str = "pending",
    limitations: list[str] | None = None,
    is_placeholder: bool = False,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    relative_path_text = str(relative_path).replace("\\", "/")
    artifact_path = project_dir / relative_path_text
    exists = artifact_path.exists() and artifact_path.is_file()
    checksum = compute_file_sha256(artifact_path) if exists else ""
    size_bytes = artifact_path.stat().st_size if exists else 0
    table_profile = _profile_table_artifact(artifact_path) if exists else {}
    artifact_id = make_stable_id(
        "artifact",
        {
            "project_id": project_dir.name,
            "path": relative_path_text,
            "checksum_sha256": checksum,
            "is_placeholder": is_placeholder,
            "artifact_type": artifact_type,
        },
    )
    manifest = {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "project_id": project_dir.name,
        "path": relative_path_text,
        "artifact_type": artifact_type,
        "producer_agent_or_task": producer,
        "producer_run_id": producer_run_id,
        "created_at": now_iso(),
        "checksum_sha256": checksum,
        "size_bytes": size_bytes,
        "exists": exists,
        "schema_name": schema_name or _infer_schema_name(relative_path_text, artifact_type),
        "expected_by_task_ids": expected_by_task_ids,
        "supports_subquestion_ids": supports_subquestion_ids,
        "evidence_item_refs": evidence_item_refs or [],
        "qc_status": qc_status,
        "limitations": limitations or [],
        "is_placeholder": is_placeholder,
    }
    manifest.update(table_profile)
    return manifest


def write_artifact_manifest(project_dir: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    project_dir = Path(project_dir)
    path = project_dir / "v5" / "artifact_registry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_jsonl_atomic_with_retry(path, manifest)
    write_bytes_artifact(
        project_dir,
        "v5/artifact_registry.jsonl",
        path.read_bytes(),
        producer="artifact_registry",
        artifact_type="artifact_registry_jsonl",
    )
    return manifest


def _append_jsonl_atomic_with_retry(path: Path, row: dict[str, Any], *, retries: int = DEFAULT_REGISTRY_WRITE_RETRIES) -> None:
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            _append_jsonl_atomic(path, line)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(DEFAULT_REGISTRY_RETRY_SLEEP_SECONDS * (attempt + 1))
    if last_error is not None:
        raise last_error


def _append_jsonl_atomic(path: Path, line: str) -> None:
    lock_path = path.with_suffix(path.suffix + ".lock")
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    lock_fd: int | None = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
        existing = path.read_bytes() if path.exists() else b""
        data = existing + line.encode("utf-8")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def load_artifact_registry(project_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(project_dir) / "v5" / "artifact_registry.jsonl"
    if not path.exists():
        return []
    manifests = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            manifests.append(json.loads(line))
    return manifests


def register_artifact(
    project_dir: str | Path,
    relative_path: str | Path,
    producer: str,
    artifact_type: str,
    expected_by_task_ids: list[str],
    supports_subquestion_ids: list[str],
    **kwargs: Any,
) -> dict[str, Any]:
    manifest = build_artifact_manifest(
        project_dir,
        relative_path,
        producer,
        artifact_type,
        expected_by_task_ids,
        supports_subquestion_ids,
        **kwargs,
    )
    if manifest.get("exists") is True:
        try:
            from targetcompass_lite.artifact_store import put_artifact

            store = put_artifact(Path(project_dir), relative_path, producer=producer, artifact_type=artifact_type)
            manifest["artifact_store_id"] = store.get("artifact_store_id", "")
            manifest["object_uri"] = store.get("object_uri", "")
            manifest["object_backend"] = store.get("object_backend", "")
            manifest["artifact_store_status"] = store.get("status", "")
        except Exception as exc:
            manifest["artifact_store_status"] = "WARN"
            manifest["artifact_store_failure_reason"] = str(exc)
    write_artifact_manifest(project_dir, manifest)
    return manifest


def validate_artifact_for_evidence(manifest: dict[str, Any]) -> list[str]:
    required = [
        "artifact_id",
        "project_id",
        "path",
        "artifact_type",
        "producer_agent_or_task",
        "producer_run_id",
        "created_at",
        "checksum_sha256",
        "size_bytes",
        "exists",
        "schema_name",
        "expected_by_task_ids",
        "supports_subquestion_ids",
        "evidence_item_refs",
        "qc_status",
        "limitations",
        "is_placeholder",
    ]
    errors = []
    for field in required:
        if field not in manifest or manifest[field] is None:
            errors.append(f"{field}: missing required field")
    if manifest.get("exists") is not True:
        errors.append("artifact cannot enter evidence synthesis when exists=false")
    if manifest.get("is_placeholder") is True:
        errors.append("artifact cannot enter evidence synthesis when is_placeholder=true")
    if not manifest.get("checksum_sha256"):
        errors.append("artifact requires checksum_sha256 based on file content")
    if manifest.get("qc_status") in {"fail", "failed", "rejected"}:
        errors.append(f"artifact cannot enter evidence synthesis when qc_status={manifest.get('qc_status')}")
    if not isinstance(manifest.get("expected_by_task_ids"), list):
        errors.append("expected_by_task_ids: expected list")
    if not isinstance(manifest.get("supports_subquestion_ids"), list):
        errors.append("supports_subquestion_ids: expected list")
    if not isinstance(manifest.get("evidence_item_refs"), list):
        errors.append("evidence_item_refs: expected list")
    if not isinstance(manifest.get("limitations"), list):
        errors.append("limitations: expected list")
    return errors


def _profile_table_artifact(path: Path, max_rows: int = 10000) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".tsv"}:
        return {}
    delimiter = "," if suffix == ".csv" else "\t"
    row_count = 0
    column_names: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            column_names = next(reader)
        except StopIteration:
            return {"row_count": 0, "column_names": []}
        for row_count, _ in enumerate(reader, start=1):
            if row_count >= max_rows:
                return {"row_count": row_count, "row_count_is_truncated": True, "column_names": column_names}
    return {"row_count": row_count, "row_count_is_truncated": False, "column_names": column_names}


def _infer_schema_name(relative_path: str, artifact_type: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    if suffix in {"csv", "tsv"}:
        return f"{artifact_type}_{suffix}_table"
    if suffix:
        return f"{artifact_type}_{suffix}"
    return artifact_type or "artifact"
