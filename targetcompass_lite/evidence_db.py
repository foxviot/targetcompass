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
         source_dataset, artifact_path, limitation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
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
                    "artifact_path": source,
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
                    "source_dataset": row.get("source_dataset", ""),
                    "artifact_path": source,
                    "limitation": row.get("limitation", "external database; requires review"),
                    "created_at": created,
                }
                if _insert_evidence(con, evidence, rejected, source, row_number):
                    summary["inserted_rows"] += 1
                    summary["by_evidence_type"][evidence_type] = summary["by_evidence_type"].get(evidence_type, 0) + 1
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
