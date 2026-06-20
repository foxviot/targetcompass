import json
import socket
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.screening import screen_project
from targetcompass_lite.webapp import _dataset_controls, _find_available_port, _run_status, _write_status


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


if __name__ == "__main__":
    unittest.main()
