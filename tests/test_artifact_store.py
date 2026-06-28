import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.annotation import annotate_project
from targetcompass_lite.artifact_store import (
    artifact_store_summary,
    build_download_manifest,
    load_artifact_store,
    put_artifact,
    verify_artifact,
)
from targetcompass_lite.canonical.artifacts import load_artifact_registry
from targetcompass_lite.cell_type_evidence import build_cell_type_evidence
from targetcompass_lite.database_validation import validate_online_databases
from targetcompass_lite.gene_identity_qc import assess_expression_gene_identity
from targetcompass_lite.output_backend import publish_output_artifacts


class ArtifactStoreTest(unittest.TestCase):
    def test_put_artifact_records_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "reports" / "target_report.html"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("<html>ok</html>", encoding="utf-8")

            record = put_artifact(project, "reports/target_report.html", producer="report", artifact_type="html_report")

            self.assertEqual(record["status"], "PASS")
            self.assertEqual(record["object_backend"], "local_filesystem")
            self.assertEqual(record["object_uri"], "")
            self.assertEqual(len(load_artifact_store(project)), 1)
            self.assertEqual(artifact_store_summary(project)["artifact_store_count"], 1)

    def test_minio_primary_write_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "results" / "table.tsv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            (project / "v5").mkdir(parents=True)
            (project / "v5" / "active_backends.json").write_text(
                json.dumps({"status": "ACTIVE", "active_backends": {"object_store": "minio_local"}}),
                encoding="utf-8",
            )

            with patch("targetcompass_lite.artifact_store._s3_request", return_value=200):
                record = put_artifact(project, "results/table.tsv", producer="unit", artifact_type="table")

            self.assertEqual(record["object_backend"], "minio_local")
            self.assertEqual(record["primary_write"]["status"], "PASS")
            self.assertTrue(record["object_uri"].startswith("s3://"))
            self.assertEqual(artifact_store_summary(project)["object_uri_count"], 1)

    def test_minio_download_manifest_contains_presigned_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "results" / "table.tsv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            (project / "v5").mkdir(parents=True)
            (project / "v5" / "active_backends.json").write_text(
                json.dumps({"status": "ACTIVE", "active_backends": {"object_store": "minio_local"}}),
                encoding="utf-8",
            )

            with patch("targetcompass_lite.artifact_store._s3_request", return_value=200):
                put_artifact(project, "results/table.tsv", producer="unit", artifact_type="table")
            manifest = build_download_manifest(project, relative_path="results/table.tsv")

            self.assertEqual(manifest["signed_url_status"], "ready")
            self.assertIn("X-Amz-Signature=", manifest["signed_url"])
            self.assertTrue(manifest["expires_at"])

    def test_verify_and_download_manifest_for_existing_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "results" / "table.tsv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            record = put_artifact(project, "results/table.tsv", producer="unit", artifact_type="table")

            verification = verify_artifact(project, artifact_store_id=record["artifact_store_id"])
            manifest = build_download_manifest(project, relative_path="results/table.tsv")

            self.assertEqual(verification["status"], "PASS")
            self.assertEqual(manifest["status"], "READY")
            self.assertIn("local_cache", manifest["download_modes"])
            self.assertTrue((project / "v5" / "object_store" / "last_download_manifest.json").exists())

    def test_missing_local_cache_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "results" / "table.tsv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            record = put_artifact(project, "results/table.tsv", producer="unit", artifact_type="table")
            artifact.unlink()

            verification = verify_artifact(project, artifact_store_id=record["artifact_store_id"])

            self.assertEqual(verification["status"], "RECOVERY_REQUIRED")
            self.assertTrue(verification["recovery"]["required"])

    def test_publish_output_artifacts_writes_store_and_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            artifact = project / "reports" / "target_report.html"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("<html>ok</html>", encoding="utf-8")

            summary = publish_output_artifacts(project, ["reports/target_report.html"], producer="report", artifact_type="html_report", task_id="task_report")

            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["published_count"], 1)
            self.assertTrue(summary["published"][0]["artifact_store_id"])
            self.assertEqual(len(load_artifact_store(project)), 1)
            self.assertEqual(len(load_artifact_registry(project)), 1)

    def test_annotation_outputs_are_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text(
                json.dumps({"candidate_gene_sets": {"priority": ["IL6"]}}),
                encoding="utf-8",
            )
            access_path, safety_path, review_path = annotate_project(project)

            store_paths = {row.get("relative_path", row.get("path", "")) for row in load_artifact_store(project)}

            self.assertTrue(access_path.exists())
            self.assertIn("results/annotation/accessibility_annotation.tsv", store_paths)
            self.assertIn("results/annotation/safety_flags.tsv", store_paths)
            self.assertIn("results/annotation/unknown_review.tsv", store_paths)
            self.assertTrue(safety_path.exists())
            self.assertTrue(review_path.exists())

    def test_cell_type_evidence_outputs_are_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            summary = build_cell_type_evidence(project)
            store_paths = {row.get("relative_path", row.get("path", "")) for row in load_artifact_store(project)}

            self.assertEqual(summary["row_count"], 0)
            self.assertIn("results/cell_type_evidence/cell_type_evidence.tsv", store_paths)
            self.assertIn("results/cell_type_evidence/cell_type_summary.json", store_paths)

    def test_database_validation_outputs_are_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            with patch("targetcompass_lite.database_validation._get_text", return_value="Entry\tGene Names (primary)\nP05231\tIL6\n"), patch(
                "targetcompass_lite.database_validation._get_json", return_value=[]
            ), patch("targetcompass_lite.database_validation._post_json", return_value={"data": {"search": {"hits": []}}}):
                result = validate_online_databases(project, genes=["IL6"], query="sarcopenia", limit=1, adapt=False)

            store_paths = {row.get("relative_path", row.get("path", "")) for row in load_artifact_store(project)}

            self.assertGreaterEqual(result["source_count"], 1)
            self.assertIn("results/database_validation/online_database_validation.json", store_paths)
            self.assertIn("results/database_validation/online_database_validation.tsv", store_paths)
            self.assertIn("results/database_validation/uniprot.tsv", store_paths)

    def test_gene_identity_qc_output_is_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            matrix = project / "data" / "ds1" / "expression_matrix.tsv"
            matrix.parent.mkdir(parents=True)
            matrix.write_text("gene_symbol\ts1\nIL6\t1\nCXCL8\t2\n", encoding="utf-8")
            with patch("targetcompass_lite.gene_identity_qc.ensure_hgnc_symbols", return_value={"IL6", "CXCL8"}), patch(
                "targetcompass_lite.gene_identity_qc.ensure_hgnc_mapping", return_value={}
            ):
                manifest = assess_expression_gene_identity(project, matrix, dataset_id="ds1")

            store_paths = {row.get("relative_path", row.get("path", "")) for row in load_artifact_store(project)}

            self.assertEqual(manifest["status"], "PASS")
            self.assertIn("data/ds1/gene_identity_qc.json", store_paths)


if __name__ == "__main__":
    unittest.main()
