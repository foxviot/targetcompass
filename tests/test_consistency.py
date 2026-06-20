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
