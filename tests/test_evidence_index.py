import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_index import build_evidence_review_report_index, query_evidence_trace
from targetcompass_lite.v4 import build_v4_manifest


class EvidenceIndexTest(unittest.TestCase):
    def test_index_links_evidence_review_items_and_report_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_base_files(project)
            _write_evidence_db(project)
            (project / "results").mkdir(exist_ok=True)
            (project / "results" / "review_queue.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "item_type": "causal_grade",
                                "item_id": "CXCL8",
                                "title": "Causal evidence CXCL8: grade A",
                                "review_status": "pending",
                                "reason": "human_review_required",
                                "report_ref": "reports/target_report.html#causal-grade-cxcl8",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            reports = project / "reports"
            reports.mkdir()
            (reports / "target_report_structured.json").write_text(
                json.dumps(
                    {
                        "report_evidence_refs": {
                            "CXCL8": {
                                "score_id": "score_1",
                                "evidence_snapshot_id": "es_1",
                                "evidence_refs": ["ev1"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            index = build_evidence_review_report_index(project)
            self.assertEqual(index["evidence_count"], 1)
            item = index["items"][0]
            self.assertEqual(item["evidence_id"], "ev1")
            self.assertEqual(item["review_items"][0]["item_type"], "causal_grade")
            self.assertEqual(item["report_refs"][0]["score_id"], "score_1")
            query = query_evidence_trace(project, gene="CXCL8")
            self.assertEqual(query["match_count"], 1)
            self.assertEqual(query["items"][0]["evidence_id"], "ev1")

            manifest = build_v4_manifest(project, json.loads((project / "analysis_plan.json").read_text(encoding="utf-8")))
            self.assertIn("evidence_review_report_index", manifest["objects"])
            self.assertTrue((project / "v4" / "evidence_review_report_index.json").exists())


def _write_base_files(project: Path) -> None:
    (project / "research_spec.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "research_theme": "vascular aging",
                "disease_scope": {"canonical": "vascular aging"},
            }
        ),
        encoding="utf-8",
    )
    (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")
    (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")


def _write_evidence_db(project: Path) -> None:
    con = sqlite3.connect(project / "evidence.sqlite")
    con.executescript(
        """
        CREATE TABLE evidence_item (
          evidence_id TEXT PRIMARY KEY, project_id TEXT, entity_symbol TEXT, entity_type TEXT,
          disease_context TEXT, organism TEXT, tissue TEXT, route TEXT, evidence_type TEXT,
          direction TEXT, effect_size REAL, p_value REAL, quality_score REAL, review_status TEXT,
          source_dataset TEXT, artifact_path TEXT, run_id TEXT, artifact_id TEXT, module_version TEXT,
          limitation TEXT, created_at TEXT
        );
        INSERT INTO evidence_item
        (evidence_id, project_id, entity_symbol, evidence_type, source_dataset, artifact_path, run_id, artifact_id, module_version, review_status, created_at)
        VALUES ('ev1', 'demo', 'CXCL8', 'qtl_colocalization', 'genetic_demo', 'results\\genetic_coloc_mr\\genetic_evidence.tsv', 'run_1', 'artifact_1', 'genetic_coloc_mr_v1', 'PENDING', 'now');
        """
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
