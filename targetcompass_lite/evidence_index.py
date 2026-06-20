import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, v4_dir


def build_evidence_review_report_index(project_dir: Path) -> dict[str, Any]:
    evidence_rows = _evidence_rows(project_dir)
    review_items = _review_items(project_dir)
    report_refs = _report_refs(project_dir)
    items = []
    for evidence in evidence_rows:
        gene = evidence.get("entity_symbol", "")
        evidence_id = evidence.get("evidence_id", "")
        related_reviews = _related_reviews(evidence, review_items)
        related_reports = _related_reports(gene, evidence_id, report_refs)
        items.append(
            {
                "evidence_id": evidence_id,
                "entity_symbol": gene,
                "evidence_type": evidence.get("evidence_type", ""),
                "source_dataset": evidence.get("source_dataset", ""),
                "artifact_path": _posix(evidence.get("artifact_path", "")),
                "artifact_id": evidence.get("artifact_id", ""),
                "run_id": evidence.get("run_id", ""),
                "module_version": evidence.get("module_version", ""),
                "review_status": evidence.get("review_status", ""),
                "review_items": related_reviews,
                "report_refs": related_reports,
            }
        )
    payload = {
        "schema_version": "v4.evidence_review_report_index/0.1",
        "index_id": "eri_" + content_hash({"project": project_dir.name, "items": items})[:16],
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_count": len(evidence_rows),
        "review_item_count": len(review_items),
        "report_ref_count": len(report_refs),
        "items": items,
    }
    path = evidence_review_report_index_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def evidence_review_report_index_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "evidence_review_report_index.json"


def query_evidence_trace(project_dir: Path, gene: str = "", evidence_id: str = "", review_status: str = "") -> dict[str, Any]:
    path = evidence_review_report_index_path(project_dir)
    index = _read_json(path, {})
    if not index:
        index = build_evidence_review_report_index(project_dir)
    gene_l = gene.lower().strip()
    evidence_id_l = evidence_id.lower().strip()
    review_status_l = review_status.lower().strip()
    matches = []
    for row in index.get("items", []):
        if gene_l and gene_l not in str(row.get("entity_symbol", "")).lower():
            continue
        if evidence_id_l and evidence_id_l not in str(row.get("evidence_id", "")).lower():
            continue
        if review_status_l and review_status_l not in str(row.get("review_status", "")).lower():
            continue
        matches.append(row)
    return {
        "schema_version": "v4.evidence_trace_query/0.1",
        "project_id": project_dir.name,
        "query": {"gene": gene, "evidence_id": evidence_id, "review_status": review_status},
        "match_count": len(matches),
        "items": matches,
    }


def _evidence_rows(project_dir: Path) -> list[dict[str, Any]]:
    db = project_dir / "evidence.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(evidence_item)").fetchall()}
        if "evidence_id" not in columns:
            return []
        rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT evidence_id, entity_symbol, evidence_type, source_dataset, artifact_path,
                       artifact_id, run_id, module_version, review_status
                FROM evidence_item
                ORDER BY entity_symbol, evidence_type, evidence_id
                """
            ).fetchall()
        ]
    finally:
        con.close()
    return rows


def _review_items(project_dir: Path) -> list[dict[str, str]]:
    queue = _read_json(project_dir / "results" / "review_queue.json", {"items": []}).get("items", [])
    actions = _read_tsv(project_dir / "results" / "review_actions.tsv")
    items = []
    for row in queue:
        items.append(
            {
                "source": "queue",
                "item_type": row.get("item_type", ""),
                "item_id": row.get("item_id", ""),
                "title": row.get("title", ""),
                "review_status": row.get("review_status", ""),
                "reason": row.get("reason", ""),
                "report_ref": _posix(row.get("report_ref", "")),
            }
        )
    for row in actions:
        items.append(
            {
                "source": "action",
                "item_type": row.get("item_type", ""),
                "item_id": row.get("item_id", ""),
                "title": row.get("review_id", ""),
                "review_status": row.get("action", ""),
                "reason": row.get("reason", ""),
                "report_ref": _posix(row.get("report_ref", "")),
            }
        )
    return items


def _report_refs(project_dir: Path) -> list[dict[str, Any]]:
    structured = _read_json(project_dir / "reports" / "target_report_structured.json", {})
    refs = structured.get("report_evidence_refs", {}) if isinstance(structured, dict) else {}
    out = []
    for gene, payload in refs.items():
        evidence_refs = payload.get("evidence_refs", []) if isinstance(payload, dict) else []
        out.append(
            {
                "gene": gene,
                "score_id": payload.get("score_id", "") if isinstance(payload, dict) else "",
                "evidence_snapshot_id": payload.get("evidence_snapshot_id", "") if isinstance(payload, dict) else "",
                "evidence_refs": evidence_refs,
                "report_ref": f"reports/target_report.html#evidence-{gene.lower()}",
            }
        )
    return out


def _related_reviews(evidence: dict[str, Any], review_items: list[dict[str, str]]) -> list[dict[str, str]]:
    gene = str(evidence.get("entity_symbol", ""))
    evidence_id = str(evidence.get("evidence_id", ""))
    related = []
    for row in review_items:
        item_id = row.get("item_id", "")
        if item_id in {gene, evidence_id} or gene and gene.lower() in row.get("title", "").lower():
            related.append(row)
    return related


def _related_reports(gene: str, evidence_id: str, report_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    related = []
    for row in report_refs:
        evidence_refs = row.get("evidence_refs", [])
        if row.get("gene") == gene or evidence_id in evidence_refs:
            related.append(row)
    return related


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _posix(value: str) -> str:
    return str(value or "").replace("\\", "/")
