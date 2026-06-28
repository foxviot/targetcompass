import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_db import _validate_evidence
from targetcompass_lite.schema_validation import SCHEMAS, load_schema, validate_object
from targetcompass_lite.validators import validate_dataset_card, validate_research_spec


VALID_SPEC = {
    "project_id": "demo",
    "goal": "target_prioritization",
    "research_theme": "vascular aging",
    "disease_scope": {"canonical": "vascular aging", "related_phenotypes": []},
    "organisms": ["human"],
    "priority_tissues": ["vascular endothelium"],
    "priority_cells": ["endothelial cell"],
    "target_routes": ["secreted"],
    "modalities_mvp": {"required": ["bulk_expression"], "optional": []},
    "constraints": {},
}


VALID_CARD = """dataset_id: ds_test
source: local
accession: TEST
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 3
  control_n: 3
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/expression_matrix.tsv
  metadata: data/metadata.tsv
"""


class SchemaValidationTest(unittest.TestCase):
    def test_core_schema_files_exist(self):
        self.assertTrue((SCHEMAS / "research_spec.schema.json").exists())
        self.assertTrue((SCHEMAS / "dataset_card.schema.json").exists())
        self.assertTrue((SCHEMAS / "evidence_item.schema.json").exists())
        self.assertTrue((SCHEMAS / "evidence_plan.schema.json").exists())
        self.assertTrue((SCHEMAS / "dataset_profile.schema.json").exists())
        self.assertTrue((SCHEMAS / "dataset_feasibility_report.schema.json").exists())
        self.assertTrue((SCHEMAS / "method_contract.schema.json").exists())
        self.assertTrue((SCHEMAS / "compatibility_decision.schema.json").exists())
        self.assertTrue((SCHEMAS / "analysis_plan.schema.json").exists())
        self.assertTrue((SCHEMAS / "codex_task_packet.schema.json").exists())
        self.assertTrue((SCHEMAS / "task_qc_report.schema.json").exists())
        self.assertTrue((SCHEMAS / "task_registry.schema.json").exists())

    def test_research_spec_schema_rejects_wrong_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "research_spec.json"
            spec = dict(VALID_SPEC)
            spec["organisms"] = "human"
            path.write_text(json.dumps(spec), encoding="utf-8")
            errors = validate_research_spec(path)
            self.assertIn("ResearchSpec.organisms: expected array", errors)

    def test_dataset_card_schema_rejects_bad_license_and_nested_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.yaml"
            path.write_text(
                VALID_CARD.replace("license_status: public", "license_status: private").replace("case_n: 3", "case_n: three"),
                encoding="utf-8",
            )
            errors = validate_dataset_card(path)
            self.assertIn("DatasetCard.license_status: must be one of public, authorized, unknown, restricted", errors)
            self.assertIn("DatasetCard.sample_summary.case_n: expected integer", errors)

    def test_evidence_item_schema_rejects_missing_core_fields(self):
        errors = _validate_evidence(
            {
                "evidence_id": "eid",
                "project_id": "demo",
                "entity_symbol": "",
                "evidence_type": "accessibility",
                "created_at": "2026-06-19T00:00:00Z",
            }
        )
        self.assertIn("EvidenceItem.entity_symbol: must not be empty", errors)

    def test_schema_validator_accepts_valid_spec_object(self):
        errors = validate_object(VALID_SPEC, load_schema("research_spec.schema.json"), "ResearchSpec")
        self.assertEqual(errors, [])

    def test_new_agent_evidence_objects_validate(self):
        evidence_plan = {
            "schema_version": "v0.1.evidence_plan",
            "project_id": "demo",
            "research_question": "aging muscle SASP surface markers",
            "evidence_axes": {"SASP_annotation": True},
            "preferred_data": ["scRNA-seq with metadata"],
            "minimum_evidence_for_candidate": ["expression signal"],
            "generated_by": "test",
        }
        profile = {
            "schema_version": "v0.1.dataset_profile",
            "project_id": "demo",
            "dataset_id": "GSE_TEST",
            "species": "human",
            "tissue": "skeletal muscle",
            "assay": "bulk_expression",
            "matrix_type": "raw_or_count_like",
            "sample_count": 6,
            "metadata_quality": "high",
            "download_status": "available",
            "analysis_readiness": "ready",
        }
        feasibility = {
            "schema_version": "v0.1.dataset_feasibility_report",
            "project_id": "demo",
            "dataset_id": "GSE_TEST",
            "decision": "pass",
            "matched_requirements": ["matrix available"],
            "unmet_requirements": [],
            "warnings": [],
            "recommended_uses": ["bulk_deg"],
        }
        method = {
            "schema_version": "v0.1.method_contract",
            "method_id": "bulk_deg_v1",
            "method_name": "bulk DEG",
            "data_modality": "bulk_expression",
            "purpose": "case-control expression testing",
            "requires": {"matrix_available": True},
            "reject_if": ["no metadata"],
            "outputs": ["deg_results.tsv"],
            "qc_checks": ["sample alignment"],
        }
        compatibility = {
            "schema_version": "v0.1.compatibility_decision",
            "project_id": "demo",
            "dataset_id": "GSE_TEST",
            "method_id": "bulk_deg_v1",
            "decision": "pass",
            "matched_requirements": ["matrix available"],
            "unmet_requirements": [],
            "warnings": [],
            "recommended_parameters": {"design": "~ group"},
        }
        analysis_plan = {
            "schema_version": "v0.2.evidence_driven_analysis_plan",
            "project_id": "demo",
            "route_strategy": "evidence_plan_plus_dataset_method_compatibility",
            "routes": [{"route_id": "route_GSE_TEST"}],
            "modules": [{"module_id": "ED_bulk_deg_GSE_TEST"}],
            "execution_order": ["ED_bulk_deg_GSE_TEST"],
        }
        task_packet = {
            "schema_version": "v0.2.codex_task_packet",
            "task_id": "ctp_demo",
            "goal": "Run bulk DEG",
            "dataset": {"dataset_id": "GSE_TEST"},
            "inputs": {"metadata": "metadata.tsv"},
            "method": {"name": "bulk DEG"},
            "expected_outputs": ["deg_results.tsv"],
            "acceptance_criteria": ["metadata matches matrix"],
            "failure_condition": "input missing",
            "forbidden_actions": ["do not invent metadata"],
        }
        task_qc = {
            "schema_version": "v0.1.task_qc_report",
            "project_id": "demo",
            "work_order_id": "wo_demo",
            "module_id": "ED_bulk_deg_demo",
            "overall_status": "pass",
            "layers": [
                {"layer": "Execution", "status": "pass"},
                {"layer": "Data", "status": "pass"},
                {"layer": "Statistical", "status": "pass"},
                {"layer": "Biological", "status": "pass"},
            ],
        }
        self.assertEqual(validate_object(evidence_plan, load_schema("evidence_plan.schema.json"), "EvidencePlan"), [])
        self.assertEqual(validate_object(profile, load_schema("dataset_profile.schema.json"), "DatasetProfile"), [])
        self.assertEqual(validate_object(feasibility, load_schema("dataset_feasibility_report.schema.json"), "DatasetFeasibilityReport"), [])
        self.assertEqual(validate_object(method, load_schema("method_contract.schema.json"), "MethodContract"), [])
        self.assertEqual(validate_object(compatibility, load_schema("compatibility_decision.schema.json"), "CompatibilityDecision"), [])
        self.assertEqual(validate_object(analysis_plan, load_schema("analysis_plan.schema.json"), "AnalysisPlan"), [])
        self.assertEqual(validate_object(task_packet, load_schema("codex_task_packet.schema.json"), "CodexTaskPacket"), [])
        self.assertEqual(validate_object(task_qc, load_schema("task_qc_report.schema.json"), "TaskQCReport"), [])


if __name__ == "__main__":
    unittest.main()
