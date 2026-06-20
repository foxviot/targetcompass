import json
import socket
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.screening import screen_project
from targetcompass_lite.v4 import compile_v4_work_orders
from targetcompass_lite.webapp import _dataset_controls, _evidence_trace_detail_page, _find_available_port, _run_status, _v4_work_order_panel, _write_status


CARD = """dataset_id: ds_web
source: local
accession: WEB001
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 3
  control_n: 3
  donor_n: 6
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_web/expression_matrix.tsv
  metadata: data/ds_web/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_web").mkdir(parents=True)
    (project / "dataset_cards" / "ds_web.yaml").write_text(CARD, encoding="utf-8")
    (project / "data" / "ds_web" / "expression_matrix.tsv").write_text(
        "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_web" / "metadata.tsv").write_text(
        "sample_id\tgroup\nS1\tyoung\nS2\taged\n",
        encoding="utf-8",
    )
    return project


class WebAppTest(unittest.TestCase):
    def test_dataset_controls_render_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            html = _dataset_controls(project)
            self.assertIn('name="dataset"', html)
            self.assertIn('value="ds_web"', html)
            self.assertIn("WEB001", html)

    def test_run_status_is_persisted_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            _write_status(project, "failed", "Workflow failed.", "stdout line", "stderr line")
            status = json.loads((project / "results" / "run_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            html = _run_status(project)
            self.assertIn("Workflow failed.", html)
            self.assertIn("stdout line", html)
            self.assertIn("stderr line", html)
            self.assertIn("Stage cards", html)
            self.assertIn("Recovery center", html)
            self.assertIn("Rebuild report", html)
            self.assertIn("run_status.json", html)

    def test_screen_project_can_limit_to_selected_datasets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            (project / "dataset_cards" / "ds_other.yaml").write_text(CARD.replace("ds_web", "ds_other"), encoding="utf-8")
            rows = screen_project(project, {"ds_web"})
            self.assertEqual([row["dataset_id"] for row in rows], ["ds_web"])

    def test_find_available_port_skips_occupied_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            occupied = int(sock.getsockname()[1])
            chosen = _find_available_port("127.0.0.1", occupied, attempts=3)
            self.assertNotEqual(chosen, occupied)

    def test_find_available_port_accepts_ephemeral_port(self):
        chosen = _find_available_port("127.0.0.1", 0)
        self.assertGreater(chosen, 0)

    def test_v4_work_order_panel_exposes_codex_task_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_new_adapter_x",
                            "module": "new_external_adapter",
                            "dataset_id": "external_x",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["results/external_x/normalized.tsv"],
                            "qc_checks": ["schema validated"],
                            "allowed_files": ["targetcompass_lite/db_adapters/**"],
                        }
                    ],
                },
            )
            html = _v4_work_order_panel(project)
            self.assertIn("BUILD_ADAPTER", html)
            self.assertIn("Codex task packet", html)
            self.assertIn('name="item_type" value="work_order"', html)
            self.assertIn('name="item_type" value="codex_task"', html)

    def test_evidence_trace_detail_page_shows_trace_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v4").mkdir()
            (project / "v4" / "evidence_review_report_index.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "evidence_id": "ev1",
                                "entity_symbol": "CXCL8",
                                "evidence_type": "qtl_colocalization",
                                "source_dataset": "genetic_demo",
                                "artifact_path": "results/genetic_coloc_mr/genetic_evidence.tsv",
                                "review_status": "PENDING",
                                "review_items": [{"source": "queue", "item_type": "causal_grade", "item_id": "CXCL8", "review_status": "pending", "reason": "review", "report_ref": "reports/target_report.html#causal-grade-cxcl8"}],
                                "report_refs": [{"gene": "CXCL8", "score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"], "report_ref": "reports/target_report.html#evidence-cxcl8"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "work_order_dag.json").write_text(json.dumps({"nodes": [{"work_order_id": "wo1", "module_id": "P4", "module": "bulk_deg", "status": "success", "outputs": [{"path": "results/genetic_coloc_mr/genetic_evidence.tsv"}], "evidence_writes": [{"evidence_id": "ev1"}]}]}), encoding="utf-8")
            html = _evidence_trace_detail_page(project, evidence_id="ev1").decode("utf-8")
            for text in ["EvidenceItem", "ReviewItem", "ReportRef", "WorkOrder / DAG node", "Artifact"]:
                self.assertIn(text, html)


if __name__ == "__main__":
    unittest.main()
