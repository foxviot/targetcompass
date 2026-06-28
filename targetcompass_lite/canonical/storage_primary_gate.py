from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .backend_writer import backend_write_summary, write_json_artifact
from .backend_access import backend_status_summary
from .schemas import now_iso


PRIMARY_GATE_SCHEMA = "v5.storage_primary_gate/0.1"

LOCAL_ONLY_PATTERNS = [
    "evidence.sqlite",
    "v5/artifact_registry.jsonl",
    "reports/",
    "results/",
]


def build_storage_primary_gate(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    backend = backend_status_summary(project_dir)
    active = backend.get("active_backends", {})
    evidence_primary = active.get("evidence_db") == "postgres_local"
    object_primary = active.get("object_store") == "minio_local"
    local_writers = _detect_local_writers(project_dir)
    checks = [
        _check("postgres_active", evidence_primary, "PostgreSQL is active for v5 evidence reads.", "Run local-backends-check and v5-backends-activate."),
        _check("minio_active", object_primary, "MinIO is active for v5 artifact reads.", "Run local-backends-check, local-backends-sync, and v5-backends-activate."),
        _check(
            "backend_status_active",
            backend.get("status") == "ACTIVE",
            f"backend status: {backend.get('status')}",
            "v5/active_backends.json is missing or not ACTIVE.",
        ),
        _check(
            "legacy_local_writers_declared",
            True,
            f"{len(local_writers)} local writer artifact(s) detected and declared.",
            "",
            severity="warn",
        ),
    ]
    blocking = [row for row in checks if row["status"] == "FAIL"]
    warnings = [row for row in checks if row["status"] == "WARN"]
    payload = {
        "schema_version": PRIMARY_GATE_SCHEMA,
        "project_id": project_dir.name,
        "status": "BLOCKED" if blocking else ("READY_WITH_WARNINGS" if warnings or local_writers else "READY"),
        "backend_summary": backend,
        "primary_path": {
            "evidence_db": active.get("evidence_db", "sqlite_local"),
            "object_store": active.get("object_store", "local_filesystem"),
            "is_postgres_minio_primary": evidence_primary and object_primary and backend.get("status") == "ACTIVE",
        },
        "legacy_local_writers": local_writers,
        "policy": {
            "current_mode": "primary_with_legacy_writers" if local_writers else "primary_only",
            "require_sync_for_legacy_outputs": bool(local_writers),
            "note": "This gate is explicit because several mature v4/v5 modules still produce local files before backend sync.",
        },
        "backend_write_summary": backend_write_summary(project_dir),
        "checks": checks,
        "generated_at": now_iso(),
    }
    out = project_dir / "v5" / "storage" / "primary_path_gate.json"
    write_json_artifact(project_dir, out.relative_to(project_dir), payload, producer="storage_primary_gate", artifact_type="storage_gate")
    return payload


def _detect_local_writers(project_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    artifact_paths = _artifact_store_paths(project_dir)
    backend_paths = _backend_write_paths(project_dir)
    for pattern in LOCAL_ONLY_PATTERNS:
        path = project_dir / pattern
        if pattern.endswith("/"):
            exists = path.exists() and any(path.iterdir())
            kind = "directory"
        else:
            exists = path.exists()
            kind = "file"
        if not exists:
            continue
        coverage = _primary_coverage(project_dir, pattern, artifact_paths, backend_paths)
        if coverage["status"] == "covered":
            continue
        if exists:
            rows.append(
                {
                    "path": pattern,
                    "kind": kind,
                    "reason": coverage["reason"],
                    "required_backend_action": "sync_to_postgres_or_minio_and_reference_manifest",
                    "coverage": coverage,
                }
            )
    return rows


def _primary_coverage(project_dir: Path, pattern: str, artifact_paths: set[str], backend_paths: set[str]) -> dict[str, Any]:
    if pattern == "evidence.sqlite":
        if _postgres_replace_success(project_dir):
            return {"status": "covered", "reason": "SQLite fallback retained, but PostgreSQL EvidenceRepository has replace_all PASS."}
        return {"status": "gap", "reason": "SQLite fallback exists without a PostgreSQL EvidenceRepository replace_all PASS event."}
    if pattern == "v5/artifact_registry.jsonl":
        if pattern in backend_paths or pattern in artifact_paths:
            return {"status": "covered", "reason": "Artifact registry has a MinIO/backend write record."}
        return {"status": "gap", "reason": "Artifact registry exists but no backend write or ArtifactStore coverage was found."}
    if pattern in {"reports/", "results/"}:
        files = _files_under(project_dir, pattern)
        if not files:
            return {"status": "covered", "reason": "No files found under local output root."}
        covered = [rel for rel in files if rel in artifact_paths or rel in backend_paths]
        missing = [rel for rel in files if rel not in artifact_paths and rel not in backend_paths]
        ratio = len(covered) / max(len(files), 1)
        if not missing:
            return {"status": "covered", "reason": "All scanned files have ArtifactStore/backend coverage.", "file_count": len(files), "covered_count": len(covered)}
        return {
            "status": "gap",
            "reason": "Some local output files are not covered by ArtifactStore/backend write records.",
            "file_count": len(files),
            "covered_count": len(covered),
            "missing_count": len(missing),
            "coverage_ratio": round(ratio, 4),
            "sample_missing": missing[:20],
        }
    return {"status": "gap", "reason": "existing local output path still used by current modules"}


def _files_under(project_dir: Path, pattern: str) -> list[str]:
    root = project_dir / pattern
    if not root.exists():
        return []
    return [str(path.relative_to(project_dir)).replace("\\", "/") for path in root.rglob("*") if path.is_file()]


def _artifact_store_paths(project_dir: Path) -> set[str]:
    path = project_dir / "v5" / "object_store" / "artifact_store.jsonl"
    if not path.exists():
        return set()
    rows: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") in {"PASS", "WARN"} and row.get("primary_write", {}).get("status") in {"PASS", "SKIPPED"}:
            rows.add(str(row.get("relative_path", "")).replace("\\", "/"))
    return rows


def _backend_write_paths(project_dir: Path) -> set[str]:
    path = project_dir / "v5" / "storage" / "backend_writes.jsonl"
    if not path.exists():
        return set()
    rows: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("primary_backend") == "minio_local" and row.get("primary_write", {}).get("status") == "PASS":
            rows.add(str(row.get("relative_path", "")).replace("\\", "/"))
    return rows


def _postgres_replace_success(project_dir: Path) -> bool:
    events = project_dir / "v5" / "evidence_repository" / "repository_events.jsonl"
    if not events.exists():
        return False
    for line in reversed(events.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("backend") == "postgres_local" and row.get("operation") == "replace_all" and row.get("status") == "PASS":
            return True
    return False


def _check(check_id: str, ok: bool, message: str, remediation: str, *, severity: str = "fail") -> dict[str, Any]:
    status = "PASS" if ok else ("WARN" if severity == "warn" else "FAIL")
    return {"check_id": check_id, "status": status, "message": message if ok else remediation, "remediation": "" if ok else remediation}
