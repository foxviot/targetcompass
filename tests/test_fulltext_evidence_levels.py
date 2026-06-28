import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_db import import_evidence, query_evidence_items
from targetcompass_lite.fulltext_literature import run_fulltext_literature


class FulltextEvidenceLevelTest(unittest.TestCase):
    def test_uploaded_text_becomes_fulltext_weighted_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            text_path = Path(tmp) / "paper.txt"
            text_path.write_text(
                "Methods skeletal muscle samples were analyzed by qPCR and ELISA. "
                "Results IL6 and CXCL8 were increased in myocytes and secreted cytokine signaling.",
                encoding="utf-8",
            )
            run = run_fulltext_literature(project, text=[str(text_path)], limit=1)
            self.assertEqual(run["document_count"], 1)
            self.assertGreaterEqual(run["evidence_row_count"], 2)

            query = query_evidence_items(project, evidence_type="fulltext_literature", limit=20)
            self.assertGreaterEqual(query["match_count"], 2)
            levels = {row["evidence_level"] for row in query["items"]}
            self.assertEqual(levels, {"L1_fulltext"})
            weights = {row["evidence_weight"] for row in query["items"]}
            self.assertEqual(weights, {0.55})

    def test_import_assigns_abstract_level_to_literature_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            lit = project / "results" / "literature_validation"
            lit.mkdir(parents=True)
            (lit / "literature_evidence.tsv").write_text(
                "evidence_id\tproject_id\tentity_symbol\tentity_type\tdisease_context\torganism\ttissue\troute\tevidence_type\tdirection\teffect_size\tp_value\tquality_score\treview_status\tsource_dataset\tartifact_path\trun_id\tartifact_id\tmodule_version\tlimitation\tcreated_at\n"
                "lit1\tdemo\tIL6\tgene\ttype 2 diabetes\thuman\tskeletal muscle\tsecreted\tliterature_validation\thigh\t\t\t0.8\tPENDING\tPubMed:1\tresults/literature_validation/literature_evidence.tsv\tliterature_validation\ta1\tliterature_validation_v1\tabstract only\t2026-01-01T00:00:00Z\n",
                encoding="utf-8",
            )
            import_evidence(project)
            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                row = con.execute("SELECT evidence_level, evidence_weight FROM evidence_item WHERE evidence_id='lit1'").fetchone()
            finally:
                con.close()
            self.assertEqual(row[0], "L0_abstract")
            self.assertEqual(row[1], 0.25)


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    project.mkdir()
    (project / "research_spec.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "research_theme": "diabetes muscle SASP",
                "disease_scope": {"canonical": "type 2 diabetes"},
                "priority_tissues": ["skeletal muscle"],
                "priority_cells": ["myocyte"],
            }
        ),
        encoding="utf-8",
    )
    return project


if __name__ == "__main__":
    unittest.main()
