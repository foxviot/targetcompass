import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .schema_validation import load_schema, validate_object


SCHEMA_VERSION = "evidence_item_v1"

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
"""


def _evidence_id(*parts: str) -> str:
    return hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def _validate_evidence(row: dict) -> list[str]:
    errors = []
    schema_row = _coerce_schema_row(row)
    errors.extend(validate_object(schema_row, load_schema("evidence_item.schema.json"), "EvidenceItem"))
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


def _coerce_schema_row(row: dict) -> dict:
    out = dict(row)
    for field in ["effect_size", "p_value", "quality_score"]:
        if out.get(field) not in (None, ""):
            try:
                out[field] = float(out[field])
            except (TypeError, ValueError):
                pass
    return out


def _dedupe_errors(errors: list[str]) -> list[str]:
    seen = set()
    out = []
    for err in errors:
        if err not in seen:
            out.append(err)
            seen.add(err)
    return out


def _insert_evidence(con: sqlite3.Connection, row: dict, rejected: list[dict], source: str, row_number: int) -> bool:
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
         evidence_type, direction, effect_size, p_value, quality_score, review_status,
         source_dataset, artifact_path, run_id, artifact_id, module_version, limitation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _write_import_audit(project_dir: Path, summary: dict, rejected: list[dict]) -> None:
    out_dir = project_dir / "results" / "evidence_import"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "import_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (out_dir / "rejected_rows.tsv").open("w", newline="", encoding="utf-8") as f:
        fields = ["source", "row_number", "entity_symbol", "evidence_type", "reason"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rejected)


def import_evidence(project_dir: Path) -> Path:
    db_path = project_dir / "evidence.sqlite"
    con = sqlite3.connect(db_path, timeout=30)
    con.executescript(SCHEMA)
    _ensure_lineage_columns(con)
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
    for deg_path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv")):
        dataset_id = deg_path.parent.name.replace("bulk_deg_", "")
        with deg_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(deg_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                gene = row.get("gene_symbol", "")
                adj_p_value = row.get("adj_p_value", "")
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
                    "limitation": "fixture demo evidence; association only",
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["bulk_deg"] = summary["by_evidence_type"].get("bulk_deg", 0) + 1
    access_path = project_dir / "results" / "annotation" / "accessibility_annotation.tsv"
    if access_path.exists():
        with access_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(access_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
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
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["accessibility"] = summary["by_evidence_type"].get("accessibility", 0) + 1
    for evidence_path in sorted((project_dir / "knowledge_imports" / "normalized").glob("*_evidence.tsv")):
        with evidence_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(evidence_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                evidence_type = row.get("evidence_type") or "external_database"
                gene = row.get("entity_symbol", "")
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
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1
    genetic_path = project_dir / "results" / "genetic_coloc_mr" / "genetic_evidence.tsv"
    if genetic_path.exists():
        with genetic_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(genetic_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                evidence_type = row.get("evidence_type") or "genetic_evidence"
                gene = row.get("entity_symbol", "")
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
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1
    meta_path = project_dir / "results" / "meta_analysis" / "deg_meta_analysis.tsv"
    if meta_path.exists():
        with meta_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(meta_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                gene = row.get("gene_symbol", "")
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
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["deg_meta_analysis"] = summary["by_evidence_type"].get("deg_meta_analysis", 0) + 1
    causal_path = project_dir / "results" / "causal_evidence" / "causal_evidence_grades.tsv"
    if causal_path.exists():
        with causal_path.open(encoding="utf-8") as f:
            for row_number, row in enumerate(csv.DictReader(f, delimiter="\t"), 2):
                source = str(causal_path.relative_to(project_dir))
                summary["sources"].append(source) if source not in summary["sources"] else None
                grade = row.get("causal_grade", "D")
                evidence = {
                    "evidence_id": _evidence_id(project_dir.name, row.get("gene_symbol", ""), "causal_grade", row_number),
                    "project_id": project_dir.name,
                    "entity_symbol": row.get("gene_symbol", ""),
                    "disease_context": disease,
                    "evidence_type": "causal_grade",
                    "direction": grade,
                    "effect_size": row.get("evidence_count", ""),
                    "p_value": row.get("best_p_value", ""),
                    "quality_score": {"A": 0.9, "B": 0.75, "C": 0.45, "D": 0.1}.get(grade, 0.1),
                    "review_status": "PENDING",
                    "source_dataset": row.get("evidence_types", ""),
                    "artifact_path": source,
                    "run_id": _current_run_id(project_dir),
                    "artifact_id": _artifact_id(project_dir, source, artifact_cache),
                    "module_version": "causal_evidence_grading_v1",
                    "limitation": row.get("limitation", "automated causal triage grade"),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"]["causal_grade"] = summary["by_evidence_type"].get("causal_grade", 0) + 1
    summary["rejected_rows"] = len(rejected)
    con.commit()
    con.close()
    _write_import_audit(project_dir, summary, rejected)
    return db_path


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _ensure_lineage_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(evidence_item)").fetchall()}
    for column in ["run_id", "artifact_id", "module_version"]:
        if column not in existing:
            con.execute(f"ALTER TABLE evidence_item ADD COLUMN {column} TEXT")


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
