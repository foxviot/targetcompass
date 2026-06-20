import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, load_v4_work_orders, read_work_order_attempts, v4_dir


DAG_SCHEMA = "v4.work_order_dag/0.1"


def work_order_dag_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "work_order_dag.json"


def build_work_order_dag(project_dir: Path) -> dict[str, Any]:
    orders = load_v4_work_orders(project_dir)
    attempts = read_work_order_attempts(project_dir).get("attempts", [])
    latest_attempts = _latest_attempts(attempts)
    nodes = []
    for order in orders:
        node_id = order["work_order_id"]
        latest = latest_attempts.get(node_id, {})
        outputs = _outputs(project_dir, order, latest)
        node = {
            "schema_version": "v4.work_order_dag_node/0.1",
            "node_id": node_id,
            "work_order_id": order["work_order_id"],
            "module_id": order.get("module_id", ""),
            "module": order.get("module", ""),
            "dataset_id": order.get("dataset_id", ""),
            "node_type": order.get("work_order_type", ""),
            "status": _node_status(order, latest, outputs),
            "inputs": _inputs(order),
            "outputs": outputs,
            "qc_checks": order.get("qc_checks", []),
            "evidence_writes": _evidence_writes(project_dir, order, outputs),
            "latest_attempt": latest,
            "dependencies": _dependencies(order, orders),
            "resume_key": latest.get("resume_key", order.get("idempotency_key", "")),
        }
        nodes.append(node)
    edges = _edges(nodes)
    payload = {
        "schema_version": DAG_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "status_summary": _status_summary(nodes),
        "artifact_policy": {
            "inputs_are_declared_paths_or_parameters": True,
            "outputs_are_artifact_refs": True,
            "evidence_writes_reference_evidence_ids": True,
        },
    }
    path = work_order_dag_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def load_work_order_dag(project_dir: Path) -> dict[str, Any]:
    path = work_order_dag_path(project_dir)
    if not path.exists():
        return build_work_order_dag(project_dir)
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_attempts(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        work_order_id = attempt.get("work_order_id", "")
        if not work_order_id:
            continue
        previous = latest.get(work_order_id)
        if previous is None or attempt.get("started_at", "") >= previous.get("started_at", ""):
            latest[work_order_id] = attempt
    return latest


def _inputs(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "declared": order.get("inputs", {}),
        "parameters": order.get("parameters", {}),
        "lineage": order.get("lineage", {}),
    }


def _outputs(project_dir: Path, order: dict[str, Any], latest_attempt: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for artifact in latest_attempt.get("artifacts", []):
        refs.append(_artifact_ref(project_dir, artifact, source="attempt"))
    for artifact in order.get("expected_artifacts", []):
        if artifact not in {row["path"] for row in refs}:
            refs.append(_artifact_ref(project_dir, artifact, source="expected"))
    return refs


def _artifact_ref(project_dir: Path, relative_path: str, source: str) -> dict[str, Any]:
    path = project_dir / relative_path
    return {
        "path": relative_path,
        "source": source,
        "exists": path.exists(),
        "artifact_id": "artifact_" + content_hash({"path": relative_path, "exists": path.exists()})[:16],
    }


def _node_status(order: dict[str, Any], latest_attempt: dict[str, Any], outputs: list[dict[str, Any]]) -> str:
    if latest_attempt:
        status = latest_attempt.get("status", "")
        if status in {"success", "failed", "cancelled", "running"}:
            return status
    if order.get("requires_codex") and order.get("engineering_status"):
        return order.get("engineering_status", "")
    if outputs and all(row.get("exists") for row in outputs):
        return "artifacts_ready"
    return order.get("status", "compiled")


def _evidence_writes(project_dir: Path, order: dict[str, Any], outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    db = project_dir / "evidence.sqlite"
    if not db.exists():
        return []
    artifact_paths = [row["path"] for row in outputs]
    dataset_id = order.get("dataset_id", "")
    clauses = []
    params: list[Any] = []
    if artifact_paths:
        clauses.append("artifact_path IN (" + ",".join("?" for _ in artifact_paths) + ")")
        params.extend(artifact_paths)
    if dataset_id:
        clauses.append("source_dataset = ?")
        params.append(dataset_id)
    if not clauses:
        return []
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""
            SELECT evidence_id, entity_symbol, evidence_type, source_dataset, artifact_path, run_id, module_version
            FROM evidence_item
            WHERE {' OR '.join(clauses)}
            ORDER BY evidence_type, entity_symbol, evidence_id
            LIMIT 500
            """,
            params,
        ).fetchall()
        trace_index = _trace_index_by_evidence(project_dir)
        out = []
        for row in rows:
            payload = dict(row)
            trace = trace_index.get(payload.get("evidence_id", ""), {})
            payload["review_items"] = trace.get("review_items", [])
            payload["report_refs"] = trace.get("report_refs", [])
            out.append(payload)
        return out
    finally:
        con.close()


def _trace_index_by_evidence(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = v4_dir(project_dir) / "evidence_review_report_index.json"
    if not path.exists():
        return {}
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {row.get("evidence_id", ""): row for row in index.get("items", [])}


def _dependencies(order: dict[str, Any], orders: list[dict[str, Any]]) -> list[str]:
    deps = []
    inputs = json.dumps(order.get("inputs", {}), ensure_ascii=False)
    for candidate in orders:
        if candidate.get("work_order_id") == order.get("work_order_id"):
            continue
        for artifact in candidate.get("expected_artifacts", []):
            if artifact and artifact in inputs:
                deps.append(candidate["work_order_id"])
    return sorted(set(deps))


def _edges(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    edges = []
    for node in nodes:
        for dep in node.get("dependencies", []):
            edges.append({"from": dep, "to": node["node_id"], "edge_type": "artifact_dependency"})
    return edges


def _status_summary(nodes: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for node in nodes:
        status = node.get("status", "")
        summary[status] = summary.get(status, 0) + 1
    return summary
