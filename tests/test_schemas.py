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


if __name__ == "__main__":
    unittest.main()
