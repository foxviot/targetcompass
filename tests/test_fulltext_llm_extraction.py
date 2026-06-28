import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.evidence_db import query_evidence_items
from targetcompass_lite.fulltext_llm_extraction import run_fulltext_llm_extraction


class FulltextLlmExtractionTest(unittest.TestCase):
    def test_llm_extracts_methods_samples_cell_types_and_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            docs_dir = project / "results" / "fulltext_literature"
            docs_dir.mkdir(parents=True)
            (docs_dir / "fulltext_documents.json").write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "source_type": "uploaded_pdf_ocr",
                                "pmid": "1",
                                "pmcid": "PMC1",
                                "title": "Demo full text",
                                "artifact_path": "results/fulltext_literature/demo.pdf",
                                "text": "Methods: skeletal muscle myocytes were analyzed by qPCR and ELISA. Results: IL6 increased in myocytes.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "OPENAI_API_KEY": "test-key",
                "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
                "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
                "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
            }
            with patch.dict("os.environ", env, clear=True), patch("targetcompass_lite.fulltext_llm_extraction.urllib.request.urlopen", return_value=_FakeResponse()):
                run = run_fulltext_llm_extraction(project, max_docs=1)

            self.assertEqual(run["schema_version"], "v4.fulltext_llm_extraction/0.1")
            self.assertEqual(run["evidence_row_count"], 1)
            extraction = json.loads((project / "results" / "fulltext_literature" / "llm_extraction" / "fulltext_llm_extractions.json").read_text(encoding="utf-8"))
            item = extraction["extractions"][0]
            self.assertEqual(item["methods"][0]["method"], "qPCR")
            self.assertEqual(item["samples"][0]["cell_type"], "myocyte")
            self.assertEqual(item["cell_types"][0]["cell_type"], "myocyte")
            query = query_evidence_items(project, evidence_type="fulltext_extracted_result")
            self.assertEqual(query["match_count"], 1)
            self.assertEqual(query["items"][0]["entity_symbol"], "IL6")
            self.assertEqual(query["items"][0]["evidence_level"], "L5_experimental")

    def test_llm_extraction_normalizes_common_senescence_marker_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            docs_dir = project / "results" / "fulltext_literature"
            docs_dir.mkdir(parents=True)
            (docs_dir / "fulltext_documents.json").write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "source_type": "pmc_open_access",
                                "pmid": "37434081",
                                "pmcid": "PMC10828484",
                                "title": "Demo full text",
                                "artifact_path": "results/fulltext_literature/PMC10828484.xml",
                                "text": "Results: P16 and Lamin B1 were measured by immunohistochemistry.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "OPENAI_API_KEY": "test-key",
                "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
                "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
                "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
            }
            with patch.dict("os.environ", env, clear=True), patch("targetcompass_lite.fulltext_llm_extraction.urllib.request.urlopen", return_value=_FakeAliasResponse()):
                run = run_fulltext_llm_extraction(project, max_docs=1)

            self.assertEqual(run["evidence_row_count"], 2)
            query = query_evidence_items(project, evidence_type="fulltext_extracted_result", limit=10)
            symbols = {row["entity_symbol"] for row in query["items"]}
            self.assertEqual(symbols, {"CDKN2A", "LMNB1"})
            limitations = " ".join(row["limitation"] for row in query["items"])
            self.assertIn("raw_symbol=P16", limitations)
            self.assertIn("raw_symbol=Lamin B1", limitations)


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    project.mkdir()
    (project / "research_spec.json").write_text(
        json.dumps({"project_id": "demo", "research_theme": "diabetes muscle SASP", "disease_scope": {"canonical": "type 2 diabetes"}, "priority_tissues": ["skeletal muscle"]}),
        encoding="utf-8",
    )
    return project


class _FakeResponse:
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
                                    "document_id": "PMC1",
                                    "methods": [{"method": "qPCR", "evidence_sentence": "skeletal muscle myocytes were analyzed by qPCR and ELISA"}],
                                    "samples": [{"organism": "human", "tissue": "skeletal muscle", "cell_type": "myocyte", "sample_size": "", "condition": "type 2 diabetes", "evidence_sentence": "skeletal muscle myocytes"}],
                                    "cell_types": [{"cell_type": "myocyte", "marker_or_context": "skeletal muscle", "evidence_sentence": "myocytes were analyzed"}],
                                    "results": [{"gene_symbol": "IL6", "molecule": "IL6", "direction": "up", "cell_type": "myocyte", "tissue": "skeletal muscle", "assay": "qPCR; ELISA", "evidence_sentence": "IL6 increased in myocytes.", "confidence": 0.86}],
                                    "limitations": ["demo extraction"],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


class _FakeAliasResponse:
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
                                    "document_id": "PMC10828484",
                                    "methods": [{"method": "immunohistochemistry", "evidence_sentence": "P16 and Lamin B1 were measured by immunohistochemistry"}],
                                    "samples": [{"organism": "human", "tissue": "skeletal muscle", "cell_type": "", "sample_size": "", "condition": "aging", "evidence_sentence": "human skeletal muscle"}],
                                    "cell_types": [],
                                    "results": [
                                        {"gene_symbol": "P16", "molecule": "P16", "direction": "associated", "cell_type": "", "tissue": "skeletal muscle", "assay": "immunohistochemistry", "evidence_sentence": "P16 was measured.", "confidence": 0.7},
                                        {"gene_symbol": "Lamin B1", "molecule": "Lamin B1", "direction": "associated", "cell_type": "", "tissue": "skeletal muscle", "assay": "immunohistochemistry", "evidence_sentence": "Lamin B1 was measured.", "confidence": 0.7},
                                        {"gene_symbol": "TAF", "molecule": "TAF", "direction": "associated", "cell_type": "", "tissue": "skeletal muscle", "assay": "immunohistochemistry", "evidence_sentence": "TAF was measured.", "confidence": 0.7},
                                    ],
                                    "limitations": ["demo extraction"],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
