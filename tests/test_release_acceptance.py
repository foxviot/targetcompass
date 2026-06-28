import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.release_acceptance import build_real_data_main_path_validation_matrix, build_release_acceptance_manifest


class ReleaseAcceptanceTest(unittest.TestCase):
    def test_release_acceptance_writes_scripts_and_real_data_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "analysis_main_path").mkdir(parents=True)
            (project / "v5" / "analysis_main_path" / "main_path_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "source": "geo",
                        "geo_import": {"accession": "GSE1", "expression_matrix": "data/GSE1/expression_matrix.tsv", "metadata": "data/GSE1/metadata.tsv"},
                        "stages": [
                            {"stage": "real_data_download_parse_align", "status": "completed"},
                            {"stage": "analysis_qc_evidence_report", "status": "completed"},
                        ],
                        "task_run_refs": ["v5/task_runs/task_run.json"],
                        "qc_report_refs": ["v5/qc/qc.json"],
                    }
                ),
                encoding="utf-8",
            )

            manifest = build_release_acceptance_manifest(project, question_count=5)
            matrix = manifest["real_data_validation_matrix"]

            self.assertEqual(matrix["rows"][0]["source"], "GEO")
            self.assertEqual(matrix["rows"][0]["status"], "PASS")
            self.assertTrue((project / "v5" / "platform" / "pre_release_scripts.json").exists())
            self.assertTrue((project / "v5" / "platform" / "real_data_main_path_validation_matrix.json").exists())
            self.assertIn("pre_release_script", manifest["commands"])

    def test_real_data_matrix_does_not_pass_sra_or_cellxgene_from_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            matrix = build_real_data_main_path_validation_matrix(
                project,
                main_path={},
                resource_gate={
                    "gate_items": [
                        {"source_database": "sra", "accession": "SRR1", "matrix_parse_ready": True},
                        {"source_database": "cellxgene", "accession": "CXG1", "matrix_parse_ready": True},
                    ]
                },
            )

            statuses = {row["source"]: row["status"] for row in matrix["rows"]}
            self.assertEqual(statuses["SRA"], "REVIEW")
            self.assertEqual(statuses["cellxgene"], "REVIEW")


if __name__ == "__main__":
    unittest.main()
