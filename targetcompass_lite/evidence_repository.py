from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .canonical.backend_access import load_v5_active_backends
from .local_backends import _container_dsn, _docker_bin, _postgres_client_image, local_backend_env


EVIDENCE_COLUMNS = [
    "evidence_id",
    "project_id",
    "entity_symbol",
    "entity_type",
    "disease_context",
    "organism",
    "tissue",
    "route",
    "evidence_type",
    "direction",
    "effect_size",
    "p_value",
    "quality_score",
    "evidence_level",
    "evidence_weight",
    "evidence_basis",
    "review_status",
    "source_dataset",
    "artifact_path",
    "run_id",
    "artifact_id",
    "module_version",
    "limitation",
    "created_at",
]


def replace_evidence_rows(project_dir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = load_v5_active_backends(project_dir)
    if active.get("active_backends", {}).get("evidence_db") != "postgres_local":
        return _record_status(project_dir, {"status": "SKIPPED", "backend": "sqlite_local", "reason": "postgres_local is not active", "row_count": len(rows)})
    result = _replace_postgres_rows(project_dir, rows)
    return _record_status(project_dir, result)


def load_evidence_rows(
    project_dir: Path,
    *,
    gene: str = "",
    evidence_type: str = "",
    source_dataset: str = "",
    review_status: str = "",
    limit: int = 100000,
) -> dict[str, Any]:
    active = load_v5_active_backends(project_dir)
    if active.get("active_backends", {}).get("evidence_db") == "postgres_local":
        pg = _query_postgres_rows(project_dir, gene=gene, evidence_type=evidence_type, source_dataset=source_dataset, review_status=review_status, limit=limit)
        if pg.get("status") == "PASS":
            _record_status(project_dir, {"status": "PASS", "backend": "postgres_local", "operation": "query", "row_count": len(pg["rows"])})
            return {"backend": "postgres_local", "rows": pg["rows"], "status": "PASS", "failure_reason": ""}
    rows = _query_sqlite_rows(project_dir, gene=gene, evidence_type=evidence_type, source_dataset=source_dataset, review_status=review_status, limit=limit)
    _record_status(project_dir, {"status": "FALLBACK", "backend": "sqlite_local", "operation": "query", "row_count": len(rows)})
    return {"backend": "sqlite_local", "rows": rows, "status": "FALLBACK", "failure_reason": ""}


def load_sqlite_evidence_rows(project_dir: Path) -> list[dict[str, Any]]:
    return _query_sqlite_rows(project_dir, limit=100000)


def _replace_postgres_rows(project_dir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    docker = _docker_bin()
    if not docker:
        return {"status": "FAIL", "backend": "postgres_local", "reason": "Docker CLI is not available", "row_count": len(rows)}
    env = local_backend_env(project_dir)
    export_path = project_dir / "v5" / "evidence_repository" / "postgres_primary_import.tsv"
    sql_path = project_dir / "v5" / "evidence_repository" / "postgres_primary_import.sql"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with export_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_COLUMNS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") if row.get(column) is not None else "" for column in EVIDENCE_COLUMNS})
    sql_path.write_text(
        "\n".join(
            [
                "TRUNCATE evidence_item;",
                "\\copy evidence_item FROM '/import/postgres_primary_import.tsv' WITH (FORMAT csv, HEADER true, DELIMITER E'\\t', NULL '');",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-v",
            f"{str(export_path.parent.resolve())}:/import:ro",
            _postgres_client_image(),
            "psql",
            _container_dsn(env["TARGETCOMPASS_POSTGRES_DSN"]),
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/import/postgres_primary_import.sql",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "backend": "postgres_local",
        "operation": "replace_all",
        "row_count": len(rows),
        "export_ref": str(export_path.relative_to(project_dir)).replace("\\", "/"),
        "sql_ref": str(sql_path.relative_to(project_dir)).replace("\\", "/"),
        "failure_reason": "" if result.returncode == 0 else (result.stderr or result.stdout),
    }


def _query_postgres_rows(project_dir: Path, **filters: Any) -> dict[str, Any]:
    docker = _docker_bin()
    if not docker:
        return {"status": "FAIL", "rows": [], "failure_reason": "Docker CLI is not available"}
    env = local_backend_env(project_dir)
    where, params = _where_sql(filters, style="postgres")
    limit = max(1, min(int(filters.get("limit") or 100000), 100000))
    sql = (
        "COPY (SELECT "
        + ", ".join(EVIDENCE_COLUMNS)
        + f" FROM evidence_item {where} ORDER BY entity_symbol, evidence_type, evidence_id LIMIT {limit}) TO STDOUT WITH CSV HEADER DELIMITER E'\\t';"
    )
    for value in params:
        sql = sql.replace("%s", _quote_pg(value), 1)
    result = subprocess.run(
        [docker, "run", "--rm", _postgres_client_image(), "psql", _container_dsn(env["TARGETCOMPASS_POSTGRES_DSN"]), "-c", sql],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        return {"status": "FAIL", "rows": [], "failure_reason": result.stderr or result.stdout}
    rows = list(csv.DictReader(result.stdout.splitlines(), delimiter="\t"))
    return {"status": "PASS", "rows": rows, "failure_reason": ""}


def _query_sqlite_rows(project_dir: Path, **filters: Any) -> list[dict[str, Any]]:
    where, params = _where_sql(filters, style="sqlite")
    limit = max(1, min(int(filters.get("limit") or 100000), 100000))
    db = project_dir / "evidence.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        has_table = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_item'").fetchone()
        if not has_table:
            return []
        existing = {row[1] for row in con.execute("PRAGMA table_info(evidence_item)").fetchall()}
        selected = [column for column in EVIDENCE_COLUMNS if column in existing]
        if not selected:
            return []
        rows = []
        for row in con.execute(
            f"SELECT {', '.join(selected)} FROM evidence_item {where} ORDER BY entity_symbol, evidence_type, evidence_id LIMIT ?",
            [*params, limit],
        ).fetchall():
            payload = {column: "" for column in EVIDENCE_COLUMNS}
            payload.update(dict(row))
            rows.append(payload)
        return rows
    finally:
        con.close()


def _where_sql(filters: dict[str, Any], *, style: str) -> tuple[str, list[Any]]:
    placeholder = "%s" if style == "postgres" else "?"
    clauses = []
    params: list[Any] = []
    if filters.get("gene"):
        clauses.append(f"LOWER(entity_symbol) LIKE {placeholder}")
        params.append(f"%{str(filters['gene']).lower()}%")
    if filters.get("evidence_type"):
        clauses.append(f"LOWER(evidence_type) = {placeholder}")
        params.append(str(filters["evidence_type"]).lower())
    if filters.get("source_dataset"):
        clauses.append(f"LOWER(source_dataset) LIKE {placeholder}")
        params.append(f"%{str(filters['source_dataset']).lower()}%")
    if filters.get("review_status"):
        clauses.append(f"LOWER(review_status) = {placeholder}")
        params.append(str(filters["review_status"]).lower())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _quote_pg(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _record_status(project_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": "v5.evidence_repository_status/0.1", "project_id": project_dir.name, **payload}
    path = project_dir / "v5" / "evidence_repository" / "last_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log = project_dir / "v5" / "evidence_repository" / "repository_events.jsonl"
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload
