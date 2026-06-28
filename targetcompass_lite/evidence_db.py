import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, v4_dir
from .evidence_levels import classify_evidence_level


SCHEMA_VERSION = "evidence_item_v3"
MIGRATION_SCHEMA = "v4.evidence_db_migration/0.1"
SNAPSHOT_SCHEMA = "v4.evidence_db_snapshot/0.1"
MAX_DEG_EVIDENCE_ROWS_PER_DATASET = 500
_QC_REPORT_CACHE: dict[str, Any] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_item (
  evidence_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  entity_symbol TEXT NOT NULL,
  entity_type TEXT DEFAULT 'gene',
  disease_context TEXT,
  organism TEXT,
  tissue TEXT,
  route TEXT,
  evidence_type TEXT NOT NULL,
  direction TEXT,
  effect_size REAL,
  p_value REAL,
  quality_score REAL,
  evidence_level TEXT,
  evidence_weight REAL,
  evidence_basis TEXT,
  review_status TEXT DEFAULT 'PENDING',
  source_dataset TEXT,
  artifact_path TEXT,
  run_id TEXT,
  artifact_id TEXT,
  module_version TEXT,
  limitation TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_migration (
  migration_id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  description TEXT NOT NULL
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_evidence_entity_symbol ON evidence_item(entity_symbol)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence_item(evidence_type)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_dataset ON evidence_item(source_dataset)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_review_status ON evidence_item(review_status)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_artifact ON evidence_item(artifact_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence_item(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gene_type ON evidence_item(entity_symbol, evidence_type)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_level ON evidence_item(evidence_level)",
]


def migrate_evidence_db(project_dir: Path) -> dict[str, Any]:
    db_path = project_dir / "evidence.sqlite"
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.executescript(SCHEMA)
        _ensure_lineage_columns(con)
        for sql in INDEX_SQL:
            con.execute(sql)
        con.execute("INSERT OR REPLACE INTO evidence_metadata (key, value) VALUES (?, ?)", ("schema_version", SCHEMA_VERSION))
        migration_id = "migration_" + content_hash({"schema": SCHEMA_VERSION, "indexes": INDEX_SQL})[:16]
        con.execute(
            """
            INSERT OR REPLACE INTO evidence_migration
            (migration_id, applied_at, schema_version, description)
            VALUES (?, ?, ?, ?)
            """,
            (
                migration_id,
                datetime.now(timezone.utc).isoformat(),
                SCHEMA_VERSION,
                "Ensure evidence schema, lineage columns, metadata, migration log, and production query indexes.",
            ),
        )
        con.commit()
        indexes = _list_indexes(con)
        payload = {
            "schema_version": MIGRATION_SCHEMA,
            "project_id": project_dir.name,
            "database": "evidence.sqlite",
            "migration_id": migration_id,
            "evidence_schema_version": SCHEMA_VERSION,
            "index_count": len(indexes),
            "indexes": indexes,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        con.close()
    out = v4_dir(project_dir) / "evidence_db_migration.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def query_evidence_items(
    project_dir: Path,
    gene: str = "",
    evidence_type: str = "",
    source_dataset: str = "",
    review_status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    migrate_evidence_db(project_dir)
    limit = max(1, min(int(limit or 100), 1000))
    from .evidence_repository import load_evidence_rows

    repo = load_evidence_rows(project_dir, gene=gene, evidence_type=evidence_type, source_dataset=source_dataset, review_status=review_status, limit=limit)
    rows = repo["rows"]
    count = len(rows)
    return {
        "schema_version": "v4.evidence_query/0.1",
        "project_id": project_dir.name,
        "repository_backend": repo.get("backend", "sqlite_local"),
        "query": {
            "gene": gene,
            "evidence_type": evidence_type,
            "source_dataset": source_dataset,
            "review_status": review_status,
            "limit": limit,
        },
        "match_count": count,
        "returned_count": len(rows),
        "items": rows,
    }


def build_evidence_db_snapshot(project_dir: Path) -> dict[str, Any]:
    from .storage_manifest import build_storage_manifest

    migration = migrate_evidence_db(project_dir)
    storage = build_storage_manifest(project_dir)
    from .evidence_repository import load_evidence_rows

    repo = load_evidence_rows(project_dir, limit=100000)
    rows = repo["rows"]
    total = len(rows)
    by_type = _count_by(rows, "evidence_type")
    by_level = _count_by(rows, "evidence_level", default="unclassified")
    by_dataset = _count_by(rows, "source_dataset")
    by_review = _count_by(rows, "review_status")
    latest_created_at = max([str(row.get("created_at", "")) for row in rows] or [""])
    con = sqlite3.connect(project_dir / "evidence.sqlite", timeout=30)
    con.row_factory = sqlite3.Row
    try:
        indexes = _list_indexes(con)
        migrations = [dict(row) for row in con.execute("SELECT * FROM evidence_migration ORDER BY applied_at").fetchall()]
    finally:
        con.close()
    payload = {
        "schema_version": SNAPSHOT_SCHEMA,
        "project_id": project_dir.name,
        "database": "evidence.sqlite",
        "repository_backend": repo.get("backend", "sqlite_local"),
        "evidence_schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": total,
        "by_evidence_type": by_type,
        "by_evidence_level": by_level,
        "by_source_dataset": by_dataset,
        "by_review_status": by_review,
        "latest_created_at": latest_created_at,
        "indexes": indexes,
        "migrations": migrations,
        "migration_ref": "v4/evidence_db_migration.json",
        "storage_backend_ref": "v4/storage_backend_manifest.json",
        "storage_hash": storage.get("storage_hash", ""),
        "snapshot_hash": content_hash(
            {
                "row_count": total,
                "by_type": by_type,
                "by_level": by_level,
                "by_dataset": by_dataset,
                "by_review": by_review,
                "latest_created_at": latest_created_at,
                "migration": migration.get("migration_id", ""),
                "storage": storage.get("storage_hash", ""),
            }
        ),
    }
    out = evidence_db_snapshot_path(project_dir)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _count_by(rows: list[dict[str, Any]], field: str, default: str = "") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or default)
        counts[key] = counts.get(key, 0) + 1
    return counts


def evidence_db_snapshot_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "evidence_db_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _evidence_id(*parts: str) -> str:
    return hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def _validate_evidence(row: dict) -> list[str]:
    errors = []
    for field in ["evidence_id", "project_id", "entity_symbol", "evidence_type", "created_at"]:
        if not str(row.get(field, "") or "").strip():
            errors.append(f"EvidenceItem.{field}: must not be empty")
    if row.get("evidence_type") == "bulk_deg":
        for field in ["direction", "effect_size", "p_value", "source_dataset", "artifact_path"]:
            if row.get(field) in (None, ""):
                errors.append(f"{field} is required for bulk_deg")
        for field in ["effect_size", "p_value", "quality_score"]:
            if row.get(field) not in (None, ""):
                try:
                    float(row[field])
                except (TypeError, ValueError):
                    errors.append(f"{field} must be numeric")
    return _dedupe_errors(errors)


def _dedupe_errors(errors: list[str]) -> list[str]:
    seen = set()
    out = []
    for err in errors:
        if err not in seen:
            out.append(err)
            seen.add(err)
    return out


def _insert_evidence(con: sqlite3.Connection, row: dict, rejected: list[dict], source: str, row_number: int, project_dir: Path | None = None) -> bool:
    row = _with_evidence_level(row)
    if project_dir is not None:
        allowed, row, reason = _apply_qc_gate(project_dir, row)
        if not allowed:
            rejected.append(
                {
                    "source": source,
                    "row_number": row_number,
                    "entity_symbol": row.get("entity_symbol", ""),
                    "evidence_type": row.get("evidence_type", ""),
                    "reason": reason,
                }
            )
            return False
    errors = _validate_evidence(row)
    if errors:
        rejected.append(
            {
                "source": source,
                "row_number": row_number,
                "entity_symbol": row.get("entity_symbol", ""),
                "evidence_type": row.get("evidence_type", ""),
                "reason": "; ".join(errors),
            }
        )
        return False
    con.execute(
        """
        INSERT INTO evidence_item
        (evidence_id, project_id, entity_symbol, disease_context, organism, tissue, route,
         evidence_type, direction, effect_size, p_value, quality_score, evidence_level,
         evidence_weight, evidence_basis, review_status, source_dataset, artifact_path,
         run_id, artifact_id, module_version, limitation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["evidence_id"],
            row["project_id"],
            row["entity_symbol"],
            row.get("disease_context"),
            row.get("organism"),
            row.get("tissue"),
            row.get("route"),
            row["evidence_type"],
            row.get("direction"),
            float(row["effect_size"]) if row.get("effect_size") not in (None, "") else None,
            float(row["p_value"]) if row.get("p_value") not in (None, "") else None,
            float(row["quality_score"]) if row.get("quality_score") not in (None, "") else None,
            row.get("evidence_level"),
            float(row["evidence_weight"]) if row.get("evidence_weight") not in (None, "") else None,
            row.get("evidence_basis"),
            row.get("review_status", "PENDING"),
            row.get("source_dataset"),
            row.get("artifact_path"),
            row.get("run_id"),
            row.get("artifact_id"),
            row.get("module_version"),
            row.get("limitation"),
            row["created_at"],
        ),
    )
    return True


def _apply_qc_gate(project_dir: Path, row: dict) -> tuple[bool, dict, str]:
    gate = _qc_gate_for_evidence(project_dir, row)
    if gate["status"] == "not_applicable":
        return True, row, ""
    out = dict(row)
    if gate["status"] == "fail":
        return False, out, "QC gate failed: " + gate.get("reason", "")
    if gate["status"] == "review":
        out["review_status"] = "QC_REVIEW_REQUIRED"
        limitation = out.get("limitation", "")
        suffix = "QC review required: " + gate.get("reason", "")
        out["limitation"] = f"{limitation}; {suffix}" if limitation else suffix
    return True, out, ""


def _qc_gate_for_evidence(project_dir: Path, row: dict) -> dict[str, str]:
    artifact = str(row.get("artifact_path", "") or "")
    module_version = str(row.get("module_version", "") or "")
    evidence_type = str(row.get("evidence_type", "") or "")
    if evidence_type in {"literature_validation", "fulltext_literature", "fulltext_extracted_result", "external_database"}:
        return {"status": "not_applicable", "reason": "non-computational evidence"}
    index_path = project_dir / "results" / "qc" / "task_qc_reports.json"
    if not index_path.exists():
        return {"status": "review", "reason": "no TaskQCReport index found"}
    reports = _cached_qc_reports(project_dir, index_path)
    if reports is None:
        return {"status": "review", "reason": "TaskQCReport index could not be read"}
    match = _match_qc_report(project_dir, reports, artifact, module_version, evidence_type)
    if not match:
        return {"status": "review", "reason": f"no matching TaskQCReport for artifact={artifact or 'unknown'} module={module_version or evidence_type}"}
    status = match.get("overall_status", "")
    if status == "fail":
        return {"status": "fail", "reason": f"{match.get('path', '')} overall_status=fail"}
    if status == "review":
        return {"status": "review", "reason": f"{match.get('path', '')} overall_status=review"}
    return {"status": "pass", "reason": f"{match.get('path', '')} overall_status=pass"}


def _match_qc_report(project_dir: Path, reports: list[dict], artifact: str, module_version: str, evidence_type: str) -> dict:
    module_hint = _module_hint(module_version, evidence_type)
    artifact_norm = _norm_artifact_path(artifact)
    best = {}
    for row in reversed(reports):
        report = row.get("_report") or {}
        if not report:
            continue
        artifacts = {_norm_artifact_path(item) for item in report.get("artifacts", []) if item}
        if artifact_norm and artifact_norm in artifacts:
            return row
        if artifact_norm:
            continue
        module_id = str(row.get("module_id", "")).lower()
        if module_hint and module_hint in module_id:
            best = row
    return best


def _norm_artifact_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().lower()


def _cached_qc_reports(project_dir: Path, index_path: Path) -> list[dict] | None:
    try:
        stat = index_path.stat()
    except OSError:
        return None
    cache_key = str(index_path.resolve())
    cached = _QC_REPORT_CACHE.get(cache_key)
    if cached and cached.get("mtime_ns") == stat.st_mtime_ns:
        return cached.get("reports", [])
    try:
        reports = json.loads(index_path.read_text(encoding="utf-8")).get("reports", [])
    except Exception:
        return None
    hydrated = []
    for row in reports:
        item = dict(row)
        item["_report"] = _read_json(project_dir / row.get("path", ""), {})
        hydrated.append(item)
    _QC_REPORT_CACHE[cache_key] = {"mtime_ns": stat.st_mtime_ns, "reports": hydrated}
    return hydrated


def _module_hint(module_version: str, evidence_type: str) -> str:
    text = f"{module_version} {evidence_type}".lower()
    if "bulk_deg" in text:
        return "bulk_deg"
    if "sasp" in text:
        return "sasp_score"
    if "cell_type" in text:
        return "cell_type_evidence"
    if "surface" in text or "accessibility" in text or "annotation" in text:
        return "annotation"
    if "meta" in text:
        return "meta_analysis"
    if "causal" in text:
        return "causal"
    if "genetic" in text:
        return "genetic"
    return ""


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _with_evidence_level(row: dict) -> dict:
    out = dict(row)
    level, weight, basis = classify_evidence_level(out)
    out["evidence_level"] = level
    out["evidence_weight"] = weight
    out["evidence_basis"] = basis
    return out


def _write_import_audit(project_dir: Path, summary: dict, rejected: list[dict]) -> None:
    out_dir = project_dir / "results" / "evidence_import"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "import_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (out_dir / "rejected_rows.tsv").open("w", newline="", encoding="utf-8") as f:
        fields = ["source", "row_number", "entity_symbol", "evidence_type", "reason"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rejected)


def _import_generic_evidence_tsv(
    con: sqlite3.Connection,
    project_dir: Path,
    path: Path,
    disease: str,
    created: str,
    summary: dict,
    rejected: list[dict],
    artifact_cache: dict[str, str],
    default_evidence_type: str,
    default_module_version: str,
    gene_universe: set[str] | None = None,
) -> None:
    with path.open(encoding="utf-8") as f:
        for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
            source = str(path.relative_to(project_dir))
            summary["sources"].append(source) if source not in summary["sources"] else None
            evidence_type = row.get("evidence_type") or default_evidence_type
            gene = row.get("entity_symbol", "")
            if gene_universe is not None and not _should_import_gene_evidence(gene, gene_universe, allow_unknown=True):
                continue
            evidence = {
                "evidence_id": row.get("evidence_id") or _evidence_id(project_dir.name, gene, evidence_type, row.get("source_dataset", ""), row_number),
                "project_id": project_dir.name,
                "entity_symbol": gene,
                "entity_type": row.get("entity_type", "gene"),
                "disease_context": row.get("disease_context", disease),
                "organism": row.get("organism", ""),
                "tissue": row.get("tissue", ""),
                "route": row.get("route", ""),
                "evidence_type": evidence_type,
                "direction": row.get("direction", ""),
                "effect_size": row.get("effect_size", ""),
                "p_value": row.get("p_value", ""),
                "quality_score": row.get("quality_score", "0.65"),
                "evidence_level": row.get("evidence_level", ""),
                "evidence_weight": row.get("evidence_weight", ""),
                "evidence_basis": row.get("evidence_basis", ""),
                "review_status": row.get("review_status", "PENDING"),
                "source_dataset": row.get("source_dataset", "") or source,
                "artifact_path": row.get("artifact_path", source),
                "run_id": row.get("run_id", default_evidence_type),
                "artifact_id": row.get("artifact_id") or _artifact_id(project_dir, source, artifact_cache),
                "module_version": row.get("module_version", default_module_version),
                "limitation": row.get("limitation", f"{default_evidence_type} evidence requires review"),
                "created_at": row.get("created_at", created),
            }
            if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                summary["inserted_rows"] += 1
                summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1


def _priority_genes_from_spec(spec: dict[str, Any]) -> set[str]:
    genes: set[str] = set()
    for values in spec.get("candidate_gene_sets", {}).values():
        if isinstance(values, list):
            genes.update(str(gene).strip().upper() for gene in values if str(gene).strip())
    return genes


def _should_import_deg_row(row_number: int, gene: str, adj_p_value: str, priority_genes: set[str]) -> bool:
    if row_number <= MAX_DEG_EVIDENCE_ROWS_PER_DATASET + 1:
        return True
    if gene.strip().upper() in priority_genes:
        return True
    return _is_float(adj_p_value) and float(adj_p_value) < 0.05


def _sasp_quality(score: str, adj_p_value: str) -> float:
    quality = 0.45
    if _is_float(score):
        quality += min(0.25, float(score) / 100.0)
    if _is_float(adj_p_value):
        p = float(adj_p_value)
        if p <= 0.01:
            quality += 0.20
        elif p <= 0.05:
            quality += 0.12
    return round(min(0.9, quality), 3)


def _should_import_gene_evidence(gene: str, gene_universe: set[str], allow_unknown: bool = False) -> bool:
    normalized = str(gene or "").strip().upper()
    if not normalized:
        return allow_unknown
    if normalized in {"UNKNOWN", "NA", "N/A"}:
        return allow_unknown
    if not gene_universe:
        return True
    return normalized in gene_universe


def _artifact_candidate_genes(project_dir: Path) -> set[str]:
    genes: set[str] = set()
    paths = [
        project_dir / "results" / "fulltext_literature" / "llm_extraction" / "fulltext_llm_evidence.tsv",
        project_dir / "results" / "cell_type_evidence" / "cell_type_evidence.tsv",
        project_dir / "results" / "sasp_score" / "sasp_gene_scores.tsv",
        project_dir / "results" / "annotation" / "accessibility_annotation.tsv",
    ]
    paths.extend(sorted((project_dir / "knowledge_imports" / "normalized").glob("*_evidence.tsv")))
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                gene = str(row.get("entity_symbol") or row.get("gene_symbol") or "").strip().upper()
                if gene and gene not in {"UNKNOWN", "NA", "N/A"} and len(gene) >= 2:
                    genes.add(gene)
    return genes


def import_evidence(project_dir: Path) -> Path:
    db_path = project_dir / "evidence.sqlite"
    con = sqlite3.connect(db_path, timeout=30)
    con.executescript(SCHEMA)
    _ensure_lineage_columns(con)
    for sql in INDEX_SQL:
        con.execute(sql)
    con.execute("DELETE FROM evidence_item")
    con.execute("DELETE FROM evidence_metadata")
    con.execute(
        "INSERT INTO evidence_metadata (key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    created = datetime.now(timezone.utc).isoformat()
    spec = json.loads((project_dir / "research_spec.json").read_text(encoding="utf-8"))
    disease = spec["disease_scope"]["canonical"]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_dir.name,
        "inserted_rows": 0,
        "rejected_rows": 0,
        "by_evidence_type": {},
        "sources": [],
    }
    rejected = []
    artifact_cache: dict[str, str] = {}
    priority_genes = _priority_genes_from_spec(spec)
    imported_gene_universe: set[str] = set(priority_genes) | _artifact_candidate_genes(project_dir)
    for deg_path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv")):
        dataset_id = deg_path.parent.name.replace("bulk_deg_", "")
        with deg_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(deg_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                gene = row.get("gene_symbol", "")
                adj_p_value = row.get("adj_p_value", "")
                if not _should_import_deg_row(row_number, gene, adj_p_value, priority_genes):
                    continue
                imported_gene_universe.add(gene.strip().upper())
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, gene, "bulk_deg", dataset_id),
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "disease_context": disease,
                    "evidence_type": "bulk_deg",
                    "direction": row.get("direction", ""),
                    "effect_size": row.get("logFC", ""),
                    "p_value": adj_p_value,
                    "quality_score": 0.75 if _is_float(adj_p_value) and float(adj_p_value) < 0.05 else 0.45,
                    "source_dataset": dataset_id,
                    "artifact_path": source,
                    "run_id": _run_id_for_artifact(project_dir, deg_path.parent),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": "bulk_deg_v1",
                    "limitation": "bulk DEG evidence; association only; complete result table remains in artifact_path",
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["bulk_deg"] = summary["by_evidence_type"].get("bulk_deg", 0) + 1
    access_path = project_dir / "results" / "annotation" / "accessibility_annotation.tsv"
    if access_path.exists():
        with access_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(access_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                gene = row.get("gene_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe):
                    continue
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, row.get("gene_symbol", ""), "accessibility", row.get("route", "")),
                    "project_id": project_dir.name,
                    "entity_symbol": row.get("gene_symbol", ""),
                    "disease_context": disease,
                    "route": row.get("route", ""),
                    "evidence_type": "accessibility",
                    "quality_score": 0.8 if row.get("accessibility_status") == "SUPPORTED" else 0.2,
                    "review_status": "PENDING",
                    "source_dataset": "annotation_accessibility",
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": "accessibility_annotation_v1",
                    "limitation": row.get("accessibility_status", ""),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["accessibility"] = summary["by_evidence_type"].get("accessibility", 0) + 1
                route = (row.get("route", "") or "").strip().lower()
                if route in {"surface", "secreted", "ecd", "plasma_membrane", "cell_surface"}:
                    surface_evidence = {
                        "evidence_id": _evidence_id(project_dir.name, row.get("gene_symbol", ""), "surface_marker_annotation", route),
                        "project_id": project_dir.name,
                        "entity_symbol": row.get("gene_symbol", ""),
                        "disease_context": disease,
                        "route": row.get("route", ""),
                        "evidence_type": "surface_marker_annotation",
                        "quality_score": 0.72 if row.get("accessibility_status") == "SUPPORTED" else 0.45,
                        "review_status": "PENDING",
                        "source_dataset": "annotation_accessibility",
                        "artifact_path": source,
                        "run_id": _current_run_id(project_dir),
                        "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                        "module_version": "surface_marker_annotation_v1",
                        "limitation": "annotation-level surface/secreted/ECD evidence; not experimental accessibility proof",
                        "created_at": created,
                    }
                    if _insert_evidence(con, surface_evidence, rejected, source, row_number, project_dir):
                        summary["inserted_rows"] += 1
                        summary["by_evidence_type"]["surface_marker_annotation"] = summary["by_evidence_type"].get("surface_marker_annotation", 0) + 1
    sasp_path = project_dir / "results" / "sasp_score" / "sasp_gene_scores.tsv"
    if sasp_path.exists():
        with sasp_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(sasp_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                gene = row.get("gene_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe):
                    continue
                score = row.get("sasp_component_score", "")
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, gene, "sasp_score", row.get("dataset_id", ""), row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "disease_context": disease,
                    "evidence_type": "sasp_score",
                    "direction": row.get("direction", ""),
                    "effect_size": score,
                    "p_value": row.get("adj_p_value", ""),
                    "quality_score": _sasp_quality(score, row.get("adj_p_value", "")),
                    "review_status": "PENDING",
                    "source_dataset": row.get("dataset_id", ""),
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": "sasp_score_v1",
                    "limitation": "SASP core overlap score derived from DEG output; phenotype/program evidence, not causal proof",
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["sasp_score"] = summary["by_evidence_type"].get("sasp_score", 0) + 1
    for evidence_path in sorted((project_dir / "knowledge_imports" / "normalized").glob("*_evidence.tsv")):
        with evidence_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(evidence_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                evidence_type = row.get("evidence_type") or "external_database"
                gene = row.get("entity_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe):
                    continue
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, gene, evidence_type, row.get("source_dataset", source), row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "disease_context": disease,
                    "route": row.get("route", ""),
                    "evidence_type": evidence_type,
                    "direction": row.get("direction", ""),
                    "effect_size": row.get("effect_size", ""),
                    "p_value": row.get("p_value", ""),
                    "quality_score": row.get("quality_score", "0.5"),
                    "review_status": "PENDING",
                    "source_dataset": row.get("source_dataset", "") or source,
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": row.get("module_version", "external_database_adapter_v1"),
                    "limitation": row.get("limitation", "external database; requires review"),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1
    literature_path = project_dir / "results" / "literature_validation" / "literature_evidence.tsv"
    if literature_path.exists():
        with literature_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(literature_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                evidence_type = row.get("evidence_type") or "literature_validation"
                gene = row.get("entity_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe, allow_unknown=True):
                    continue
                evidence = {
                    "evidence_id": row.get("evidence_id") or _evidence_id(project_dir.name, gene, evidence_type, row.get("source_dataset", ""), row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "disease_context": row.get("disease_context", disease),
                    "organism": row.get("organism", ""),
                    "tissue": row.get("tissue", ""),
                    "route": row.get("route", ""),
                    "evidence_type": evidence_type,
                    "direction": row.get("direction", ""),
                    "effect_size": row.get("effect_size", ""),
                    "p_value": row.get("p_value", ""),
                    "quality_score": row.get("quality_score", "0.5"),
                    "review_status": row.get("review_status", "PENDING"),
                    "source_dataset": row.get("source_dataset", "") or source,
                    "artifact_path": source,
                    "run_id": row.get("run_id", "literature_validation"),
                    "artifact_id": row.get("artifact_id") or _artifact_id(project_dir, source, artifact_cache),
                    "module_version": row.get("module_version", "literature_validation_v1"),
                    "limitation": row.get("limitation", "literature evidence requires review"),
                    "created_at": row.get("created_at", created),
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1
    fulltext_path = project_dir / "results" / "fulltext_literature" / "fulltext_evidence.tsv"
    if fulltext_path.exists():
        _import_generic_evidence_tsv(con, project_dir, fulltext_path, disease, created, summary, rejected, artifact_cache, "fulltext_literature", "fulltext_literature_v1", imported_gene_universe)
    fulltext_llm_path = project_dir / "results" / "fulltext_literature" / "llm_extraction" / "fulltext_llm_evidence.tsv"
    if fulltext_llm_path.exists():
        _import_generic_evidence_tsv(con, project_dir, fulltext_llm_path, disease, created, summary, rejected, artifact_cache, "fulltext_extracted_result", "fulltext_llm_extraction_v1", imported_gene_universe)
    cell_type_path = project_dir / "results" / "cell_type_evidence" / "cell_type_evidence.tsv"
    if cell_type_path.exists():
        _import_generic_evidence_tsv(con, project_dir, cell_type_path, disease, created, summary, rejected, artifact_cache, "cell_type_expression", "cell_type_evidence_v1", imported_gene_universe)
    genetic_path = project_dir / "results" / "genetic_coloc_mr" / "genetic_evidence.tsv"
    if genetic_path.exists():
        with genetic_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(genetic_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                evidence_type = row.get("evidence_type") or "genetic_evidence"
                gene = row.get("entity_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe):
                    continue
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, gene, evidence_type, "genetic_coloc_mr", row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "disease_context": disease,
                    "evidence_type": evidence_type,
                    "direction": row.get("direction", ""),
                    "effect_size": row.get("effect_size", ""),
                    "p_value": row.get("p_value", ""),
                    "quality_score": row.get("quality_score", "0.5"),
                    "review_status": "PENDING",
                    "source_dataset": row.get("source_dataset", "genetic_coloc_mr"),
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": row.get("module_version", "genetic_coloc_mr_v1"),
                    "limitation": row.get("limitation", "genetic coloc/MR evidence requires review"),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1
    meta_path = project_dir / "results" / "meta_analysis" / "deg_meta_analysis.tsv"
    if meta_path.exists():
        with meta_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(meta_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                gene = row.get("gene_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe):
                    continue
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, gene, "deg_meta_analysis", row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "disease_context": disease,
                    "evidence_type": "deg_meta_analysis",
                    "direction": row.get("dominant_direction", ""),
                    "effect_size": row.get("mean_logFC", ""),
                    "p_value": "",
                    "quality_score": min(0.9, 0.35 + 0.15 * int(row.get("dataset_count", "0") or 0)),
                    "review_status": "PENDING",
                    "source_dataset": row.get("source_datasets", ""),
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": "deg_meta_analysis_v1",
                    "limitation": row.get("limitation", "lightweight meta-analysis summary"),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["deg_meta_analysis"] = summary["by_evidence_type"].get("deg_meta_analysis", 0) + 1
    causal_path = project_dir / "results" / "causal_evidence" / "causal_evidence_grades.tsv"
    if causal_path.exists():
        with causal_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(causal_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                grade = row.get("causal_grade", "C0")
                gene = row.get("gene_symbol", "")
                if not _should_import_gene_evidence(gene, imported_gene_universe):
                    continue
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, row.get("gene_symbol", ""), "causal_grade", row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": row.get("gene_symbol", ""),
                    "disease_context": disease,
                    "evidence_type": "causal_grade",
                    "direction": grade,
                    "effect_size": row.get("evidence_count", ""),
                    "p_value": row.get("best_p_value", ""),
                    "quality_score": {
                        "A": 0.9,
                        "B": 0.75,
                        "C": 0.45,
                        "D": 0.1,
                        "C4": 0.95,
                        "C3": 0.8,
                        "C2": 0.55,
                        "C1": 0.3,
                        "C0": 0.05,
                    }.get(grade, 0.1),
                    "review_status": "PENDING",
                    "source_dataset": row.get("evidence_types", ""),
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": "causal_evidence_grading_v1",
                    "limitation": row.get("limitation", "automated causal triage grade"),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number, project_dir):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["causal_grade"] = summary["by_evidence_type"].get("causal_grade", 0) + 1
    summary["rejected_rows"] = len(rejected)
    con.commit()
    con.close()
    try:
        from .evidence_repository import load_sqlite_evidence_rows, replace_evidence_rows

        replace_evidence_rows(project_dir, load_sqlite_evidence_rows(project_dir))
    except Exception:
        pass
    _write_import_audit(project_dir, summary, rejected)
    migrate_evidence_db(project_dir)
    build_evidence_db_snapshot(project_dir)
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [
                db_path,
                "results/evidence_import/import_summary.json",
                "results/evidence_import/rejected_rows.tsv",
                "v4/evidence_db_migration.json",
                "v4/evidence_db_snapshot.json",
            ],
            producer="evidence_db",
            artifact_type="evidence_db_output",
            task_id="evidence_db_import",
        )
    except Exception:
        pass
    return db_path


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _ensure_lineage_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(evidence_item)").fetchall()}
    for column in ["run_id", "artifact_id", "module_version", "evidence_level", "evidence_weight", "evidence_basis"]:
        if column not in existing:
            column_type = "REAL" if column == "evidence_weight" else "TEXT"
            con.execute(f"ALTER TABLE evidence_item ADD COLUMN {column} {column_type}")


def _list_indexes(con: sqlite3.Connection) -> list[dict[str, Any]]:
    indexes = []
    for row in con.execute("PRAGMA index_list(evidence_item)").fetchall():
        name = row[1]
        columns = [col[2] for col in con.execute(f"PRAGMA index_info({name})").fetchall()]
        indexes.append({"name": name, "unique": bool(row[2]), "columns": columns})
    return sorted(indexes, key=lambda item: item["name"])


def _current_run_id(project_dir: Path) -> str:
    path = project_dir / "results" / "run_status.json"
    if not path.exists():
        return "manual_import"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("run_id") or "manual_import"
    except json.JSONDecodeError:
        return "manual_import"


def _run_id_for_artifact(project_dir: Path, result_dir: Path) -> str:
    manifest = result_dir / "run_manifest.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text(encoding="utf-8")).get("run_id") or _current_run_id(project_dir)
        except json.JSONDecodeError:
            pass
    return _current_run_id(project_dir)


def _artifact_id(project_dir: Path, relative_path: str, cache: dict[str, str] | None = None) -> str:
    if cache is not None and relative_path in cache:
        return cache[relative_path]
    path = project_dir / relative_path
    payload = relative_path
    if path.exists():
        payload += "|" + hashlib.sha256(path.read_bytes()).hexdigest()
    value = "artifact_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    if cache is not None:
        cache[relative_path] = value
    return value
