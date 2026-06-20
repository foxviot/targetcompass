import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.screening import metadata_quality, screen_card, source_class, validate_bulk_files


CARD = """dataset_id: ds_test
source: local
accession: TEST
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
  expression_matrix: data/expression_matrix.tsv
  metadata: data/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def write_project(tmp: str, matrix: str, metadata: str) -> tuple[Path, Path]:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data").mkdir()
    card_path = project / "dataset_cards" / "ds_test.yaml"
    card_path.write_text(CARD, encoding="utf-8")
    (project / "data" / "expression_matrix.tsv").write_text(matrix, encoding="utf-8")
    (project / "data" / "metadata.tsv").write_text(metadata, encoding="utf-8")
    return project, card_path


class ScreeningTest(unittest.TestCase):
    def test_valid_bulk_files_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, card_path = write_project(
                tmp,
                "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
                "sample_id\tgroup\nS1\tyoung\nS2\taged\n",
            )
            row = screen_card(card_path, project)
            self.assertIn(row["grade"], {"A", "B"})
            self.assertEqual(row["metadata_quality_label"], "medium")

    def test_metadata_quality_and_source_class_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, card_path = write_project(
                tmp,
                "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
                "sample_id\tgroup\tbatch\tsex\tage\nS1\tyoung\tA\tF\t31\nS2\taged\tB\tM\t70\n",
            )
            card = {
                "source": "GEO",
                "accession": "GSE123",
                "file_paths": {"metadata": "data/metadata.tsv"},
            }
            quality = metadata_quality(card, project)
            self.assertEqual(quality["label"], "high")
            self.assertEqual(source_class(card), "real_public")

    def test_mismatched_samples_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, card_path = write_project(
                tmp,
                "gene_symbol\tS1\tS3\nIL6\t1\t2\n",
                "sample_id\tgroup\nS1\tyoung\nS2\taged\n",
            )
            row = screen_card(card_path, project)
            self.assertEqual(row["grade"], "D")
            self.assertIn("sample columns do not match", row["reasons"])

    def test_missing_group_column_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, card_path = write_project(
                tmp,
                "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
                "sample_id\tcondition\nS1\tyoung\nS2\taged\n",
            )
            row = screen_card(card_path, project)
            self.assertEqual(row["grade"], "D")
            self.assertIn("metadata missing required column: group", row["reasons"])

    def test_missing_file_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, card_path = write_project(
                tmp,
                "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
                "sample_id\tgroup\nS1\tyoung\nS2\taged\n",
            )
            (project / "data" / "metadata.tsv").unlink()
            row = screen_card(card_path, project)
            self.assertEqual(row["grade"], "D")
            self.assertIn("metadata file not found", row["reasons"])

    def test_unknown_license_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, card_path = write_project(
                tmp,
                "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
                "sample_id\tgroup\nS1\tyoung\nS2\taged\n",
            )
            card_path.write_text(CARD.replace("license_status: public", "license_status: unknown"), encoding="utf-8")
            row = screen_card(card_path, project)
            self.assertEqual(row["grade"], "D")
            self.assertIn("license is not public or authorized", row["reasons"])


if __name__ == "__main__":
    unittest.main()
