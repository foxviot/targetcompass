import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.gene_identity_qc import assess_expression_gene_identity
from targetcompass_lite.screening import validate_bulk_files


class GeneIdentityQcTest(unittest.TestCase):
    def test_unresolved_gene_ids_fail_bulk_screening(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            expr = project / "data" / "DS" / "expression_matrix.tsv"
            meta = project / "data" / "DS" / "metadata.tsv"
            expr.parent.mkdir(parents=True)
            expr.write_text("gene_symbol\tS1\tS2\nBAD@@\t1\t2\n123-???\t3\t4\n", encoding="utf-8")
            meta.write_text("sample_id\tgroup\nS1\tcase\nS2\tcontrol\n", encoding="utf-8")
            card = {
                "dataset_id": "DS",
                "contrast": {"case": "case", "control": "control"},
                "file_paths": {"expression_matrix": "data/DS/expression_matrix.tsv", "metadata": "data/DS/metadata.tsv"},
            }
            with patch("targetcompass_lite.gene_identity_qc.ensure_hgnc_symbols", return_value={"IL6"}), patch(
                "targetcompass_lite.gene_identity_qc.ensure_hgnc_mapping", return_value={}
            ):
                errors = validate_bulk_files(card, project)

            self.assertTrue(any("gene identity unresolved" in err for err in errors))

    def test_hgnc_symbols_pass_identity_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            expr = project / "expression_matrix.tsv"
            expr.write_text("gene_symbol\tS1\nIL6\t1\nCXCL8\t2\n", encoding="utf-8")
            with patch("targetcompass_lite.gene_identity_qc.ensure_hgnc_symbols", return_value={"IL6", "CXCL8"}), patch(
                "targetcompass_lite.gene_identity_qc.ensure_hgnc_mapping", return_value={}
            ):
                qc = assess_expression_gene_identity(project, expr, "DS")

            self.assertEqual(qc["status"], "PASS")
            self.assertEqual(qc["identity_type"], "hgnc_symbol")

    def test_probe_ids_require_annotation_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            expr = project / "expression_matrix.tsv"
            expr.write_text("gene_symbol\tS1\nILMN_1\t1\nAFFX_TEST\t2\n", encoding="utf-8")
            with patch("targetcompass_lite.gene_identity_qc.ensure_hgnc_symbols", return_value=set()), patch(
                "targetcompass_lite.gene_identity_qc.ensure_hgnc_mapping", return_value={}
            ):
                qc = assess_expression_gene_identity(project, expr, "DS")

            self.assertEqual(qc["status"], "REVIEW")
            self.assertEqual(qc["identity_type"], "platform_probe_id_requires_annotation")
