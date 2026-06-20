import csv
import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.planning import build_plan


BULK_CARD = """dataset_id: ds_bulk
source: local
accession: BULK001
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 3
  control_n: 3
  donor_n: 6
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_bulk/expression_matrix.tsv
  metadata: data/ds_bulk/metadata.tsv
known_limitations: [small sample size]
recommended_use: [bulk_deg]
blocked_use: []
"""


DESCRIPTIVE_CARD = """dataset_id: ds_context
source: local
accession: CTX001
modality: proteomics
organism: human
tissue: artery
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 2
  control_n: 2
  donor_n: 4
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths: {}
known_limitations: [descriptive-only fixture]
recommended_use: [descriptive_evidence]
blocked_use: [bulk_deg]
"""


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    cards = project / "dataset_cards"
    cards.mkdir(parents=True)
    bulk_card = cards / "ds_bulk.yaml"
    descriptive_card = cards / "ds_context.yaml"
    bulk_card.write_text(BULK_CARD, encoding="utf-8")
    descriptive_card.write_text(DESCRIPTIVE_CARD, encoding="utf-8")
    with (project / "eligible_datasets.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["dataset_id", "grade", "modality", "recommended_use", "path", "reasons"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "dataset_id": "ds_bulk",
                "grade": "A",
                "modality": "bulk_expression",
                "recommended_use": "bulk_deg",
                "path": str(bulk_card),
                "reasons": "bulk expression dataset is analyzable",
            }
        )
        writer.writerow(
            {
                "dataset_id": "ds_context",
                "grade": "C",
                "modality": "proteomics",
                "recommended_use": "descriptive_evidence",
                "path": str(descriptive_card),
                "reasons": "non-bulk dataset kept as descriptive evidence",
            }
        )
    return project


class PlanningTest(unittest.TestCase):
    def test_plan_contains_executable_bulk_work_order_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            plan = build_plan(project)
            module = next(m for m in plan["modules"] if m["module"] == "bulk_deg")
            self.assertEqual(module["module_id"], "P4_bulk_deg_ds_bulk")
            self.assertEqual(module["parameters"]["case"], "aged")
            self.assertIn("expression_matrix", module["inputs"])
            self.assertIn("qc_checks", module)
            self.assertIn("run_manifest records input hashes", module["qc_checks"])
            self.assertIn(module["module_id"], plan["execution_order"])
            work_order = project / "work_orders" / "P4_bulk_deg_ds_bulk.md"
            self.assertTrue(work_order.exists())
            text = work_order.read_text(encoding="utf-8")
            self.assertIn("## QC Checks", text)
            self.assertIn("python tc_lite.py run-deg --project demo --dataset ds_bulk", text)
            v4_index = project / "v4" / "work_orders.json"
            self.assertTrue(v4_index.exists())
            v4_orders = json.loads(v4_index.read_text(encoding="utf-8"))["work_orders"]
            bulk_order = next(order for order in v4_orders if order["dataset_id"] == "ds_bulk")
            self.assertEqual(bulk_order["work_order_type"], "RUN_REGISTERED_MODULE")
            self.assertEqual(bulk_order["target_backend"], "temporal_nextflow_compatible")
            self.assertFalse(bulk_order["requires_codex"])
            self.assertTrue((project / "v4" / "object_manifest.json").exists())
            self.assertTrue((project / "v4" / "mcp_resources.json").exists())

    def test_plan_keeps_c_grade_non_bulk_as_descriptive_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            plan = build_plan(project)
            module = next(m for m in plan["modules"] if m["dataset_id"] == "ds_context")
            self.assertEqual(module["module"], "descriptive_evidence")
            self.assertEqual(module["runner"], "manual_review")
            self.assertIn("no causal or DEG claim is made from descriptive-only evidence", module["qc_checks"])
            work_order = project / "work_orders" / "P3_descriptive_evidence_ds_context.md"
            self.assertTrue(work_order.exists())


if __name__ == "__main__":
    unittest.main()
