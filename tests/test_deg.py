import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.deg import _check_design_matrix, _select_deg_runner, run_deg


CARD = """dataset_id: ds_deg
source: local
accession: DEG001
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 2
  control_n: 2
metadata_fields: [sample_id, group, batch]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_deg/expression_matrix.tsv
  metadata: data/ds_deg/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(tmp: str, metadata: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_deg").mkdir(parents=True)
    (project / "dataset_cards" / "ds_deg.yaml").write_text(CARD, encoding="utf-8")
    (project / "data" / "ds_deg" / "expression_matrix.tsv").write_text(
        "gene_symbol\tY1\tY2\tA1\tA2\nIL6\t1\t2\t8\t9\nCXCL8\t2\t2\t10\t11\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_deg" / "metadata.tsv").write_text(metadata, encoding="utf-8")
    return project


class DegTest(unittest.TestCase):
    def test_formal_limma_runner_script_exists(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "r" / "bulk_limma_deg.R"
        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")
        self.assertIn("library(limma)", text)
        self.assertIn("design matrix is rank deficient", text)

    def test_design_matrix_records_batch_covariate(self):
        rows = [
            {"sample_id": "Y1", "group": "young", "batch": "A"},
            {"sample_id": "Y2", "group": "young", "batch": "B"},
            {"sample_id": "A1", "group": "aged", "batch": "A"},
            {"sample_id": "A2", "group": "aged", "batch": "B"},
        ]
        design = _check_design_matrix(rows, "aged", "young")
        self.assertTrue(design["full_rank"])
        self.assertEqual(design["batch_covariates"], ["batch"])

    def test_rank_deficient_batch_design_drops_confounded_batch(self):
        rows = [
            {"sample_id": "Y1", "group": "young", "batch": "A"},
            {"sample_id": "Y2", "group": "young", "batch": "A"},
            {"sample_id": "A1", "group": "aged", "batch": "B"},
            {"sample_id": "A2", "group": "aged", "batch": "B"},
        ]
        design = _check_design_matrix(rows, "aged", "young")
        self.assertTrue(design["full_rank"])
        self.assertEqual(design["batch_covariates"], [])
        self.assertEqual(design["dropped_batch_covariates"], ["batch"])
        self.assertTrue(design["warnings"])

    def test_runner_auto_falls_back_when_rscript_unavailable(self):
        with patch("targetcompass_lite.deg._find_rscript", return_value=None), patch.dict(
            "os.environ", {}, clear=True
        ):
            runner = _select_deg_runner()
        self.assertEqual(runner["runner_type"], "python_fallback")
        self.assertIn("Rscript not found", runner["reason"])

    def test_runner_can_force_python_fallback(self):
        with patch.dict("os.environ", {"TARGETCOMPASS_DEG_RUNNER": "python"}):
            runner = _select_deg_runner()
        self.assertEqual(runner["runner_type"], "python_fallback")
        self.assertIn("forced", runner["reason"])

    def test_forced_formal_runner_requires_r_limma(self):
        with patch("targetcompass_lite.deg._find_rscript", return_value=None), patch.dict(
            "os.environ", {"TARGETCOMPASS_DEG_RUNNER": "formal"}
        ):
            with self.assertRaisesRegex(RuntimeError, "formal DEG runner requested"):
                _select_deg_runner()

    def test_run_manifest_includes_design_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(
                tmp,
                "sample_id\tgroup\tbatch\nY1\tyoung\tA\nY2\tyoung\tB\nA1\taged\tA\nA2\taged\tB\n",
            )
            with patch.dict("os.environ", {"TARGETCOMPASS_DEG_RUNNER": "python"}):
                run_deg(project, "ds_deg")
            manifest = json.loads(
                (project / "results" / "bulk_deg_ds_deg" / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["runner_type"], "python_fallback")
            self.assertEqual(manifest["formal_runner"], "scripts/r/bulk_limma_deg.R")
            self.assertEqual(manifest["parameters"]["batch_covariates"], ["batch"])
            self.assertTrue(manifest["design"]["full_rank"])

    def test_formal_limma_failure_falls_back_to_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(
                tmp,
                "sample_id\tgroup\tbatch\nY1\tyoung\tA\nY2\tyoung\tB\nA1\taged\tA\nA2\taged\tB\n",
            )
            with patch("targetcompass_lite.deg._select_deg_runner", return_value={"runner_type": "r_limma", "reason": "test"}), patch(
                "targetcompass_lite.deg._run_formal_limma", side_effect=RuntimeError("limma failed")
            ):
                result = run_deg(project, "ds_deg")
            self.assertTrue(result.exists())
            manifest = json.loads(
                (project / "results" / "bulk_deg_ds_deg" / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["runner_type"], "python_fallback")
            self.assertIn("formal limma failed", manifest["runner_reason"])


if __name__ == "__main__":
    unittest.main()
