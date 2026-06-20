import json
from pathlib import Path
from typing import Any

from .run_state import read_status, status_path


STAGE_ORDER = ["generation", "initial_review", "verification", "execution", "final_review", "report"]


PARTIAL_ACTIONS = [
    {"id": "annotation", "label": "Recompute annotation", "description": "Rebuild accessibility, safety, and unknown-review tables."},
    {"id": "enrichment", "label": "Recompute enrichment", "description": "Rerun pathway/gene-set enrichment from DEG outputs."},
    {"id": "evidence", "label": "Reimport evidence", "description": "Rebuild evidence.sqlite from DEG, enrichment, and adapters."},
    {"id": "scoring", "label": "Recompute scoring", "description": "Recalculate candidate ranking from current evidence."},
    {"id": "report", "label": "Rebuild report", "description": "Regenerate HTML, docx, and structured report JSON."},
]


def build_status_center(project_dir: Path) -> dict[str, Any]:
    status = read_status(project_dir)
    latest_by_stage = _latest_stage_map(status.get("stages", []))
    stage_cards = []
    for stage in STAGE_ORDER:
        row = latest_by_stage.get(stage, {})
        stage_cards.append(
            {
                "name": stage,
                "status": row.get("status", "pending"),
                "message": row.get("message", ""),
                "purpose": row.get("details", {}).get("purpose", ""),
                "active": status.get("active_stage") == stage,
            }
        )
    geo_statuses = _latest_geo_statuses(project_dir)
    return {
        "run": status,
        "run_status_file": str(status_path(project_dir).relative_to(project_dir)) if status_path(project_dir).exists() else "",
        "stage_cards": stage_cards,
        "recovery": _recovery_items(project_dir, status, geo_statuses),
        "geo_statuses": geo_statuses,
        "partial_actions": PARTIAL_ACTIONS,
    }


def _latest_stage_map(stages: list[dict]) -> dict[str, dict]:
    latest = {}
    for row in stages:
        latest[row.get("name", "")] = row
    return latest


def _latest_geo_statuses(project_dir: Path) -> list[dict]:
    rows = []
    for path in sorted((project_dir / "data").glob("GSE*/geo_import_status.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        error = data.get("error", {})
        rows.append(
            {
                "accession": data.get("accession", path.parent.name),
                "status": data.get("status", ""),
                "stage": data.get("stage", error.get("stage", "")),
                "code": error.get("code", ""),
                "message": error.get("message", data.get("stage", "")),
                "recovery": error.get("recovery", []),
                "details": error.get("details", {}),
                "path": str(path.relative_to(project_dir)),
                "retryable": error.get("retryable", False),
                "updated_at": data.get("updated_at", ""),
                "recovery_actions": _geo_recovery_actions(data.get("accession", path.parent.name), data.get("status", ""), error),
            }
        )
    return rows[-5:]


def _geo_recovery_actions(accession: str, status: str, error: dict) -> list[dict[str, str]]:
    if status != "failed":
        return []
    code = error.get("code", "")
    details = error.get("details", {}) or {}
    actions = [
        {"label": "Retry auto import", "route": "/geo/import-auto", "mode": "auto", "accession": accession},
        {"label": "Manual grouping", "route": "/geo/import", "mode": "manual", "accession": accession},
    ]
    if error.get("retryable") or code in {"GEO_DOWNLOAD_HTTP_ERROR", "GEO_DOWNLOAD_NETWORK_ERROR", "GEO_IMPORT_UNEXPECTED_ERROR"}:
        actions.insert(0, {"label": "Force redownload", "route": "/geo/import-auto", "mode": "auto_force", "accession": accession})
    if code in {"GEO_AUTO_GROUPING_NO_CANDIDATE", "GEO_AUTO_GROUPING_LOW_CONFIDENCE", "GEO_GROUP_ASSIGNMENT_FAILED", "GEO_SAMPLE_SIZE_TOO_SMALL"}:
        actions.append({"label": "Use lower confidence", "route": "/geo/import-auto", "mode": "low_confidence", "accession": accession})
    if code == "GEO_PLATFORM_ANNOTATION_MISSING":
        actions.append(
            {
                "label": "Add platform annotation",
                "route": "/geo/import-auto",
                "mode": "platform_annotation",
                "accession": accession,
                "platform_annotation": details.get("platform_annotation", ""),
                "symbol_column": details.get("symbol_column", ""),
            }
        )
    return actions


def _recovery_items(project_dir: Path, status: dict, geo_statuses: list[dict]) -> list[dict]:
    items = []
    if status.get("failure_reason"):
        items.append(
            {
                "title": "Workflow failure",
                "message": status.get("failure_reason", ""),
                "actions": ["Use Rerun last request after checking the failed stage.", "Use a local recompute button if only one artifact is stale."],
                "status_file": str(status_path(project_dir).relative_to(project_dir)) if status_path(project_dir).exists() else "",
            }
        )
    for geo in geo_statuses:
        if geo.get("status") == "failed":
            items.append(
                {
                    "title": f"GEO import failed: {geo.get('accession', '')}",
                    "message": f"{geo.get('code', '')}: {geo.get('message', '')}",
                    "actions": geo.get("recovery", []),
                    "status_file": geo.get("path", ""),
                }
            )
    if not items and status.get("status") in {"idle", "success"}:
        items.append(
            {
                "title": "No blocking failure",
                "message": "Workflow artifacts are available. Use local recompute only when changing inputs or adapters.",
                "actions": ["Rerun last request to refresh the full workflow.", "Rebuild report after manual review changes."],
                "status_file": str(status_path(project_dir).relative_to(project_dir)) if status_path(project_dir).exists() else "",
            }
        )
    return items
