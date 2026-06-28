import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.external_agent_import import (
    discover_external_agent_specs,
    import_external_agent_contracts,
    map_external_agent_to_v5_agent,
    validate_external_agent_contract_import,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_ROOT = REPO_ROOT / "external_agents" / "bioinfo-agent-system" / "bioinfo-agent-system"


class ExternalAgentImportTest(unittest.TestCase):
    def test_discovers_six_external_agents(self):
        specs = discover_external_agent_specs(EXTERNAL_ROOT)
        self.assertEqual(len(specs), 6)
        self.assertEqual(specs[0]["external_agent_id"], "01_scientific_question_normalizer")

    def test_reads_agent_docs_and_schemas(self):
        specs = discover_external_agent_specs(EXTERNAL_ROOT)
        for spec in specs:
            self.assertTrue(spec["agent_md_path"].endswith("agent.md"))
            self.assertTrue(spec["input_schema_path"].endswith("input.schema.json"))
            self.assertTrue(spec["output_schema_path"].endswith("output.schema.json"))
            self.assertIsInstance(spec["agent_md"], str)
            self.assertGreater(len(spec["agent_md"]), 20)
            self.assertIsInstance(spec["input_schema"], dict)
            self.assertIsInstance(spec["output_schema"], dict)

    def test_mapping_is_complete(self):
        expected = {
            "01_scientific_question_normalizer": "question_normalizer",
            "02_scope_ontology_resolver": "scope_resolver",
            "03_evidence_dataset_scout": "resource_discovery_agent",
            "04_method_extraction_agent": "evidence_plan_builder",
            "05_method_motif_feasibility_synthesizer": "method_adapter_workorder_compiler",
            "06_research_plan_compiler": "method_adapter_workorder_compiler",
        }
        for external_agent_id, v5_agent_id in expected.items():
            self.assertEqual(map_external_agent_to_v5_agent(external_agent_id), v5_agent_id)

    def test_import_marks_reference_only_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            result = import_external_agent_contracts(project_dir, EXTERNAL_ROOT)
            self.assertTrue((project_dir / "v5" / "imported_external_agents.json").exists())
            self.assertTrue(result["reference_only"])
            self.assertFalse(result["imported_as_evidence"])
            self.assertFalse(result["external_mock_runtime_called"])
            self.assertFalse(result["mock_outputs_imported"])
            self.assertFalse(result["canonical_specs_overwritten"])
            for agent in result["agents"]:
                self.assertTrue(agent["reference_only"])
                self.assertEqual(agent["import_status"], "imported_reference")
                self.assertFalse(agent["imported_as_evidence"])

    def test_mock_output_is_not_imported_as_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            result = import_external_agent_contracts(project_dir, EXTERNAL_ROOT)
            manifest_text = (project_dir / "v5" / "imported_external_agents.json").read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertFalse(manifest["mock_outputs_imported"])
            self.assertFalse(manifest["imported_as_evidence"])
            self.assertIn("outputs", " ".join(manifest["forbidden_paths"]))
            self.assertNotIn("agent_outputs", manifest_text)
            self.assertNotIn("AUTO_GEO", manifest_text)

    def test_validate_rejects_non_reference_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            result = import_external_agent_contracts(project_dir, EXTERNAL_ROOT)
            result["reference_only"] = False
            errors = validate_external_agent_contract_import(result)
            self.assertIn("import_result must be reference_only=true", errors)


if __name__ == "__main__":
    unittest.main()
