import csv
import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.cell_type_evidence import build_cell_type_evidence
from targetcompass_lite.recovery_center import build_recovery_manifest


class RecoveryAndCellTypeTest(unittest.TestCase):
    def test_recovery_manifest_records_database_and_fulltext_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            db_dir = project / "results" / "database_validation"
            ft_dir = project / "results" / "fulltext_literature"
            db_dir.mkdir(parents=True)
            ft_dir.mkdir(parents=True)
            (db_dir / "online_database_validation.json").write_text(
                json.dumps(
                    {
                        "sources": [
                            {"source_id": "hpa", "status": "failed", "message": "timeout"},
                            {"source_id": "disgenet", "status": "requires_credentials", "message": "license required"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (ft_dir / "fulltext_literature_run.json").write_text(
                json.dumps({"failures": [{"source": "123", "reason": "no PMC Open Access full text found"}]}),
                encoding="utf-8",
            )

            manifest = build_recovery_manifest(project)

            self.assertEqual(manifest["open_count"], 3)
            self.assertEqual(manifest["retryable_count"], 2)
            item_ids = {row["item_id"] for row in manifest["items"]}
            self.assertIn("database:hpa", item_ids)
            self.assertIn("database:disgenet", item_ids)
            self.assertIn("fulltext:123", item_ids)
            self.assertTrue((project / "results" / "recovery" / "recovery_manifest.json").exists())

    def test_cellmarker_like_resource_becomes_cell_type_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            marker = project / "cellmarker.tsv"
            marker.parent.mkdir(parents=True)
            with marker.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["gene_symbol", "cell_type", "tissue", "confidence"], delimiter="\t")
                writer.writeheader()
                writer.writerow({"gene_symbol": "IL6", "cell_type": "macrophage", "tissue": "skeletal muscle", "confidence": "0.9"})
            configs = project / "configs"
            configs.mkdir()
            (configs / "knowledge_registry.json").write_text(
                json.dumps(
                    [
                        {
                            "resource_id": "cellmarker_demo",
                            "resource_type": "external_database",
                            "source_path": str(marker),
                            "adapter": "cellmarker_marker_v0",
                            "status": "registered",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            summary = build_cell_type_evidence(project)

            self.assertEqual(summary["row_count"], 1)
            self.assertEqual(summary["cell_type_by_gene"]["IL6"][0]["cell_type"], "macrophage")
            text = (project / "results" / "cell_type_evidence" / "cell_type_evidence.tsv").read_text(encoding="utf-8")
            self.assertIn("marker_database_cell_type", text)


if __name__ == "__main__":
    unittest.main()
