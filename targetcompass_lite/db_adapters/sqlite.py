import sqlite3

from .common import GENE_ALIASES, detect_field_mapping, norm_key, normalized_evidence_row, write_evidence
from .contracts import DatabaseAdapterContext, DatabaseAdapterResult


class SQLiteEvidenceAdapter:
    adapter_id = "sqlite_evidence_v0"
    label = "SQLite evidence database"
    description = "Inspects SQLite tables and adapts the first table with a recognizable gene or target symbol column."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        return context.source_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        con = sqlite3.connect(context.source_path)
        con.row_factory = sqlite3.Row
        try:
            table = _select_table(con)
            if table is None:
                out = context.project_dir / "knowledge_imports" / "normalized" / f"{context.resource_id}_evidence.tsv"
                count = write_evidence(out, [])
                return DatabaseAdapterResult(self.adapter_id, out, count, "No table with a recognizable gene column was found.")
            rows = [dict(row) for row in con.execute(f'SELECT * FROM "{table}"')]
            columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
        finally:
            con.close()
        normalized = [normalized_evidence_row(row, context.resource_id) for row in rows]
        out = context.project_dir / "knowledge_imports" / "normalized" / f"{context.resource_id}_evidence.tsv"
        count = write_evidence(out, normalized)
        return DatabaseAdapterResult(
            self.adapter_id,
            out,
            count,
            f"Adapted {count} SQLite evidence row(s) from table {table}.",
            input_rows=len(rows),
            dropped_rows=max(0, len(rows) - count),
            field_mapping=detect_field_mapping(columns),
        )


def _select_table(con: sqlite3.Connection) -> str | None:
    tables = [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
        normalized = {norm_key(column) for column in columns}
        if any(norm_key(alias) in normalized for alias in GENE_ALIASES):
            return table
    return None
