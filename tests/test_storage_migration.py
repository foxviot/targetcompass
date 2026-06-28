import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.artifact_store import load_artifact_store
from targetcompass_lite.storage_migration import build_demo_slim_storage_manifest, build_storage_migration_plan, load_storage_migration_history, load_storage_migration_plan, migrate_legacy_outputs_to_primary_backends


class StorageMigrationTest(unittest.TestCase):
    def test_plan_and_migrate_register_legacy_outputs_and_syncs_evidence_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "results" / "bulk_deg_ds").mkdir(parents=True)
            (project / "reports").mkdir()
            (project / "results" / "bulk_deg_ds" / "deg_results.tsv").write_text("gene_symbol\tlogFC\nIL6\t1.2\n", encoding="utf-8")
            (project / "reports" / "target_report.html").write_text("<html>report</html>", encoding="utf-8")
            _write_evidence(project)

            plan = build_storage_migration_plan(project)
            run = migrate_legacy_outputs_to_primary_backends(project, limit=10)
            after = build_storage_migration_plan(project)
            store_paths = {row.get("relative_path") for row in load_artifact_store(project)}

            self.assertEqual(plan["schema_version"], "v5.storage_migration_plan/0.1")
            self.assertGreaterEqual(plan["artifact_store_missing_count"], 2)
            self.assertGreaterEqual(run["migrated_artifact_count"], 2)
            self.assertIn("results/bulk_deg_ds/deg_results.tsv", store_paths)
            self.assertIn("reports/target_report.html", store_paths)
            self.assertLess(after["artifact_store_missing_count"], plan["artifact_store_missing_count"])
            self.assertIn("migration_progress", after)
            self.assertGreater(after["history_summary"]["batch_count"], 0)
            self.assertTrue(after["primary_path_gaps"])
            self.assertEqual(load_storage_migration_history(project)[-1]["status"], run["status"])
            self.assertTrue((project / "v5" / "platform" / "storage_migration_last_run.json").exists())

    def test_load_plan_uses_cached_manifest_without_rescanning(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "platform").mkdir(parents=True)
            cached = {
                "schema_version": "v5.storage_migration_plan/0.1",
                "project_id": "demo",
                "status": "CACHED_TEST",
                "artifact_store_missing_count": 123,
            }
            (project / "v5" / "platform" / "storage_migration_plan.json").write_text(json.dumps(cached), encoding="utf-8")

            loaded = load_storage_migration_plan(project)

            self.assertEqual(loaded["status"], "CACHED_TEST")
            self.assertEqual(loaded["cache_policy"]["mode"], "cached")

    def test_demo_slim_storage_registers_effective_outputs_and_excludes_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "reports").mkdir(parents=True)
            (project / "results" / "evidence_planning").mkdir(parents=True)
            (project / "results" / "old_batch_001").mkdir(parents=True)
            (project / "v5" / "task_runs").mkdir(parents=True)
            (project / "reports" / "target_report.html").write_text("<html>report</html>", encoding="utf-8")
            (project / "results" / "evidence_planning" / "evidence_plan.json").write_text("{}", encoding="utf-8")
            (project / "results" / "old_batch_001" / "debug.tsv").write_text("x\n", encoding="utf-8")
            (project / "v5" / "task_runs" / "run1.json").write_text("{}", encoding="utf-8")

            manifest = build_demo_slim_storage_manifest(project)
            store_paths = {row.get("relative_path") for row in load_artifact_store(project)}

            self.assertEqual(manifest["schema_version"], "v5.demo_slim_storage_manifest/0.1")
            self.assertEqual(manifest["status"], "PASS")
            self.assertIn("reports/target_report.html", store_paths)
            self.assertIn("results/evidence_planning/evidence_plan.json", store_paths)
            self.assertIn("v5/task_runs/run1.json", store_paths)
            self.assertNotIn("results/old_batch_001/debug.tsv", store_paths)
            self.assertEqual(manifest["excluded_historical_legacy_count"], 1)
            self.assertTrue((project / "v5" / "platform" / "demo_slim_storage_manifest.json").exists())

    def test_migrate_uses_full_missing_list_not_plan_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "results" / "many").mkdir(parents=True)
            for idx in range(505):
                (project / "results" / "many" / f"artifact_{idx:03d}.txt").write_text(str(idx), encoding="utf-8")
            _write_evidence(project)

            run = migrate_legacy_outputs_to_primary_backends(project, limit=505, sync_evidence=False)
            store_paths = {row.get("relative_path") for row in load_artifact_store(project)}

            self.assertEqual(run["full_missing_before_count"], 506)
            self.assertEqual(run["migrated_artifact_count"], 505)
            self.assertIn("results/many/artifact_504.txt", store_paths)


def _write_evidence(project: Path) -> None:
    con = sqlite3.connect(project / "evidence.sqlite")
    con.execute(
        """
        CREATE TABLE evidence_item (
            evidence_id TEXT, project_id TEXT, entity_symbol TEXT, evidence_type TEXT, review_status TEXT
        )
        """
    )
    con.execute("INSERT INTO evidence_item VALUES ('ev1', 'demo', 'IL6', 'bulk_deg', 'PENDING')")
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
