import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.database_validation import validate_online_databases
from targetcompass_lite.webapp import _knowledge_panel


class DatabaseValidationTest(unittest.TestCase):
    def test_online_database_validation_downloads_registers_and_adapts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "configs").mkdir()
            (project / "research_spec.json").write_text(
                json.dumps({"project_id": "demo", "disease_scope": {"canonical": "type 2 diabetes"}}),
                encoding="utf-8",
            )
            with patch("targetcompass_lite.database_validation._get_text", side_effect=_fake_get_text), patch(
                "targetcompass_lite.database_validation._get_json", side_effect=_fake_get_json
            ), patch("targetcompass_lite.database_validation._post_json", side_effect=_fake_post_json):
                result = validate_online_databases(project, genes=["IL6", "CXCL8"], query="type 2 diabetes", limit=2, adapt=True)

            self.assertEqual(result["schema_version"], "v4.online_database_validation/0.1")
            self.assertGreaterEqual(result["success_count"], 4)
            self.assertIn("online_uniprot", result["registered_resources"])
            self.assertTrue((project / "results" / "database_validation" / "online_database_validation.json").exists())
            self.assertTrue((project / "knowledge_imports" / "normalized" / "online_uniprot_evidence.tsv").exists())
            html = _knowledge_panel(project)
            for text in ["Online database validation", "uniprot", "reactome", "requires_credentials"]:
                self.assertIn(text, html)


def _fake_get_text(url: str, timeout: int) -> str:
    if "uniprot" in url:
        return (
            "Entry\tGene Names (primary)\tProtein names\tReviewed\tSubcellular location [CC]\n"
            "P05231\tIL6\tInterleukin-6\treviewed\tSecreted\n"
            "P10145\tCXCL8\tInterleukin-8\treviewed\tSecreted\n"
        )
    raise AssertionError(url)


def _fake_get_json(url: str, timeout: int):
    if "proteinatlas" in url:
        return [{"Gene": "IL6", "Subcellular location": "Secreted", "RNA tissue specificity": "Low specificity", "Brain": "Low", "Heart muscle": "Low"}]
    if "reactome" in url:
        return [{"pathways": [{"stId": "R-HSA-449147", "displayName": "Signaling by interleukins"}]}]
    if "ebi.ac.uk/gwas" in url:
        return {"_embedded": {"studies": [{"reportedTrait": "IL6", "pvalue": 1e-9, "diseaseTrait": {"trait": "type 2 diabetes"}}]}}
    raise AssertionError(url)


def _fake_post_json(url: str, payload: dict, timeout: int):
    if "opentargets" in url:
        return {"data": {"search": {"hits": [{"id": "ENSG00000136244", "name": "IL6", "entity": "target"}]}}}
    raise AssertionError(url)


if __name__ == "__main__":
    unittest.main()
