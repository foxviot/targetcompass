import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.matching import match_card_to_spec, match_project
from targetcompass_lite.spec_builder import update_project_spec


class DatasetMatchTest(unittest.TestCase):
    def test_huvec_matches_endothelial_senescence(self):
        spec = {
            "disease_scope": {"canonical": "endothelial senescence"},
            "organisms": ["human"],
            "priority_tissues": ["vascular endothelium"],
            "modalities_mvp": {"required": ["bulk_expression"]},
        }
        card = {
            "dataset_id": "GSE312006",
            "organism": "human",
            "tissue": "HUVEC",
            "modality": "bulk_expression",
            "contrast": {"case": "replicative_senescence", "control": "young"},
            "known_limitations": [],
        }
        row = match_card_to_spec(card, spec)
        self.assertEqual(row["match_status"], "MATCH")
        self.assertGreaterEqual(row["match_score"], 75)

    def test_lung_fibrosis_flags_huvec_for_review(self):
        spec = {
            "disease_scope": {"canonical": "pulmonary fibrosis"},
            "organisms": ["human"],
            "priority_tissues": ["lung"],
            "modalities_mvp": {"required": ["bulk_expression"]},
        }
        card = {
            "dataset_id": "GSE312006",
            "organism": "human",
            "tissue": "HUVEC",
            "modality": "bulk_expression",
            "contrast": {"case": "replicative_senescence", "control": "young"},
            "known_limitations": [],
        }
        row = match_card_to_spec(card, spec)
        self.assertIn(row["match_status"], {"REVIEW", "LOW_MATCH"})
        self.assertIn("tissue not matched", row["warnings"])

    def test_match_project_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "dataset_cards").mkdir(parents=True)
            update_project_spec(project, "human endothelial senescence secreted targets")
            (project / "dataset_cards" / "GSE312006.yaml").write_text(
                "\n".join(
                    [
                        "dataset_id: GSE312006",
                        "source: GEO",
                        "accession: GSE312006",
                        "modality: bulk_expression",
                        "organism: human",
                        "tissue: HUVEC",
                        "contrast:",
                        "  case: replicative_senescence",
                        "  control: young",
                        "sample_summary:",
                        "  case_n: 3",
                        "  control_n: 3",
                        "  donor_n: 6",
                        "metadata_fields: [sample_id, group]",
                        "matrix_available: true",
                        "license_status: public",
                        "file_paths:",
                        "  expression_matrix: data/expression_matrix.tsv",
                        "  metadata: data/metadata.tsv",
                    ]
                ),
                encoding="utf-8",
            )
            rows = match_project(project)
            self.assertEqual(rows[0]["dataset_id"], "GSE312006")
            self.assertTrue((project / "dataset_match_report.csv").exists())
            self.assertTrue((project / "dataset_match_report.md").exists())

    def test_match_project_can_limit_to_selected_datasets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "dataset_cards").mkdir(parents=True)
            update_project_spec(project, "human endothelial senescence secreted targets")
            base = "\n".join(
                [
                    "source: GEO",
                    "accession: TEST",
                    "modality: bulk_expression",
                    "organism: human",
                    "tissue: HUVEC",
                    "contrast:",
                    "  case: replicative_senescence",
                    "  control: young",
                    "sample_summary:",
                    "  case_n: 3",
                    "  control_n: 3",
                    "  donor_n: 6",
                    "metadata_fields: [sample_id, group]",
                    "matrix_available: true",
                    "license_status: public",
                    "file_paths:",
                    "  expression_matrix: data/expression_matrix.tsv",
                    "  metadata: data/metadata.tsv",
                ]
            )
            (project / "dataset_cards" / "selected.yaml").write_text("dataset_id: selected\n" + base, encoding="utf-8")
            (project / "dataset_cards" / "other.yaml").write_text("dataset_id: other\n" + base, encoding="utf-8")
            rows = match_project(project, {"selected"})
            self.assertEqual([row["dataset_id"] for row in rows], ["selected"])


if __name__ == "__main__":
    unittest.main()
