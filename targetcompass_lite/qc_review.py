import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_db import build_evidence_db_snapshot, migrate_evidence_db
from .review import record_review
from .task_registry import build_task_registry
from .v4 import content_hash, load_v4_work_orders, save_v4_work_order, v4_dir


QC_REVIEW_SCHEMA = "v4.qc_review_queue/0.1"
VALID_QC_ACTIONS = {"approve", "reject", "needs_review"}


def build_qc_review_queue(project_dir: Path) -> dict[str, Any]:
    registry = build_task_registry(project_dir)
    items = []
    for task in registry.get("tasks", []):
        if task.get("status") != "qc_review_required":
            continue
        refs = task.get("refs", {}) or {}
        evidence_summary = _evidence_summary(project_dir, task)
        items.append(
            {
                "item_type": "qc_gate",
                "item_id": task.get("work_order_id", "") or task.get("task_id", ""),
                "task_id": task.get("task_id", ""),
                "module_id": task.get("module_id", ""),
                "dataset_id": task.get("dataset_id", ""),
                "status": "pending",
                "qc_report": refs.get("qc_report", ""),
                "queue_result": refs.get("queue_result", ""),
                "decision": (task.get("qc_gate", {}) or {}).get("decision", ""),
                "reason": (task.get("qc_gate", {}) or {}).get("reason", ""),
                "evidence_import": (task.get("qc_gate", {}) or {}).get("evidence_import", ""),
                "evidence_summary": evidence_summary,
                "report_ref": f"reports/target_report.html#qc-gate-{task.get('module_id', '').lower().replace('_', '-')}",
            }
        )
    payload = {
        "schema_version": QC_REVIEW_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "queue_count": len(items),
        "items": items,
        "source_registry": "v4/task_registry.json",
    }
    out = qc_review_queue_path(project_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def apply_qc_review(
    project_dir: Path,
    work_order_id: str,
    action: str,
    reason: str,
    reviewer: str = "human",
    report_ref: str = "",
) -> dict[str, Any]:
    action = action.strip()
    if action not in VALID_QC_ACTIONS:
        raise ValueError(f"unsupported QC review action: {action}")
    if not reason.strip():
        raise ValueError("QC review reason is required")
    queue = build_qc_review_queue(project_dir)
    item = next((row for row in queue.get("items", []) if row.get("item_id") == work_order_id or row.get("task_id") == work_order_id or row.get("module_id") == work_order_id), None)
    if not item:
        raise ValueError(f"QC review item not found: {work_order_id}")
    review = record_review(
        project_dir,
        "qc_gate",
        item["item_id"],
        action,
        note=reason,
        reviewer=reviewer,
        reason=reason,
        report_ref=report_ref or item.get("report_ref", ""),
    )
    evidence_update = _update_evidence_review_status(project_dir, item, action, reason, review["review_id"])
    _update_work_order_qc_review(project_dir, item["item_id"], action, reason, reviewer, review["review_id"])
    refreshed = build_qc_review_queue(project_dir)
    downstream = refresh_downstream_after_qc_review(project_dir)
    return {
        "schema_version": "v4.qc_review_decision/0.1",
        "project_id": project_dir.name,
        "review": review,
        "qc_item": item,
        "evidence_update": evidence_update,
        "remaining_qc_review_count": refreshed.get("queue_count", 0),
        "downstream_refresh": downstream,
    }


def apply_qc_review_batch(
    project_dir: Path,
    work_order_ids: list[str],
    action: str,
    reason: str,
    reviewer: str = "human",
) -> dict[str, Any]:
    if not work_order_ids:
        raise ValueError("choose at least one QC review item")
    results = []
    errors = []
    for work_order_id in work_order_ids:
        try:
            results.append(
                apply_qc_review(
                    project_dir,
                    work_order_id,
                    action,
                    reason,
                    reviewer=reviewer,
                )
            )
        except Exception as exc:
            errors.append({"work_order_id": work_order_id, "error": str(exc)})
    downstream = refresh_downstream_after_qc_review(project_dir)
    return {
        "schema_version": "v4.qc_review_batch_decision/0.1",
        "project_id": project_dir.name,
        "action": action,
        "reviewed_count": len(results),
        "error_count": len(errors),
        "results": results,
        "errors": errors,
        "downstream_refresh": downstream,
    }


def refresh_downstream_after_qc_review(project_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "v4.qc_review_downstream_refresh/0.1",
        "project_id": project_dir.name,
        "generated_at": _now(),
        "refreshed": {},
        "errors": [],
    }
    _run_refresh(payload, "task_registry", lambda: build_task_registry(project_dir))
    _run_refresh(payload, "qc_review_queue", lambda: build_qc_review_queue(project_dir))
    _run_refresh(payload, "evidence_snapshot", lambda: build_evidence_db_snapshot(project_dir))
    _run_refresh(payload, "score", lambda: {"path": str(_score_project(project_dir).relative_to(project_dir)).replace("\\", "/")})
    _run_refresh(payload, "report", lambda: _report_paths(project_dir))
    _run_refresh(payload, "traceability", lambda: _refresh_trace(project_dir))
    out = v4_dir(project_dir) / "qc_review_downstream_refresh.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def qc_review_queue_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "qc_review_queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _update_evidence_review_status(project_dir: Path, item: dict[str, Any], action: str, reason: str, review_id: str) -> dict[str, Any]:
    migrate_evidence_db(project_dir)
    target_status = {
        "approve": "ACCEPT_WITH_FLAGS",
        "reject": "REJECTED_QC",
        "needs_review": "QC_REVIEW_REQUIRED",
    }[action]
    module_id = item.get("module_id", "")
    qc_report = item.get("qc_report", "")
    qc_artifacts = _qc_report_artifacts(project_dir, qc_report)
    con = sqlite3.connect(project_dir / "evidence.sqlite", timeout=30)
    try:
        before = con.execute(
            "SELECT review_status, COUNT(*) AS n FROM evidence_item WHERE review_status = 'QC_REVIEW_REQUIRED' GROUP BY review_status"
        ).fetchall()
        if qc_artifacts:
            placeholders = ",".join("?" for _ in qc_artifacts)
            cur = con.execute(
                f"""
                UPDATE evidence_item
                SET review_status = ?,
                    limitation = TRIM(COALESCE(limitation, '') || ?)
                WHERE review_status = 'QC_REVIEW_REQUIRED'
                  AND artifact_path IN ({placeholders})
                """,
                [target_status, _append_review_limitation_sql(reason, review_id), *qc_artifacts],
            )
        else:
            cur = con.execute(
                """
                UPDATE evidence_item
                SET review_status = ?,
                    limitation = TRIM(COALESCE(limitation, '') || ?)
                WHERE review_status = 'QC_REVIEW_REQUIRED'
                  AND (
                    module_version = ?
                    OR limitation LIKE ?
                  )
                """,
                [target_status, _append_review_limitation_sql(reason, review_id), module_id, f"%{qc_report}%"],
            )
        if cur.rowcount == 0 and module_id:
            cur = con.execute(
                """
                UPDATE evidence_item
                SET review_status = ?,
                    limitation = TRIM(COALESCE(limitation, '') || ?)
                WHERE review_status = 'QC_REVIEW_REQUIRED'
                  AND (artifact_path LIKE ? OR module_version LIKE ?)
                """,
                [target_status, _append_review_limitation_sql(reason, review_id), f"%{module_id}%", f"%{module_id}%"],
            )
        con.commit()
        updated = cur.rowcount
        after = con.execute("SELECT review_status, COUNT(*) AS n FROM evidence_item GROUP BY review_status").fetchall()
    finally:
        con.close()
    snapshot = build_evidence_db_snapshot(project_dir)
    return {
        "target_status": target_status,
        "updated_rows": int(updated or 0),
        "before_qc_review_required": {row[0]: row[1] for row in before},
        "after_by_review_status": {row[0]: row[1] for row in after},
        "snapshot_ref": "v4/evidence_db_snapshot.json",
        "snapshot_hash": snapshot.get("snapshot_hash", ""),
    }


def _update_work_order_qc_review(project_dir: Path, work_order_id: str, action: str, reason: str, reviewer: str, review_id: str) -> None:
    for order in load_v4_work_orders(project_dir):
        if order.get("work_order_id") != work_order_id:
            continue
        order["qc_review_status"] = action
        order["qc_review_reason"] = reason
        order["qc_reviewer"] = reviewer
        order["qc_review_id"] = review_id
        order["qc_reviewed_at"] = _now()
        if action == "approve" and order.get("status") == "compiled":
            order["status"] = "qc_review_approved"
        elif action == "reject":
            order["status"] = "qc_review_rejected"
        save_v4_work_order(project_dir, order)
        return


def _evidence_summary(project_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    module_id = task.get("module_id", "")
    qc_artifacts = _qc_report_artifacts(project_dir, (task.get("refs", {}) or {}).get("qc_report", ""))
    if not (project_dir / "evidence.sqlite").exists():
        return {"match_count": 0}
    con = sqlite3.connect(project_dir / "evidence.sqlite", timeout=30)
    try:
        if qc_artifacts:
            placeholders = ",".join("?" for _ in qc_artifacts)
            rows = con.execute(
                f"""
                SELECT review_status, COUNT(*) AS n
                FROM evidence_item
                WHERE artifact_path IN ({placeholders})
                GROUP BY review_status
                """,
                qc_artifacts,
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT review_status, COUNT(*) AS n
                FROM evidence_item
                WHERE module_version = ? OR artifact_path LIKE ?
                GROUP BY review_status
                """,
                [module_id, f"%{module_id}%"],
            ).fetchall()
    finally:
        con.close()
    return {
        "match_count": sum(row[1] for row in rows),
        "by_review_status": {row[0]: row[1] for row in rows},
    }


def _append_review_limitation_sql(reason: str, review_id: str) -> str:
    marker = f"; QC reviewed ({review_id}): {reason.strip()}"
    return marker


def _run_refresh(payload: dict[str, Any], name: str, fn) -> None:
    try:
        result = fn()
        if isinstance(result, dict):
            payload["refreshed"][name] = _compact_refresh_result(result)
        else:
            payload["refreshed"][name] = result
    except Exception as exc:
        payload["errors"].append({"component": name, "error": str(exc)})


def _score_project(project_dir: Path) -> Path:
    from .scoring import score_project

    return score_project(project_dir)


def _report_paths(project_dir: Path) -> dict[str, str]:
    from .reporting import build_report

    html_path, docx_path = build_report(project_dir)
    return {
        "html": str(html_path.relative_to(project_dir)).replace("\\", "/"),
        "docx": str(docx_path.relative_to(project_dir)).replace("\\", "/"),
    }


def _refresh_trace(project_dir: Path) -> dict[str, Any]:
    from .trace_orchestrator import refresh_traceability

    return refresh_traceability(project_dir, include_review_queue=True)


def _compact_refresh_result(result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "schema_version",
        "project_id",
        "task_count",
        "queue_count",
        "status_summary",
        "row_count",
        "snapshot_hash",
        "html",
        "docx",
        "refreshed",
        "errors",
    ]
    compact = {key: result[key] for key in keys if key in result}
    return compact or {"status": "done"}


def _qc_report_artifacts(project_dir: Path, qc_report: str) -> list[str]:
    if not qc_report:
        return []
    path = project_dir / qc_report
    if not path.exists():
        return []
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    artifacts = []
    for item in report.get("artifacts", []):
        if not item:
            continue
        text = str(item)
        artifacts.append(text)
        artifacts.append(text.replace("/", "\\"))
        artifacts.append(text.replace("\\", "/"))
    return sorted(set(artifacts))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
