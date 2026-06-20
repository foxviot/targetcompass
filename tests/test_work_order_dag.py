import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.mcp_gateway import build_mcp_gateway
from targetcompass_lite.v4 import build_v4_manifest, compile_v4_work_orders, finish_work_order_attempt, start_work_order_attempt
from targetcompass_lite.webapp import _v4_work_order_panel
from targetcompass_lite.work_order_dag import build_work_order_dag, load_work_order_dag


class WorkOrderDagTest(unittest.TestCase):
    def test_work_order_dag_unifies_io_status_and_evidence_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text(
                json.dumps(
                    {
                        "project_id": "demo",
                        "research_theme": "vascular aging",
                        "disease_scope": {"canonical": "vascular aging"},
                        "organisms": ["human"],
                        "priority_tissues": ["artery"],
                        "priority_cells": ["endothelial cell"],
                        "target_routes": ["secreted"],
                    }
                ),
                encoding="utf-8",
            )
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            plan = {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "P4_bulk_deg_ds",
                        "module": "bulk_deg",
                        "dataset_id": "ds",
                        "inputs": {"dataset_card": "dataset_cards/ds.yaml"},
                        "parameters": {"case": "case", "control": "control"},
                        "expected_outputs": ["results/bulk_deg_ds/deg_results.tsv"],
                        "qc_checks": ["manifest exists"],
                    }
                ],
            }
            (project / "analysis_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            compile_v4_work_orders(project, plan)
            out = project / "results" / "bulk_deg_ds"
            out.mkdir(parents=True)
            (out / "deg_results.tsv").write_text("gene_symbol\tlogFC\nCXCL8\t2\n", encoding="utf-8")
            attempt = start_work_order_attempt(project, "P4_bulk_deg_ds", "run_1")
            finish_work_order_attempt(project, attempt["attempt_id"], "success", ["results/bulk_deg_ds/deg_results.tsv"])
            _write_evidence_db(project)

            dag = build_work_order_dag(project)
            self.assertEqual(dag["schema_version"], "v4.work_order_dag/0.1")
            self.assertEqual(dag["node_count"], 1)
            node = dag["nodes"][0]
            self.assertEqual(node["status"], "success")
            self.assertIn("declared", node["inputs"])
            self.assertTrue(node["outputs"][0]["exists"])
            self.assertEqual(node["evidence_writes"][0]["entity_symbol"], "CXCL8")
            self.assertEqual(load_work_order_dag(project)["nodes"][0]["work_order_id"], node["work_order_id"])

            manifest = build_v4_manifest(project, plan)
            self.assertIn("work_order_dag", manifest["objects"])
            gateway = build_mcp_gateway(project)
            self.assertIn("work-order-dag://demo/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertIn("WorkOrder DAG", _v4_work_order_panel(project))


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
        (evidence_id, project_id, entity_symbol, evidence_type, source_dataset, artifact_path, run_id, artifact_id, module_version, created_at)
        VALUES ('ev1', 'demo', 'CXCL8', 'bulk_deg', 'ds', 'results/bulk_deg_ds/deg_results.tsv', 'run_1', 'artifact_1', 'bulk_deg_v1', 'now');
        """
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
