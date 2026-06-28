import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.analysis_main_path import run_v5_analysis_main_path
from targetcompass_lite.geo_importer import GeoImportError


class CanonicalAnalysisMainPathTest(unittest.TestCase):
    def test_blocks_when_no_lockable_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            result = run_v5_analysis_main_path(project)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["selected_dataset"]["selection_mode"], "none")
            self.assertTrue((project / "v5" / "analysis_main_path" / "main_path_manifest.json").exists())
            self.assertIn("dataset_lock", result["recovery"][0]["category"])

    def test_runs_with_explicit_geo_accession_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            def fake_importer(project_dir, accession, **kwargs):
                return {
                    "accession": accession,
                    "expression_matrix": str(project_dir / "data" / accession / "expression_matrix.tsv"),
                    "metadata": str(project_dir / "data" / accession / "metadata.tsv"),
                    "case_n": 3,
                    "control_n": 3,
                }

            def fake_compile(project_dir, subquestion_id=""):
                return {"status": "compiled", "packet_count": 1, "packets": [{"task_id": "t1", "subquestion_id": subquestion_id, "packet_type": "AnalysisTaskPacket"}]}

            def fake_execute(project_dir, packets, max_packets=None):
                return {
                    "status": "completed",
                    "completed_count": 1,
                    "task_results": [{"task_run_ref": "v5/task_runs/tr1.json", "qc_report_ref": "v5/qc_reports/qc1.json"}],
                }

            def fake_report(project_dir):
                return {"status": "ready_for_signoff", "human_review_gate": {"required": False}}

            def fake_export(project_dir):
                out = project_dir / "exports" / "demo.zip"
                out.parent.mkdir(parents=True)
                out.write_text("zip", encoding="utf-8")
                return out

            result = run_v5_analysis_main_path(
                project,
                accession="GSE_TEST",
                import_geo_func=fake_importer,
                compile_func=fake_compile,
                execute_func=fake_execute,
                report_func=fake_report,
                export_func=fake_export,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["selected_dataset"]["accession"], "GSE_TEST")
            self.assertEqual(result["task_packet_count"], 1)
            self.assertEqual(result["task_run_refs"], ["v5/task_runs/tr1.json"])
            manifest = json.loads((project / "v5" / "analysis_main_path" / "main_path_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")

    def test_explicit_accession_uses_dataset_gate_group_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").write_text(
                json.dumps(
                    {
                        "resource_candidates": [
                            {
                                "resource_candidate_id": "rc1",
                                "resource_type": "dataset",
                                "source_database": "geo",
                                "accession": "GSE_LOCK",
                                "verified": True,
                                "source_status": "metadata_verified",
                            }
                        ],
                        "dataset_profiles": [
                            {
                                "resource_candidate_id": "rc1",
                                "dataset_profile_id": "dp1",
                                "modality": "bulk_expression",
                                "group_metadata_status": "not_assessed",
                                "sample_size_status": "not_assessed",
                                "organism": "unknown",
                                "tissue": "unknown",
                                "platform": "unknown",
                            }
                        ],
                        "dataset_selection_decisions": [],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl").write_text(
                json.dumps(
                    {
                        "resource_candidate_id": "rc1",
                        "group_metadata_status": "case_control_selected",
                        "sample_size_status": "sufficient",
                        "group_column": "condition",
                        "case_label": "case",
                        "control_label": "control",
                        "organism": "human",
                        "tissue": "skeletal muscle",
                        "platform": "GPLTEST",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            captured = {}

            def fake_importer(project_dir, accession, **kwargs):
                captured.update(kwargs)
                return {"accession": accession}

            def fake_compile(project_dir, subquestion_id=""):
                return {"status": "compiled", "packet_count": 1, "packets": [{"task_id": "t1", "subquestion_id": subquestion_id, "packet_type": "AnalysisTaskPacket"}]}

            def fake_execute(project_dir, packets, max_packets=None):
                return {"status": "completed", "completed_count": 1, "task_results": []}

            result = run_v5_analysis_main_path(
                project,
                accession="GSE_LOCK",
                import_geo_func=fake_importer,
                compile_func=fake_compile,
                execute_func=fake_execute,
                report_func=lambda project_dir: {"status": "ready_for_signoff"},
                export_func=lambda project_dir: project_dir / "missing.zip",
            )

            self.assertEqual(result["selected_dataset"]["selection_mode"], "explicit_accession_with_gate_context")
            self.assertEqual(captured["group_column"], "condition")
            self.assertEqual(captured["case_label"], "case")
            self.assertEqual(result["selected_route"]["analysis_module"], "bulk_deg")

    def test_geo_import_error_becomes_structured_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            def failing_importer(*args, **kwargs):
                raise GeoImportError(
                    "GEO_GROUPING_FAILED",
                    "auto_grouping",
                    "Could not infer case/control groups.",
                    ["Provide case/control hints.", "Manually upload metadata.tsv."],
                    retryable=True,
                )

            result = run_v5_analysis_main_path(project, accession="GSE_FAIL", import_geo_func=failing_importer)

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["geo_import_error"]["code"], "GEO_GROUPING_FAILED")
            self.assertIn("Provide case/control hints.", result["recovery"][0]["recovery_actions"])

    def test_non_geo_explicit_accession_skips_geo_importer(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            def fail_if_called(*args, **kwargs):
                raise AssertionError("manual accession should not call GEO importer")

            def fake_compile(project_dir, subquestion_id=""):
                return {"status": "compiled", "packet_count": 1, "packets": [{"task_id": "t1", "subquestion_id": subquestion_id, "packet_type": "AnalysisTaskPacket"}]}

            def fake_execute(project_dir, packets, max_packets=None):
                return {"status": "completed", "completed_count": 1, "task_results": []}

            result = run_v5_analysis_main_path(
                project,
                accession="ds_fixture_vascular_aging",
                source="local",
                import_geo_func=fail_if_called,
                compile_func=fake_compile,
                execute_func=fake_execute,
                report_func=lambda project_dir: {"status": "ready_for_signoff"},
                export_func=lambda project_dir: project_dir / "missing.zip",
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["selected_route"]["download_adapter"], "local")
            self.assertEqual(result["stages"][0]["status"], "skipped")

    def test_sra_accession_blocks_until_quantified_matrix_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").write_text(
                json.dumps(
                    {
                        "resource_candidates": [
                            {
                                "resource_candidate_id": "rc_sra",
                                "resource_type": "dataset",
                                "source_database": "sra",
                                "accession": "SRR1",
                                "verified": True,
                                "source_status": "metadata_verified",
                            }
                        ],
                        "dataset_profiles": [
                            {
                                "resource_candidate_id": "rc_sra",
                                "dataset_profile_id": "dp_sra",
                                "modality": "bulk_expression",
                                "group_metadata_status": "case_control_selected",
                                "sample_size_status": "sufficient",
                                "organism": "human",
                                "tissue": "skeletal muscle",
                                "platform": "Illumina",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl").write_text(
                json.dumps(
                    {
                        "resource_candidate_id": "rc_sra",
                        "group_metadata_status": "case_control_selected",
                        "sample_size_status": "sufficient",
                        "group_column": "condition",
                        "case_label": "case",
                        "control_label": "control",
                        "organism": "human",
                        "tissue": "skeletal muscle",
                        "platform": "Illumina",
                        "sample_count": "8",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_v5_analysis_main_path(project, accession="SRR1", source="sra")

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["stages"][0]["status"], "blocked")
            self.assertEqual(result["recovery"][0]["category"], "sra_adapter")
            self.assertIn("quantification manifest", result["recovery"][0]["recovery_actions"][1])

    def test_sra_accession_with_local_matrix_enters_analysis_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "data" / "SRR1").mkdir(parents=True)
            (project / "data" / "SRR1" / "expression_matrix.tsv").write_text("gene\ts1\ts2\nIL6\t1\t2\n", encoding="utf-8")
            (project / "data" / "SRR1" / "metadata.tsv").write_text("sample\tcondition\ns1\tcase\ns2\tcontrol\n", encoding="utf-8")
            (project / "data" / "SRR1" / "quantification_manifest.json").write_text(json.dumps({"tool": "salmon", "status": "completed"}), encoding="utf-8")
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").write_text(
                json.dumps(
                    {
                        "resource_candidates": [
                            {
                                "resource_candidate_id": "rc_sra",
                                "resource_type": "dataset",
                                "source_database": "sra",
                                "accession": "SRR1",
                                "verified": True,
                                "source_status": "metadata_verified",
                            }
                        ],
                        "dataset_profiles": [
                            {
                                "resource_candidate_id": "rc_sra",
                                "dataset_profile_id": "dp_sra",
                                "modality": "bulk_expression",
                                "group_metadata_status": "case_control_selected",
                                "sample_size_status": "sufficient",
                                "organism": "human",
                                "tissue": "skeletal muscle",
                                "platform": "Illumina",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl").write_text(
                json.dumps(
                    {
                        "resource_candidate_id": "rc_sra",
                        "group_metadata_status": "case_control_selected",
                        "sample_size_status": "sufficient",
                        "group_column": "condition",
                        "case_label": "case",
                        "control_label": "control",
                        "organism": "human",
                        "tissue": "skeletal muscle",
                        "platform": "Illumina",
                        "sample_count": "2",
                        "matrix_parse_ready": "true",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_compile(project_dir, subquestion_id=""):
                return {"status": "compiled", "packet_count": 1, "packets": [{"task_id": "t1", "subquestion_id": subquestion_id, "packet_type": "AnalysisTaskPacket"}]}

            def fake_execute(project_dir, packets, max_packets=None):
                return {"status": "completed", "completed_count": 1, "task_results": [{"task_run_ref": "v5/task_runs/tr_sra.json", "qc_report_ref": "v5/qc/qc_sra.json"}]}

            result = run_v5_analysis_main_path(
                project,
                accession="SRR1",
                source="sra",
                compile_func=fake_compile,
                execute_func=fake_execute,
                report_func=lambda project_dir: {"status": "ready_for_signoff"},
                export_func=lambda project_dir: project_dir / "missing.zip",
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stages"][0]["status"], "completed")
            self.assertTrue(result["adapter_input_status"]["ready"])
            self.assertEqual(result["parsed_matrix"]["quantification_manifest"], "data/SRR1/quantification_manifest.json")


if __name__ == "__main__":
    unittest.main()
