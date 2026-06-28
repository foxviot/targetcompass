import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import register_artifact
from targetcompass_lite.canonical.matrix_path_validation import build_matrix_path_validation


class CanonicalMatrixPathValidationTest(unittest.TestCase):
    def test_sra_metadata_only_stays_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "v5" / "resource_discovery" / "resource_gate_report.json").write_text(
                json.dumps(
                    {
                        "gate_items": [
                            {
                                "source_database": "sra",
                                "accession": "SRP1",
                                "gate_status": "analysis_ready_after_review",
                                "matrix_parse_ready": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_matrix_path_validation(project)

            self.assertEqual(report["status"], "REVIEW")
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["rows"][0]["checks"][0]["status"], "REVIEW")

    def test_sra_full_matrix_path_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            data_dir = project / "data" / "SRP1"
            data_dir.mkdir(parents=True)
            (data_dir / "expression_matrix.tsv").write_text("gene_symbol\tS1\tS2\nIL6\t1\t2\n", encoding="utf-8")
            (data_dir / "metadata.tsv").write_text("sample_id\tgroup\nS1\tcase\nS2\tcontrol\n", encoding="utf-8")
            (data_dir / "quantification_manifest.json").write_text(json.dumps({"tool": "salmon", "status": "completed"}), encoding="utf-8")
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "v5" / "resource_discovery" / "resource_gate_report.json").write_text(
                json.dumps(
                    {
                        "gate_items": [
                            {
                                "source_database": "sra",
                                "accession": "SRP1",
                                "gate_status": "datasets_locked_ready",
                                "matrix_parse_ready": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            for rel, artifact_type in [
                ("data/SRP1/expression_matrix.tsv", "parsed_expression_matrix"),
                ("data/SRP1/metadata.tsv", "parsed_sample_metadata"),
                ("data/SRP1/quantification_manifest.json", "sra_quantification_manifest"),
            ]:
                register_artifact(project, rel, "unit", artifact_type, ["task_sra"], ["sq1"], qc_status="pass")
            (project / "v5" / "analysis_main_path").mkdir(parents=True)
            (project / "v5" / "analysis_main_path" / "main_path_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "selected_dataset": {"accession": "SRP1"},
                        "task_run_refs": ["v5/task_runs/tr1.json"],
                        "qc_report_refs": ["v5/qc_reports/qc1.json"],
                        "canonical_report_manifest_ref": "v5/reports/canonical_report_manifest.json",
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "reports").mkdir(parents=True)
            (project / "v5" / "reports" / "canonical_report_manifest.json").write_text(json.dumps({"status": "ready_for_signoff"}), encoding="utf-8")

            report = build_matrix_path_validation(project)

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["pass_count"], 1)
            self.assertTrue(all(row["status"] == "PASS" for row in report["rows"][0]["checks"]))


if __name__ == "__main__":
    unittest.main()
