import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.consistency import run_consistency_check
from targetcompass_lite.webapp import _v4_work_order_panel


class ConsistencyCheckTest(unittest.TestCase):
    def test_consistency_check_reports_review_items_and_trace_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            result = run_consistency_check(project)
            self.assertEqual(result["status"], "REVIEW")
            checks = {row["check"]: row for row in result["checks"]}
            self.assertEqual(checks["report_references_current_evidence_index"]["status"], "PASS")
            self.assertEqual(checks["dag_contains_evidence_writes"]["status"], "PASS")
            self.assertEqual(checks["review_queue_has_no_pending_items"]["status"], "REVIEW")
            self.assertTrue((project / "v4" / "consistency_check.json").exists())
            html = _v4_work_order_panel(project)
            self.assertIn("Consistency check", html)
            self.assertIn("Run consistency check", html)

    def test_consistency_accepts_active_postgres_with_sqlite_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            (project / "v4" / "evidence_db_snapshot.json").write_text(
                json.dumps(
                    {
                        "row_count": 1,
                        "evidence_schema_version": "evidence_item_v3",
                        "indexes": [
                            {"name": "idx_evidence_entity_symbol"},
                            {"name": "idx_evidence_type"},
                            {"name": "idx_evidence_dataset"},
                            {"name": "idx_evidence_review_status"},
                            {"name": "idx_evidence_artifact"},
                            {"name": "idx_evidence_run"},
                            {"name": "idx_evidence_gene_type"},
                        ],
                        "storage_backend_ref": "v4/storage_backend_manifest.json",
                    }
                ),
                encoding="utf-8",
            )
            index = json.loads((project / "v4" / "evidence_review_report_index.json").read_text(encoding="utf-8"))
            index["evidence_count"] = 1
            (project / "v4" / "evidence_review_report_index.json").write_text(json.dumps(index), encoding="utf-8")
            (project / "evidence.sqlite").write_text("sqlite fallback placeholder", encoding="utf-8")
            (project / "v4" / "storage_backend_manifest.json").write_text(
                json.dumps(
                    {
                        "active_backends": {"evidence_db": "postgres_local", "object_store": "minio_local"},
                        "sqlite_local": {"exists": True},
                        "postgres_contract": {"enabled": True, "migration_mode": "active_local_docker"},
                    }
                ),
                encoding="utf-8",
            )

            result = run_consistency_check(project)
            checks = {row["check"]: row for row in result["checks"]}
            self.assertEqual(checks["storage_backend_manifest_is_current"]["status"], "PASS")


def _write_project(project: Path) -> None:
    project.mkdir()
    (project / "v4").mkdir()
    (project / "results").mkdir()
    (project / "reports").mkdir()
    evidence_index = {
        "index_id": "eri_1",
        "items": [
            {
                "evidence_id": "ev1",
                "entity_symbol": "CXCL8",
                "review_status": "PENDING",
                "review_items": [],
                "report_refs": [],
            }
        ],
    }
    (project / "v4" / "evidence_review_report_index.json").write_text(json.dumps(evidence_index), encoding="utf-8")
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps({"evidence_review_report_index": {"path": "v4/evidence_review_report_index.json", "index_id": "eri_1"}}),
        encoding="utf-8",
    )
    (project / "v4" / "work_order_dag.json").write_text(
        json.dumps({"nodes": [{"work_order_id": "wo1", "outputs": [{"path": "results/x.tsv"}], "evidence_writes": [{"evidence_id": "ev1"}]}]}),
        encoding="utf-8",
    )
    (project / "results" / "review_queue.json").write_text(
        json.dumps({"queue_count": 1, "items": [{"item_type": "causal_grade", "item_id": "CXCL8", "review_status": "pending"}]}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
