import csv
import unittest
from pathlib import Path

from targetcompass_lite.validators import validate_dataset_card


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "vascular_aging_demo"


class GSE43292IntegrationTest(unittest.TestCase):
    def test_gse43292_card_and_prepared_files_exist(self):
        card = PROJECT / "dataset_cards" / "GSE43292.yaml"
        matrix = PROJECT / "data" / "GSE43292" / "expression_matrix.tsv"
        metadata = PROJECT / "data" / "GSE43292" / "metadata.tsv"
        self.assertTrue(card.exists())
        self.assertTrue(matrix.exists())
        self.assertTrue(metadata.exists())
        self.assertEqual(validate_dataset_card(card), [])

    def test_gse43292_metadata_is_paired_carotid_tissue(self):
        metadata = PROJECT / "data" / "GSE43292" / "metadata.tsv"
        with metadata.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(len(rows), 64)
        self.assertEqual({row["group"] for row in rows}, {"atheroma_plaque", "intact_carotid"})
        self.assertEqual(len({row["patient_id"] for row in rows}), 32)


if __name__ == "__main__":
    unittest.main()
