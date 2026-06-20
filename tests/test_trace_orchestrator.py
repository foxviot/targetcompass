import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.trace_orchestrator import refresh_traceability
from targetcompass_lite.v4 import compile_v4_work_orders, finish_work_order_attempt, start_work_order_attempt


class TraceOrchestratorTest(unittest.TestCase):
    def test_refresh_traceability_updates_queue_dag_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            compile_v4_work_orders(project, json.loads((project / "analysis_plan.json").read_text(encoding="utf-8")))
            attempt = start_work_order_attempt(project, "P4_bulk_deg_ds", "run_1")
            finish_work_order_attempt(project, attempt["attempt_id"], "success", ["results/bulk_deg_ds/deg_results.tsv"])

            result = refresh_traceability(project)
            self.assertFalse(result["errors"])
            self.assertTrue((project / "results" / "review_queue.json").exists())
            self.assertTrue((project / "v4" / "work_order_dag.json").exists())
            self.assertTrue((project / "v4" / "evidence_review_report_index.json").exists())
            self.assertTrue((project / "v4" / "traceability_refresh.json").exists())
            self.assertEqual(result["refreshed"]["work_order_dag"]["node_count"], 1)
            self.assertEqual(result["refreshed"]["evidence_review_report_index"]["evidence_count"], 1)


def _write_project(project: Path) -> None:
    project.mkdir()
    (project / "research_spec.json").write_text(json.dumps({"project_id": "demo", "disease_scope": {"canonical": "vascular aging"}}), encoding="utf-8")
    (project / "analysis_plan.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "P4_bulk_deg_ds",
                        "module": "bulk_deg",
                        "dataset_id": "ds",
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = project / "results" / "bulk_deg_ds"
    out.mkdir(parents=True)
    (out / "deg_results.tsv").write_text("gene_symbol\tlogFC\nCXCL8\t2\n", encoding="utf-8")
    (project / "reports").mkdir()
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps({"report_evidence_refs": {"CXCL8": {"score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"]}}}),
        encoding="utf-8",
    )
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
        VALUES ('ev1', 'demo', 'CXCL8', 'bulk_deg', 'ds', 'results/bulk_deg_ds/deg_results.tsv', 'run_1', 'artifact_1', 'bulk_deg_v1', 'PENDING', 'now');
        """
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
