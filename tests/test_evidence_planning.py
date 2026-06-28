import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_planning import build_compatibility_decisions, build_dataset_profiles, build_evidence_plan, build_evidence_planning_bundle, build_method_contracts


class EvidencePlanningTest(unittest.TestCase):
    def test_builds_evidence_plan_and_dataset_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            evidence_plan = build_evidence_plan(project)
            self.assertTrue(evidence_plan["evidence_axes"]["SASP_annotation"])
            self.assertTrue(evidence_plan["evidence_axes"]["secreted_or_surface_annotation"])
            self.assertTrue(evidence_plan["evidence_axes"]["cell_type_specificity"])
            self.assertFalse(evidence_plan["evidence_axes"]["causal_or_genetic_support"])

            profiles = build_dataset_profiles(project)
            self.assertEqual(profiles["profile_count"], 2)
            by_id = {row["dataset_id"]: row for row in profiles["profiles"]}
            self.assertEqual(by_id["bulk_ready"]["analysis_readiness"], "ready")
            reports = {row["dataset_id"]: row for row in profiles["feasibility_reports"]}
            self.assertEqual(reports["bulk_ready"]["decision"], "pass")
            self.assertEqual(reports["reference_only"]["decision"], "exploratory_only")

            methods = build_method_contracts(project)
            self.assertEqual(methods["method_count"], 5)
            self.assertIn("bulk_deg_limma_or_countlike_v1", {row["method_id"] for row in methods["methods"]})

            compatibility = build_compatibility_decisions(project)
            decisions = {(row["dataset_id"], row["method_id"]): row for row in compatibility["decisions"]}
            self.assertEqual(decisions[("bulk_ready", "bulk_deg_limma_or_countlike_v1")]["decision"], "pass")
            self.assertEqual(decisions[("reference_only", "bulk_deg_limma_or_countlike_v1")]["decision"], "fail")
            self.assertEqual(decisions[("bulk_ready", "sasp_score_from_deg_v1")]["decision"], "repairable")
            self.assertEqual(decisions[("reference_only", "sasp_score_from_deg_v1")]["decision"], "fail")
            self.assertIn("claim_limit", decisions[("bulk_ready", "surface_secretome_annotation_v1")])

            bundle = build_evidence_planning_bundle(project)
            self.assertEqual(bundle["profile_count"], 2)
            self.assertEqual(bundle["method_count"], 5)
            self.assertIn("compatibility_summary", bundle)
            self.assertTrue((project / "results" / "evidence_planning" / "evidence_plan.json").exists())
            self.assertTrue((project / "results" / "evidence_planning" / "dataset_profiles.tsv").exists())
            self.assertTrue((project / "results" / "evidence_planning" / "dataset_feasibility.tsv").exists())
            self.assertTrue((project / "results" / "evidence_planning" / "method_contracts.json").exists())
            self.assertTrue((project / "results" / "evidence_planning" / "compatibility_decisions.tsv").exists())


def _write_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "dataset_cards").mkdir()
    (project / "data" / "bulk_ready").mkdir(parents=True)
    (project / "research_spec.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "goal": "target_prioritization",
                "research_theme": "aging skeletal muscle SASP surface marker discovery",
                "disease_scope": {"canonical": "sarcopenia", "related_phenotypes": ["aging muscle"]},
                "organisms": ["human"],
                "priority_tissues": ["skeletal muscle"],
                "priority_cells": ["fibroblast", "stromal cell"],
                "target_routes": ["surface", "secreted"],
                "modalities_mvp": {"required": ["bulk_expression"], "optional": ["single_cell", "enrichment"]},
                "constraints": {"causal_requirement": "preferred_not_mandatory"},
            }
        ),
        encoding="utf-8",
    )
    (project / "data" / "bulk_ready" / "expression_matrix.tsv").write_text(
        "gene_symbol\tS1\tS2\tS3\tS4\tS5\tS6\nIL6\t10\t12\t11\t2\t3\t2\nCXCL8\t8\t9\t10\t1\t2\t1\n",
        encoding="utf-8",
    )
    (project / "data" / "bulk_ready" / "metadata.tsv").write_text(
        "sample_id\tgroup\tbatch\tsex\tdonor_id\nS1\taged\tA\tF\tD1\nS2\taged\tA\tM\tD2\nS3\taged\tB\tF\tD3\nS4\tyoung\tA\tF\tD4\nS5\tyoung\tB\tM\tD5\nS6\tyoung\tB\tF\tD6\n",
        encoding="utf-8",
    )
    (project / "dataset_cards" / "bulk_ready.yaml").write_text(
        """dataset_id: bulk_ready
source: local
accession: TEST_BULK
modality: bulk_expression
organism: human
tissue: skeletal muscle
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 3
  control_n: 3
  donor_n: 6
metadata_fields: [sample_id, group, batch, sex, donor_id]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/bulk_ready/expression_matrix.tsv
  metadata: data/bulk_ready/metadata.tsv
known_limitations: []
recommended_use: [bulk_deg]
blocked_use: []
""",
        encoding="utf-8",
    )
    (project / "dataset_cards" / "reference_only.yaml").write_text(
        """dataset_id: reference_only
source: GEO
accession: GSE_REF
modality: transcriptomic_reference
organism: human
tissue: skeletal muscle
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 0
  control_n: 0
metadata_fields: [sample_id, age, tissue]
matrix_available: false
license_status: public
file_paths:
known_limitations: [reference card only]
recommended_use: [descriptive_reference]
blocked_use: [bulk_deg]
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
