import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.registry_snapshots import build_registry_snapshots
from targetcompass_lite.scoring import score_project
from targetcompass_lite.v4 import build_v4_manifest
from targetcompass_lite.webapp import _v4_work_order_panel


class RegistrySnapshotTest(unittest.TestCase):
    def test_registry_snapshots_capture_methods_sources_and_rubric(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "configs").mkdir()
            source = project / "source.tsv"
            source.write_text("gene_symbol\troute\nIL6\tsecreted\n", encoding="utf-8")
            (project / "configs" / "knowledge_registry.json").write_text(
                json.dumps(
                    [
                        {
                            "resource_id": "demo_source",
                            "resource_type": "annotation_table",
                            "source_path": str(source),
                            "adapter": "copy",
                            "status": "registered",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            snapshot = build_registry_snapshots(project)
            self.assertEqual(snapshot["schema_version"], "v4.registry_snapshots/0.1")
            self.assertTrue(snapshot["snapshot_hash"])
            self.assertGreater(snapshot["snapshots"]["method_registry"]["method_count"], 0)
            self.assertEqual(snapshot["snapshots"]["source_registry"]["resource_count"], 1)
            self.assertTrue(snapshot["snapshots"]["rubric"]["hash"])

            (project / "research_spec.json").write_text(json.dumps({"project_id": "demo"}), encoding="utf-8")
            (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")
            manifest = build_v4_manifest(project)
            self.assertEqual(manifest["objects"]["registry_snapshots"]["path"], "v4/registry_snapshots.json")
            html = _v4_work_order_panel(project)
            self.assertIn("Registry snapshots", html)
            self.assertIn("Method Registry", html)

    def test_score_manifest_references_rubric_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "results" / "annotation").mkdir(parents=True)
            _write_evidence_db(project)
            (project / "results" / "annotation" / "accessibility_annotation.tsv").write_text(
                "gene_symbol\troute\taccessibility_status\tsource\nIL6\tsecreted\tSUPPORTED\tfixture\n",
                encoding="utf-8",
            )
            (project / "results" / "annotation" / "safety_flags.tsv").write_text(
                "gene_symbol\tsafety_gate\tcritical_tissue_flag\tnote\nIL6\tPASS\t\tfixture\n",
                encoding="utf-8",
            )
            score_project(project)
            manifest = json.loads((project / "results" / "scoring" / "target_score_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["registry_snapshot"], "v4/registry_snapshots.json")
            self.assertTrue(manifest["rubric_snapshot_hash"])


def _write_evidence_db(project: Path) -> None:
    con = sqlite3.connect(project / "evidence.sqlite")
    con.execute(
        """
        CREATE TABLE evidence_item (
            evidence_id TEXT,
            entity_symbol TEXT,
            evidence_type TEXT,
            source_dataset TEXT,
            direction TEXT,
            effect_size REAL,
            p_value REAL,
            adjusted_p_value REAL,
            artifact_path TEXT,
            limitation TEXT,
            review_status TEXT,
            run_id TEXT,
            artifact_id TEXT,
            module_version TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO evidence_item VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ev1", "IL6", "bulk_deg", "fixture", "up", 2.0, 0.001, 0.01, "x", "fixture", "PENDING", "run1", "artifact1", "v1"),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
