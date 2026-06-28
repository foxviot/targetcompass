import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.evidence_db import query_evidence_items
from targetcompass_lite.literature_validation import run_literature_validation


class LiteratureValidationTest(unittest.TestCase):
    def test_pubmed_literature_validation_writes_artifacts_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text(
                json.dumps(
                    {
                        "project_id": "demo",
                        "research_theme": "diabetes muscle SASP",
                        "disease_scope": {"canonical": "type 2 diabetes"},
                        "priority_tissues": ["skeletal muscle"],
                        "priority_cells": ["myocyte"],
                    }
                ),
                encoding="utf-8",
            )
            with patch("targetcompass_lite.literature_validation._get_json", return_value={"esearchresult": {"idlist": ["1", "2"]}}), patch(
                "targetcompass_lite.literature_validation._get_text",
                return_value=_pubmed_xml(),
            ), patch.dict(
                "os.environ",
                {
                    "OPENAI_API_KEY": "test-key",
                    "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
                    "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
                    "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
                },
                clear=True,
            ), patch("targetcompass_lite.literature_validation.urllib.request.urlopen", return_value=_FakeChatResponse()):
                run = run_literature_validation(project, query="diabetes muscle SASP", limit=2, batch_size=2, use_llm=True)

            self.assertEqual(run["schema_version"], "v4.literature_validation_run/0.1")
            self.assertEqual(run["article_count"], 2)
            self.assertEqual(run["effective_query"], "diabetes muscle SASP")
            self.assertEqual(run["query_attempts"][0]["strategy"], "original")
            self.assertEqual(run["inserted_evidence_rows"], 2)
            self.assertTrue((project / "results" / "literature_validation" / "pubmed_articles.tsv").exists())
            self.assertTrue((project / "results" / "literature_validation" / "literature_evidence.tsv").exists())
            query = query_evidence_items(project, evidence_type="literature_validation")
            self.assertEqual(query["match_count"], 2)
            self.assertEqual({row["entity_symbol"] for row in query["items"]}, {"IL6", "CXCL8"})

    def test_pubmed_literature_validation_relaxes_query_when_strict_query_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text(
                json.dumps({"project_id": "demo", "research_theme": "sarcopenia muscle SASP"}),
                encoding="utf-8",
            )

            def fake_search(query, limit, timeout):
                if query == "sarcopenia skeletal muscle SASP surface marker senescence":
                    return []
                return ["1"]

            with patch("targetcompass_lite.literature_validation._pubmed_search", side_effect=fake_search), patch(
                "targetcompass_lite.literature_validation._pubmed_fetch",
                return_value=[
                    {
                        "pmid": "1",
                        "title": "Sarcopenia skeletal muscle senescence",
                        "abstract": "SASP-like inflammation in skeletal muscle.",
                        "journal": "Demo",
                        "year": "2025",
                        "mesh_terms": [],
                        "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
                    }
                ],
            ):
                run = run_literature_validation(
                    project,
                    query="sarcopenia skeletal muscle SASP surface marker senescence",
                    limit=2,
                    batch_size=2,
                    use_llm=False,
                )

            self.assertEqual(run["article_count"], 1)
            self.assertGreaterEqual(len(run["query_attempts"]), 2)
            self.assertEqual(run["query_attempts"][0]["id_count"], 0)
            self.assertEqual(run["query_attempts"][-1]["strategy"], "relaxed_retry")
            self.assertNotEqual(run["effective_query"], "sarcopenia skeletal muscle SASP surface marker senescence")

    def test_pubmed_literature_validation_records_rate_limit_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text(
                json.dumps({"project_id": "demo", "research_theme": "sarcopenia muscle SASP"}),
                encoding="utf-8",
            )
            rate_limit = urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", hdrs=None, fp=None)
            with patch("targetcompass_lite.literature_validation._pubmed_search", side_effect=rate_limit):
                run = run_literature_validation(
                    project,
                    query="sarcopenia skeletal muscle SASP surface marker senescence",
                    limit=2,
                    batch_size=2,
                    use_llm=False,
                )

            self.assertEqual(run["article_count"], 0)
            self.assertTrue(run["query_attempts"])
            self.assertTrue(all(row["status"] == "failed" for row in run["query_attempts"]))
            self.assertEqual(run["query_attempts"][0]["error_type"], "HTTPError")
            self.assertIn("Retry later", run["query_attempts"][0]["recovery"])


def _pubmed_xml() -> str:
    return """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation><PMID>1</PMID>
          <Article><Journal><Title>Demo Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
          <ArticleTitle>IL6 secretion in diabetic skeletal muscle senescence</ArticleTitle>
          <Abstract><AbstractText>IL6 is associated with SASP-like inflammation in diabetic skeletal muscle.</AbstractText></Abstract>
          </Article>
          <MeshHeadingList><MeshHeading><DescriptorName>Diabetes Mellitus</DescriptorName></MeshHeading></MeshHeadingList>
        </MedlineCitation>
      </PubmedArticle>
      <PubmedArticle>
        <MedlineCitation><PMID>2</PMID>
          <Article><Journal><Title>Demo Journal</Title><JournalIssue><PubDate><Year>2023</Year></PubDate></JournalIssue></Journal>
          <ArticleTitle>CXCL8 and inflammatory myocyte signaling</ArticleTitle>
          <Abstract><AbstractText>CXCL8 is discussed as a chemokine in muscle inflammation.</AbstractText></Abstract>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """


class _FakeChatResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decisions": [
                                        {
                                            "pmid": "1",
                                            "relevance": "high",
                                            "candidate_symbols": ["IL6"],
                                            "evidence_type": "literature_validation",
                                            "confidence": 0.82,
                                            "rationale": "Diabetic skeletal muscle SASP signal.",
                                            "limitations": "Association-level literature evidence.",
                                        },
                                        {
                                            "pmid": "2",
                                            "relevance": "medium",
                                            "candidate_symbols": ["CXCL8"],
                                            "evidence_type": "literature_validation",
                                            "confidence": 0.64,
                                            "rationale": "Chemokine inflammation signal.",
                                            "limitations": "Not diabetes-specific in abstract.",
                                        },
                                    ]
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
