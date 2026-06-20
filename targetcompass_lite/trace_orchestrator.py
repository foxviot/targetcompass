import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def refresh_traceability(project_dir: Path, include_review_queue: bool = True, include_work_order_dag: bool = True, include_evidence_index: bool = True) -> dict[str, Any]:
    outputs: dict[str, Any] = {
        "schema_version": "v4.traceability_refresh/0.1",
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "refreshed": {},
        "errors": [],
    }
    if include_review_queue:
        _run(outputs, "review_queue", lambda: _refresh_review_queue(project_dir))
    if include_work_order_dag:
        _run(outputs, "work_order_dag", lambda: _refresh_work_order_dag(project_dir))
    if include_evidence_index:
        _run(outputs, "evidence_review_report_index", lambda: _refresh_evidence_index(project_dir))
    path = project_dir / "v4" / "traceability_refresh.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(outputs, indent=2, ensure_ascii=False), encoding="utf-8")
    return outputs


def _run(outputs: dict[str, Any], name: str, fn) -> None:
    try:
        outputs["refreshed"][name] = fn()
    except Exception as exc:
        outputs["errors"].append({"component": name, "error": str(exc)})


def _refresh_review_queue(project_dir: Path) -> dict[str, Any]:
    from .review import build_review_queue

    queue = build_review_queue(project_dir, refresh_trace=False)
    return {"path": "results/review_queue.json", "queue_count": queue.get("queue_count", 0)}


def _refresh_work_order_dag(project_dir: Path) -> dict[str, Any]:
    from .work_order_dag import build_work_order_dag, work_order_dag_path

    dag = build_work_order_dag(project_dir)
    return {"path": str(work_order_dag_path(project_dir).relative_to(project_dir)).replace("\\", "/"), "node_count": dag.get("node_count", 0)}


def _refresh_evidence_index(project_dir: Path) -> dict[str, Any]:
    from .evidence_index import build_evidence_review_report_index, evidence_review_report_index_path

    index = build_evidence_review_report_index(project_dir)
    return {
        "path": str(evidence_review_report_index_path(project_dir).relative_to(project_dir)).replace("\\", "/"),
        "index_id": index.get("index_id", ""),
        "evidence_count": index.get("evidence_count", 0),
        "review_item_count": index.get("review_item_count", 0),
        "report_ref_count": index.get("report_ref_count", 0),
    }
