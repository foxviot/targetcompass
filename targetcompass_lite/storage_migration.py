from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import load_artifact_store, put_artifact
from .canonical.backend_access import load_v5_active_backends
from .canonical.backend_writer import write_json_artifact
from .evidence_repository import load_sqlite_evidence_rows, replace_evidence_rows


STORAGE_MIGRATION_PLAN_SCHEMA = "v5.storage_migration_plan/0.1"
STORAGE_MIGRATION_RUN_SCHEMA = "v5.storage_migration_run/0.1"
STORAGE_MIGRATION_HISTORY_SCHEMA = "v5.storage_migration_history/0.1"
DEMO_SLIM_STORAGE_SCHEMA = "v5.demo_slim_storage_manifest/0.1"

DEMO_EFFECTIVE_RESULT_DIRS = {
    "annotation",
    "bulk_deg_GSE312006",
    "bulk_deg_GSE43292",
    "causal_evidence",
    "cell_type_evidence",
    "database_validation",
    "enrichment",
    "evidence_import",
    "evidence_planning",
    "experiments",
    "meta_analysis",
    "qc",
    "sasp_score",
    "scoring",
}
DEMO_EFFECTIVE_RESULT_FILES = {"agent_trace.json", "review_queue.json", "run_status.json"}
DEMO_EFFECTIVE_V5_DIRS = {
    "analysis_main_path",
    "delivery",
    "doctor",
    "evidence_repository",
    "local_demo",
    "local_execution",
    "memory_palace",
    "nextflow",
    "platform",
    "qc_reports",
    "recovery",
    "reports",
    "resource_discovery",
    "task_packets",
    "task_runs",
    "wet_lab_protocols",
}


def build_storage_migration_plan(project_dir: str | Path, *, roots: tuple[str, ...] = ("results", "reports")) -> dict[str, Any]:
    project_dir = Path(project_dir)
    active = load_v5_active_backends(project_dir)
    registered = {row.get("relative_path", "") for row in load_artifact_store(project_dir)}
    candidates = _legacy_file_candidates(project_dir, roots)
    missing = [row for row in candidates if row["relative_path"] not in registered]
    sqlite_rows = load_sqlite_evidence_rows(project_dir)
    plan = {
        "schema_version": STORAGE_MIGRATION_PLAN_SCHEMA,
        "project_id": project_dir.name,
        "active_backends": active.get("active_backends", {}),
        "active_status": active.get("status", "FALLBACK"),
        "candidate_file_count": len(candidates),
        "artifact_store_registered_count": len(candidates) - len(missing),
        "artifact_store_missing_count": len(missing),
        "missing_artifacts": missing[:500],
        "sqlite_evidence_row_count": len(sqlite_rows),
        "migration_progress": _migration_progress(candidates, missing),
        "primary_targets": {
            "evidence_db": active.get("active_backends", {}).get("evidence_db", "sqlite_local"),
            "object_store": active.get("active_backends", {}).get("object_store", "local_filesystem"),
        },
        "actions": _plan_actions(project_dir, active, missing, sqlite_rows),
        "primary_path_gaps": _primary_path_gaps(project_dir, active, missing, sqlite_rows),
        "history_summary": _history_summary(load_storage_migration_history(project_dir)),
        "status": _plan_status(active, missing),
        "generated_at": _now(),
    }
    write_json_artifact(project_dir, "v5/platform/storage_migration_plan.json", plan, producer="storage_migration", artifact_type="storage_migration_plan")
    return plan


def load_storage_migration_plan(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    path = project_dir / "v5" / "platform" / "storage_migration_plan.json"
    if not path.exists():
        return build_storage_migration_plan(project_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return build_storage_migration_plan(project_dir)
    payload["cache_policy"] = {
        "mode": "cached",
        "source_ref": "v5/platform/storage_migration_plan.json",
        "refresh_command": f"python tc_lite.py v5-storage-migration --project {project_dir.name} --action plan",
    }
    return payload


def migrate_legacy_outputs_to_primary_backends(
    project_dir: str | Path,
    *,
    roots: tuple[str, ...] = ("results", "reports"),
    limit: int = 500,
    sync_evidence: bool = True,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    plan = build_storage_migration_plan(project_dir, roots=roots)
    registered = {row.get("relative_path", "") for row in load_artifact_store(project_dir)}
    all_candidates = _legacy_file_candidates(project_dir, roots)
    full_missing = [row for row in all_candidates if row["relative_path"] not in registered]
    migrated = []
    failed = []
    for row in full_missing[: max(1, limit)]:
        try:
            record = put_artifact(project_dir, row["relative_path"], producer="storage_migration", artifact_type=_artifact_type(row["relative_path"]))
            migrated.append(record)
        except Exception as exc:
            failed.append({"relative_path": row["relative_path"], "failure_reason": str(exc)})
    evidence_result = {"status": "SKIPPED", "reason": "sync_evidence is false"}
    if sync_evidence:
        evidence_result = replace_evidence_rows(project_dir, load_sqlite_evidence_rows(project_dir))
    run = {
        "schema_version": STORAGE_MIGRATION_RUN_SCHEMA,
        "project_id": project_dir.name,
        "plan_ref": "v5/platform/storage_migration_plan.json",
        "requested_limit": limit,
        "full_missing_before_count": len(full_missing),
        "migrated_artifact_count": len(migrated),
        "failed_artifact_count": len(failed),
        "migrated_artifacts": migrated[-100:],
        "failed_artifacts": failed,
        "evidence_repository_result": evidence_result,
        "status": "PASS" if not failed and evidence_result.get("status") in {"PASS", "SKIPPED"} else "REVIEW_REQUIRED",
        "generated_at": _now(),
    }
    write_json_artifact(project_dir, "v5/platform/storage_migration_last_run.json", run, producer="storage_migration", artifact_type="storage_migration_run")
    _append_storage_migration_history(project_dir, run)
    build_storage_migration_plan(project_dir, roots=roots)
    return run


def build_demo_slim_storage_manifest(
    project_dir: str | Path,
    *,
    migrate: bool = True,
    limit: int = 5000,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    registered = {row.get("relative_path", "") for row in load_artifact_store(project_dir)}
    effective = _demo_effective_file_candidates(project_dir)
    missing = [row for row in effective if row["relative_path"] not in registered]
    migrated = []
    failed = []
    if migrate:
        for row in missing[: max(1, limit)]:
            try:
                migrated.append(put_artifact(project_dir, row["relative_path"], producer="demo_slim_storage", artifact_type=row["artifact_type"]))
            except Exception as exc:
                failed.append({"relative_path": row["relative_path"], "failure_reason": str(exc)})
        registered = {row.get("relative_path", "") for row in load_artifact_store(project_dir)}
    remaining = [row for row in effective if row["relative_path"] not in registered]
    all_legacy = _legacy_file_candidates(project_dir, ("results", "reports"))
    effective_paths = {row["relative_path"] for row in effective}
    excluded = [row for row in all_legacy if row["relative_path"] not in effective_paths]
    payload = {
        "schema_version": DEMO_SLIM_STORAGE_SCHEMA,
        "project_id": project_dir.name,
        "strategy": "professor_demo_effective_artifacts_only",
        "status": "PASS" if not remaining and not failed else "REVIEW",
        "effective_artifact_count": len(effective),
        "effective_registered_count": len(effective) - len(remaining),
        "effective_missing_count": len(remaining),
        "migrated_artifact_count": len(migrated),
        "failed_artifact_count": len(failed),
        "excluded_historical_legacy_count": len(excluded),
        "excluded_policy": {
            "meaning": "Historical debug, batch validation, cache, and raw intermediate outputs are kept in the development workspace but excluded from the slim professor demo primary-path claim.",
            "full_migration_command": f"python tc_lite.py v5-storage-migration --project {project_dir.name} --action migrate --limit 5000",
        },
        "effective_roots": {
            "reports": "all report files",
            "results": sorted(DEMO_EFFECTIVE_RESULT_DIRS | DEMO_EFFECTIVE_RESULT_FILES),
            "v5": sorted(DEMO_EFFECTIVE_V5_DIRS),
            "project_files": ["candidate_scores.csv", "evidence.sqlite"],
        },
        "remaining_effective_artifacts": remaining[:200],
        "failed_artifacts": failed,
        "sample_excluded_historical_artifacts": excluded[:50],
        "manifest_ref": "v5/platform/demo_slim_storage_manifest.json",
        "generated_at": _now(),
    }
    write_json_artifact(project_dir, "v5/platform/demo_slim_storage_manifest.json", payload, producer="demo_slim_storage", artifact_type="demo_slim_storage_manifest")
    return payload


def load_storage_migration_history(project_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    project_dir = Path(project_dir)
    path = project_dir / "v5" / "platform" / "storage_migration_history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-max(1, limit) :]


def load_demo_slim_storage_manifest(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    path = project_dir / "v5" / "platform" / "demo_slim_storage_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _legacy_file_candidates(project_dir: Path, roots: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for root_name in roots:
        root = project_dir / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(project_dir)).replace("\\", "/")
            rows.append({"relative_path": rel, "size_bytes": path.stat().st_size, "root": root_name, "artifact_type": _artifact_type(rel)})
    if (project_dir / "evidence.sqlite").exists():
        rows.append({"relative_path": "evidence.sqlite", "size_bytes": (project_dir / "evidence.sqlite").stat().st_size, "root": "project", "artifact_type": "sqlite_evidence_fallback"})
    return rows


def _demo_effective_file_candidates(project_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel in ["candidate_scores.csv", "evidence.sqlite"]:
        path = project_dir / rel
        if path.exists() and path.is_file():
            rows.append({"relative_path": rel, "size_bytes": path.stat().st_size, "root": "project", "artifact_type": _artifact_type(rel), "reason": "project_summary_or_evidence"})
    reports = project_dir / "reports"
    if reports.exists():
        rows.extend(_files_under(project_dir, reports, "reports", "formal_report_output"))
    results = project_dir / "results"
    if results.exists():
        for name in DEMO_EFFECTIVE_RESULT_FILES:
            path = results / name
            if path.exists() and path.is_file():
                rows.append({"relative_path": str(path.relative_to(project_dir)).replace("\\", "/"), "size_bytes": path.stat().st_size, "root": "results", "artifact_type": _artifact_type(name), "reason": "runtime_status"})
        for dirname in sorted(DEMO_EFFECTIVE_RESULT_DIRS):
            path = results / dirname
            if path.exists() and path.is_dir():
                if dirname == "meta_analysis":
                    rows.extend(_files_under(project_dir, path, "results", f"effective_result_dir:{dirname}", recursive=False))
                else:
                    rows.extend(_files_under(project_dir, path, "results", f"effective_result_dir:{dirname}"))
    v5 = project_dir / "v5"
    if v5.exists():
        for dirname in sorted(DEMO_EFFECTIVE_V5_DIRS):
            path = v5 / dirname
            if path.exists() and path.is_dir():
                rows.extend(_files_under(project_dir, path, "v5", f"v5_control_plane:{dirname}"))
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[row["relative_path"]] = row
    return [deduped[key] for key in sorted(deduped)]


def _files_under(project_dir: Path, root: Path, root_name: str, reason: str, *, recursive: bool = True) -> list[dict[str, Any]]:
    rows = []
    iterator = root.rglob("*") if recursive else root.glob("*")
    for path in sorted(iterator):
        if not path.is_file():
            continue
        rel = str(path.relative_to(project_dir)).replace("\\", "/")
        rows.append({"relative_path": rel, "size_bytes": path.stat().st_size, "root": root_name, "artifact_type": _artifact_type(rel), "reason": reason})
    return rows


def _plan_actions(project_dir: Path, active: dict[str, Any], missing: list[dict[str, Any]], sqlite_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions = []
    if active.get("active_backends", {}).get("object_store") != "minio_local":
        actions.append({"priority": "P0", "action": "Activate MinIO object_store before treating ArtifactStore as primary."})
    if active.get("active_backends", {}).get("evidence_db") != "postgres_local":
        actions.append({"priority": "P0", "action": "Activate PostgreSQL evidence_db before treating EvidenceRepository as primary."})
    if missing:
        actions.append({"priority": "P1", "action": f"Register/upload {len(missing)} legacy output file(s) through ArtifactStore."})
    if sqlite_rows and not _postgres_replace_success(project_dir, active):
        actions.append({"priority": "P1", "action": f"Write {len(sqlite_rows)} SQLite evidence row(s) through EvidenceRepository primary path."})
    if not actions:
        actions.append({"priority": "P3", "action": "No migration action required for scanned roots."})
    return actions


def _primary_path_gaps(project_dir: Path, active: dict[str, Any], missing: list[dict[str, Any]], sqlite_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps = []
    if active.get("active_backends", {}).get("object_store") != "minio_local":
        gaps.append({"gap_id": "object_store_not_primary", "severity": "P0", "meaning": "Artifacts cannot be treated as object-store primary until MinIO/S3 is active."})
    if active.get("active_backends", {}).get("evidence_db") != "postgres_local":
        gaps.append({"gap_id": "evidence_repository_not_primary", "severity": "P0", "meaning": "Evidence cannot be treated as PostgreSQL primary until postgres_local is active."})
    if missing:
        by_root: dict[str, int] = {}
        for row in missing:
            by_root[row.get("root", "unknown")] = by_root.get(row.get("root", "unknown"), 0) + 1
        gaps.append(
            {
                "gap_id": "legacy_artifacts_not_registered",
                "severity": "P1",
                "meaning": "Some mature local outputs still need ArtifactStore registration/upload.",
                "remaining_by_root": by_root,
            }
        )
    if sqlite_rows and not _postgres_replace_success(project_dir, active):
        gaps.append(
            {
                "gap_id": "sqlite_evidence_fallback_present",
                "severity": "P1",
                "meaning": "SQLite fallback evidence exists; EvidenceRepository should be the read/write interface and PostgreSQL primary when active.",
                "row_count": len(sqlite_rows),
            }
        )
    elif sqlite_rows:
        gaps.append(
            {
                "gap_id": "sqlite_fallback_retained",
                "severity": "P3",
                "meaning": "SQLite fallback file is retained as local backup; PostgreSQL EvidenceRepository has a successful primary-path replace_all record.",
                "row_count": len(sqlite_rows),
            }
        )
    if not gaps:
        gaps.append({"gap_id": "no_primary_path_gap_in_scanned_roots", "severity": "P3", "meaning": "Scanned roots are already registered against the active primary backends."})
    return gaps


def _postgres_replace_success(project_dir: Path, active: dict[str, Any]) -> bool:
    if active.get("active_backends", {}).get("evidence_db") != "postgres_local":
        return False
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


def _migration_progress(candidates: list[dict[str, Any]], missing: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(candidates)
    remaining = len(missing)
    migrated = max(0, total - remaining)
    percent = round((migrated / total) * 100, 2) if total else 100.0
    return {"total": total, "migrated_or_registered": migrated, "remaining": remaining, "percent_complete": percent}


def _append_storage_migration_history(project_dir: Path, run: dict[str, Any]) -> None:
    entry = {
        "schema_version": STORAGE_MIGRATION_HISTORY_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": run.get("generated_at", _now()),
        "status": run.get("status", ""),
        "migrated_artifact_count": run.get("migrated_artifact_count", 0),
        "failed_artifact_count": run.get("failed_artifact_count", 0),
        "evidence_repository_status": (run.get("evidence_repository_result", {}) or {}).get("status", ""),
        "run_ref": "v5/platform/storage_migration_last_run.json",
    }
    path = project_dir / "v5" / "platform" / "storage_migration_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _history_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "history_ref": "v5/platform/storage_migration_history.jsonl",
        "batch_count": len(history),
        "total_migrated_artifacts": sum(int(row.get("migrated_artifact_count", 0) or 0) for row in history),
        "total_failed_artifacts": sum(int(row.get("failed_artifact_count", 0) or 0) for row in history),
        "last_status": history[-1].get("status", "") if history else "not_run",
        "recent_batches": history[-10:],
    }


def _plan_status(active: dict[str, Any], missing: list[dict[str, Any]]) -> str:
    if active.get("active_backends", {}).get("object_store") != "minio_local" or active.get("active_backends", {}).get("evidence_db") != "postgres_local":
        return "BACKEND_NOT_PRIMARY"
    if missing:
        return "MIGRATION_REQUIRED"
    return "PRIMARY_READY"


def _artifact_type(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".html") or lowered.endswith(".docx") or "report" in lowered:
        return "report_artifact"
    if lowered.endswith(".json"):
        return "analysis_manifest"
    if lowered.endswith(".tsv") or lowered.endswith(".csv"):
        return "analysis_table"
    if lowered.endswith(".sqlite"):
        return "sqlite_evidence_fallback"
    return "legacy_output"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
