import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.canonical.literature_pipeline import run_v5_literature_pipeline
from targetcompass_lite.evidence_db import query_evidence_items


class CanonicalLiteraturePipelineTest(unittest.TestCase):
    def test_v5_literature_pipeline_layers_abstract_and_fulltext_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            with patch("targetcompass_lite.literature_validation._get_json", return_value={"esearchresult": {"idlist": ["1"]}}), patch(
                "targetcompass_lite.literature_validation._get_text",
                return_value=_pubmed_xml(),
            ), patch("targetcompass_lite.fulltext_literature._pmids_from_literature", return_value=[]):
                run = run_v5_literature_pipeline(project, query="sarcopenia muscle SASP", limit=1, use_llm=False, fulltext_limit=1)

            self.assertEqual(run["schema_version"], "v5.literature_pipeline/0.1")
            self.assertEqual(run["abstract_layer"]["evidence_level"], "L0_abstract")
            self.assertEqual(run["fulltext_layer"]["evidence_level"], "L1_fulltext")
            self.assertTrue((project / "v5" / "literature" / "literature_pipeline_run.json").exists())
            query = query_evidence_items(project, evidence_type="literature_validation")
            self.assertGreaterEqual(query["match_count"], 1)

    def test_v5_literature_pipeline_registers_uploaded_fulltext_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            docs_dir = project / "results" / "fulltext_literature"
            docs_dir.mkdir(parents=True)
            text_path = Path(tmp) / "paper.txt"
            text_path.write_text("Methods skeletal muscle samples. Results IL6 increased in myocytes and secreted SASP cytokine.", encoding="utf-8")
            with patch("targetcompass_lite.literature_validation._get_json", return_value={"esearchresult": {"idlist": []}}), patch(
                "targetcompass_lite.fulltext_literature._pmids_from_literature",
                return_value=[],
            ):
                from targetcompass_lite.fulltext_literature import run_fulltext_literature

                run_fulltext_literature(project, text=[str(text_path)], limit=1)
                run = run_v5_literature_pipeline(project, query="sarcopenia", limit=1, use_llm=False, fulltext_limit=1)

            self.assertIn("default_evidence_policy", run)
            self.assertTrue(run["artifact_refs"])


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    project.mkdir()
    (project / "research_spec.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "research_theme": "sarcopenia muscle SASP",
                "disease_scope": {"canonical": "sarcopenia"},
                "priority_tissues": ["skeletal muscle"],
                "priority_cells": ["myocyte"],
            }
        ),
        encoding="utf-8",
    )
    return project


def _pubmed_xml() -> str:
    return """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation><PMID>1</PMID>
          <Article><Journal><Title>Demo Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
          <ArticleTitle>IL6 secretion in sarcopenia skeletal muscle senescence</ArticleTitle>
          <Abstract><AbstractText>IL6 is associated with SASP-like inflammation in sarcopenia skeletal muscle.</AbstractText></Abstract>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """


if __name__ == "__main__":
    unittest.main()
