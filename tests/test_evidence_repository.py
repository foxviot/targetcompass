import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_db import SCHEMA
from targetcompass_lite.evidence_repository import load_evidence_rows, load_sqlite_evidence_rows, replace_evidence_rows


class EvidenceRepositoryTest(unittest.TestCase):
    def test_sqlite_fallback_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project_with_sqlite(Path(tmp) / "demo")

            result = load_evidence_rows(project, gene="IL6", limit=10)

            self.assertEqual(result["backend"], "sqlite_local")
            self.assertEqual(result["rows"][0]["entity_symbol"], "IL6")

    def test_replace_rows_skips_when_postgres_not_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project_with_sqlite(Path(tmp) / "demo")
            rows = load_sqlite_evidence_rows(project)

            result = replace_evidence_rows(project, rows)

            self.assertEqual(result["status"], "SKIPPED")
            self.assertEqual(result["backend"], "sqlite_local")
            self.assertTrue((project / "v5" / "evidence_repository" / "last_status.json").exists())


def _project_with_sqlite(project: Path) -> Path:
    project.mkdir(parents=True)
    con = sqlite3.connect(project / "evidence.sqlite")
    try:
        con.executescript(SCHEMA)
        con.execute(
            """
            INSERT INTO evidence_item
            (evidence_id, project_id, entity_symbol, evidence_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("ev1", "demo", "IL6", "bulk_deg", "2026-06-23T00:00:00+00:00"),
        )
        con.commit()
    finally:
        con.close()
    return project


if __name__ == "__main__":
    unittest.main()
