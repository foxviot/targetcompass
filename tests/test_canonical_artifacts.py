import tempfile
import unittest
from pathlib import Path
from unittest import mock

from targetcompass_lite.canonical.artifacts import (
    build_artifact_manifest,
    compute_file_sha256,
    load_artifact_registry,
    register_artifact,
    validate_artifact_for_evidence,
    write_artifact_manifest,
)


class CanonicalArtifactsTest(unittest.TestCase):
    def test_small_file_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.txt"
            path.write_text("hello\n", encoding="utf-8")
            self.assertEqual(compute_file_sha256(path), "cd2eca3535741f27a8ae40c31b0c41d4057a7a7b912b33b9aed86485d1c84676")

    def test_missing_file_manifest_cannot_support_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            manifest = build_artifact_manifest(project_dir, "missing.tsv", "task_a", "table", ["task_a"], ["sq1"])
            self.assertFalse(manifest["exists"])
            self.assertEqual(manifest["checksum_sha256"], "")
            self.assertIn("exists=false", " ".join(validate_artifact_for_evidence(manifest)))

    def test_placeholder_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            data_path = project_dir / "results" / "placeholder.tsv"
            data_path.parent.mkdir(parents=True)
            data_path.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            manifest = build_artifact_manifest(
                project_dir,
                "results/placeholder.tsv",
                "task_a",
                "table",
                ["task_a"],
                ["sq1"],
                is_placeholder=True,
            )
            self.assertTrue(manifest["exists"])
            self.assertIn("is_placeholder=true", " ".join(validate_artifact_for_evidence(manifest)))

    def test_checksum_changes_when_file_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            data_path = project_dir / "results" / "data.tsv"
            data_path.parent.mkdir(parents=True)
            data_path.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            first = build_artifact_manifest(project_dir, "results/data.tsv", "task_a", "table", ["task_a"], ["sq1"])
            data_path.write_text("gene\tvalue\nA\t2\n", encoding="utf-8")
            second = build_artifact_manifest(project_dir, "results/data.tsv", "task_a", "table", ["task_a"], ["sq1"])
            self.assertNotEqual(first["checksum_sha256"], second["checksum_sha256"])
            self.assertNotEqual(first["artifact_id"], second["artifact_id"])

    def test_csv_and_tsv_profile_records_rows_and_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            csv_path = project_dir / "results" / "scores.csv"
            tsv_path = project_dir / "results" / "scores.tsv"
            csv_path.parent.mkdir(parents=True)
            csv_path.write_text("gene,score\nA,1\nB,2\n", encoding="utf-8")
            tsv_path.write_text("gene\tscore\nA\t1\n", encoding="utf-8")
            csv_manifest = build_artifact_manifest(project_dir, "results/scores.csv", "task_a", "score_table", ["task_a"], ["sq1"])
            tsv_manifest = build_artifact_manifest(project_dir, "results/scores.tsv", "task_a", "score_table", ["task_a"], ["sq1"])
            self.assertEqual(csv_manifest["column_names"], ["gene", "score"])
            self.assertEqual(csv_manifest["row_count"], 2)
            self.assertEqual(tsv_manifest["column_names"], ["gene", "score"])
            self.assertEqual(tsv_manifest["row_count"], 1)

    def test_artifact_registry_appends_and_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            data_path = project_dir / "results" / "a.tsv"
            data_path.parent.mkdir(parents=True)
            data_path.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            first = register_artifact(project_dir, "results/a.tsv", "task_a", "table", ["task_a"], ["sq1"])
            data_path.write_text("gene\tvalue\nA\t2\n", encoding="utf-8")
            second = register_artifact(project_dir, "results/a.tsv", "task_a", "table", ["task_a"], ["sq1"])
            registry = load_artifact_registry(project_dir)
            self.assertEqual(len(registry), 2)
            self.assertEqual(registry[0]["artifact_id"], first["artifact_id"])
            self.assertEqual(registry[1]["artifact_id"], second["artifact_id"])

    def test_write_artifact_manifest_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            manifest = {
                "artifact_id": "artifact_manual",
                "project_id": "demo_project",
                "path": "manual.txt",
                "artifact_type": "text",
                "producer_agent_or_task": "tester",
                "producer_run_id": "run1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "checksum_sha256": "abc",
                "size_bytes": 3,
                "exists": True,
                "schema_name": "text",
                "expected_by_task_ids": [],
                "supports_subquestion_ids": [],
                "evidence_item_refs": [],
                "qc_status": "pending",
                "limitations": [],
                "is_placeholder": False,
            }
            write_artifact_manifest(project_dir, manifest)
            write_artifact_manifest(project_dir, manifest)
            self.assertEqual(len(load_artifact_registry(project_dir)), 2)

    def test_write_artifact_manifest_retries_transient_oserror(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            manifest = {
                "artifact_id": "artifact_retry",
                "project_id": "demo_project",
                "path": "manual.txt",
                "artifact_type": "text",
                "producer_agent_or_task": "tester",
                "producer_run_id": "run1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "checksum_sha256": "abc",
                "size_bytes": 3,
                "exists": True,
                "schema_name": "text",
                "expected_by_task_ids": [],
                "supports_subquestion_ids": [],
                "evidence_item_refs": [],
                "qc_status": "pending",
                "limitations": [],
                "is_placeholder": False,
            }
            from targetcompass_lite.canonical import artifacts

            real_append = artifacts._append_jsonl_atomic
            calls = {"count": 0}

            def flaky_append(path, line):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise OSError(22, "transient invalid argument")
                return real_append(path, line)

            with mock.patch.object(artifacts, "_append_jsonl_atomic", side_effect=flaky_append):
                write_artifact_manifest(project_dir, manifest)

            self.assertEqual(calls["count"], 2)
            self.assertEqual(len(load_artifact_registry(project_dir)), 1)


if __name__ == "__main__":
    unittest.main()
