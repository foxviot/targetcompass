import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database_validation import validate_online_databases
from .v4 import content_hash


RECOVERY_SCHEMA = "v4.recovery_manifest/0.1"


def build_recovery_manifest(project_dir: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    items.extend(_database_items(project_dir))
    items.extend(_geo_items(project_dir))
    items.extend(_fulltext_items(project_dir))
    items.extend(_fulltext_llm_items(project_dir))
    items.extend(_orchestrator_items(project_dir))
    payload = {
        "schema_version": RECOVERY_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "item_count": len(items),
        "open_count": len([row for row in items if row.get("status") in {"failed", "empty", "requires_credentials", "needs_manual_input"}]),
        "retryable_count": len([row for row in items if row.get("retryable")]),
        "items": items,
    }
    payload["manifest_hash"] = content_hash(payload)
    out = _out_dir(project_dir) / "recovery_manifest.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def retry_database_sources(
    project_dir: Path,
    sources: list[str] | None = None,
    genes: list[str] | None = None,
    query: str = "type 2 diabetes skeletal muscle",
    limit: int = 10,
    timeout: int = 30,
    adapt: bool = True,
) -> dict[str, Any]:
    before = _read_json(project_dir / "results" / "database_validation" / "online_database_validation.json", {})
    requested = [source.strip().lower() for source in (sources or []) if source.strip()]
    result = validate_online_databases(project_dir, genes=genes, query=query, limit=limit, timeout=timeout, adapt=adapt)
    selected = result.get("sources", [])
    if requested:
        selected = [row for row in selected if row.get("source_id", "").lower() in requested]
    payload = {
        "schema_version": "v4.database_retry/0.1",
        "project_id": project_dir.name,
        "requested_sources": requested or "all",
        "before": _source_status_map(before),
        "after": {row.get("source_id", ""): {"status": row.get("status", ""), "message": row.get("message", "")} for row in selected},
        "retry_count": len(selected),
        "generated_at": _now(),
    }
    out = _out_dir(project_dir) / "database_retry_manifest.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    build_recovery_manifest(project_dir)
    return payload


def _database_items(project_dir: Path) -> list[dict[str, Any]]:
    data = _read_json(project_dir / "results" / "database_validation" / "online_database_validation.json", {})
    items = []
    for source in data.get("sources", []) if isinstance(data, dict) else []:
        status = source.get("status", "")
        if status == "success":
            continue
        source_id = source.get("source_id", "database")
        retryable = status in {"failed", "empty"}
        items.append(
            _item(
                item_id=f"database:{source_id}",
                stage="database_validation",
                status=status or "failed",
                reason=source.get("message", "") or f"{source_id} did not return usable rows",
                retryable=retryable,
                suggested_action=(
                    "Retry with a longer timeout or smaller gene list."
                    if retryable
                    else "Provide credentials or upload a licensed/local database file through Knowledge registry."
                ),
                command=f"python tc_lite.py database-retry --project {project_dir.name} --source {source_id}",
                ui_route="/",
                source_artifact="results/database_validation/online_database_validation.json",
                manual_correction="Upload a local TSV/CSV resource and select the matching adapter." if status == "requires_credentials" else "",
            )
        )
    return items


def _geo_items(project_dir: Path) -> list[dict[str, Any]]:
    items = []
    for path in sorted((project_dir / "results" / "geo_import").glob("**/*status*.json")):
        data = _read_json(path, {})
        status = data.get("status", "")
        if status in {"success", "imported"}:
            continue
        accession = data.get("accession") or path.stem.replace("_status", "")
        reason = data.get("reason") or data.get("message") or data.get("error", {}).get("message", "") or data.get("error", "")
        recovery = data.get("recovery") or data.get("error", {}).get("recovery", {})
        actions = recovery.get("actions", []) if isinstance(recovery, dict) else []
        action_note = "; ".join(str(row.get("label", row)) for row in actions[:3] if isinstance(row, dict))
        items.append(
            _item(
                item_id=f"geo:{accession}",
                stage="geo_import",
                status=status or "failed",
                reason=str(reason or "GEO import did not complete"),
                retryable=True,
                suggested_action=action_note or "Retry auto import; if grouping failed, choose case/control patterns manually.",
                command=f"python tc_lite.py geo-import-auto --project {project_dir.name} --accession {accession}",
                ui_route="/",
                source_artifact=_rel(path, project_dir),
                manual_correction="Use GEO / GSE recovery center and fill case/control labels and keyword patterns.",
            )
        )
    return items


def _fulltext_items(project_dir: Path) -> list[dict[str, Any]]:
    data = _read_json(project_dir / "results" / "fulltext_literature" / "fulltext_literature_run.json", {})
    items = []
    for failure in data.get("failures", []) if isinstance(data, dict) else []:
        source = failure.get("source", "") or failure.get("pmid", "") or "fulltext"
        reason = failure.get("reason", "") or failure.get("error", "") or "full text was not parsed"
        items.append(
            _item(
                item_id=f"fulltext:{source}",
                stage="fulltext_literature",
                status="failed",
                reason=reason,
                retryable=True,
                suggested_action="Upload the article PDF and enable OCR if it is scanned.",
                command=f"python tc_lite.py fulltext-literature --project {project_dir.name} --pdf D:/path/to/article.pdf --ocr",
                ui_route="/",
                source_artifact="results/fulltext_literature/fulltext_literature_run.json",
                manual_correction="Upload PDF in the Full-text correction form, then run LLM extraction.",
            )
        )
    return items


def _fulltext_llm_items(project_dir: Path) -> list[dict[str, Any]]:
    data = _read_json(project_dir / "results" / "fulltext_literature" / "llm_extraction" / "fulltext_llm_extraction_run.json", {})
    if not data or data.get("failure_count", 0) in ("", 0):
        return []
    return [
        _item(
            item_id="fulltext_llm:extraction",
            stage="fulltext_llm_extraction",
            status="failed",
            reason="One or more full-text chunks failed LLM structured extraction.",
            retryable=True,
            suggested_action="Check LLM key/provider, reduce max chars, and rerun extraction.",
            command=f"python tc_lite.py fulltext-llm-extract --project {project_dir.name} --max-docs 5",
            ui_route="/",
            source_artifact="results/fulltext_literature/llm_extraction/fulltext_llm_extraction_run.json",
            manual_correction="Use a smaller document batch or upload cleaner PDF/text.",
        )
    ]


def _orchestrator_items(project_dir: Path) -> list[dict[str, Any]]:
    data = _read_json(project_dir / "v4" / "orchestrator_runs.json", {})
    runs = data.get("runs", []) if isinstance(data, dict) else []
    items = []
    for run in runs[-20:]:
        if run.get("status") not in {"failed", "cancelled"}:
            continue
        items.append(
            _item(
                item_id=f"orchestrator:{run.get('run_id', '')}",
                stage="orchestrator",
                status=run.get("status", "failed"),
                reason=run.get("failure_reason", "") or run.get("message", "") or "run did not complete",
                retryable=run.get("status") == "failed",
                suggested_action="Resume the run or rerun the failed DAG node.",
                command=f"python tc_lite.py orchestrator-resume --project {project_dir.name} --run-id {run.get('run_id', '')}",
                ui_route="/",
                source_artifact="v4/orchestrator_runs.json",
                manual_correction="Open Orchestrator panel and choose resume or partial rerun.",
            )
        )
    return items


def _item(
    item_id: str,
    stage: str,
    status: str,
    reason: str,
    retryable: bool,
    suggested_action: str,
    command: str,
    ui_route: str,
    source_artifact: str,
    manual_correction: str = "",
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "stage": stage,
        "status": status,
        "reason": reason,
        "retryable": retryable,
        "suggested_action": suggested_action,
        "manual_correction": manual_correction,
        "command": command,
        "ui_route": ui_route,
        "source_artifact": source_artifact,
    }


def _source_status_map(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {row.get("source_id", ""): {"status": row.get("status", ""), "message": row.get("message", "")} for row in data.get("sources", [])}


def _out_dir(project_dir: Path) -> Path:
    path = project_dir / "results" / "recovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
