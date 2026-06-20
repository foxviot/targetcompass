import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.experiment_design import design_experiments
from targetcompass_lite.ideas import generate_idea_batch, load_ideas
from targetcompass_lite.knowledge import add_resource, adapt_resources, load_registry, remove_resource


SPEC = {
    "project_id": "demo",
    "goal": "vaccine_candidate_target_prioritization",
    "research_theme": "vascular aging",
    "disease_scope": {"canonical": "vascular aging", "related_phenotypes": []},
    "organisms": ["human"],
    "priority_tissues": ["vascular endothelium"],
    "priority_cells": ["endothelial cell"],
    "target_routes": ["secreted", "surface"],
    "modalities_mvp": {"required": ["bulk_expression"], "optional": []},
    "constraints": {"claim_policy": "association_only_without_genetic_or_experimental_validation"},
}


CARD = """dataset_id: ds_one
source: local
accession: ONE001
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
  expression_matrix: data/ds_one/expression_matrix.tsv
  metadata: data/ds_one/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "configs").mkdir(parents=True)
    (project / "research_spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (project / "dataset_cards" / "ds_one.yaml").write_text(CARD, encoding="utf-8")
    return project


class IdeasKnowledgeTest(unittest.TestCase):
    def test_generate_idea_batch_respects_count_and_designs_experiments(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            ideas = generate_idea_batch(project, "vascular aging secreted targets", 4)
            self.assertEqual(len(ideas), 4)
            self.assertTrue((project / "results" / "ideas" / "idea_batch.csv").exists())
            self.assertEqual(len(load_ideas(project)), 4)
            designs = design_experiments(project)
            self.assertGreaterEqual(len(designs), 1)
            self.assertTrue((project / "results" / "experiments" / "experiment_designs.md").exists())

    def test_knowledge_registry_can_add_remove_and_adapt_dataset_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            source = Path(tmp) / "external_card.yaml"
            source.write_text(CARD.replace("ds_one", "ds_two"), encoding="utf-8")
            add_resource(project, "ds_two_card", "dataset_card", str(source))
            self.assertEqual(load_registry(project)[0]["resource_id"], "ds_two_card")
            adapted = adapt_resources(project)
            self.assertEqual(adapted[0]["status"], "adapted")
            self.assertTrue((project / "dataset_cards" / "external_card.yaml").exists())
            self.assertTrue(remove_resource(project, "ds_two_card"))
            self.assertEqual(load_registry(project), [])


if __name__ == "__main__":
    unittest.main()
