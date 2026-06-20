import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.geo_discovery import build_geo_query, discover_geo_datasets


SPEC = {
    "research_theme": "Find secreted targets for human endothelial senescence in vascular aging",
    "disease_scope": {"canonical": "vascular aging"},
    "organisms": ["human"],
    "priority_tissues": ["vascular endothelium"],
    "priority_cells": ["endothelial cell"],
}


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class GeoDiscoveryTest(unittest.TestCase):
    def test_build_geo_query_uses_research_spec(self):
        query = build_geo_query(SPEC)
        self.assertIn("vascular aging", query)
        self.assertIn("vascular endothelium", query)
        self.assertIn("human", query)
        self.assertIn("RNA-seq", query)

    def test_online_discovery_writes_ranked_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "research_spec.json").write_text(json.dumps(SPEC), encoding="utf-8")

            def fake_urlopen(request, timeout=10):
                url = request.full_url
                if "esearch.fcgi" in url:
                    return _Response({"esearchresult": {"idlist": ["1"]}})
                return _Response(
                    {
                        "result": {
                            "uids": ["1"],
                            "1": {
                                "uid": "1",
                                "title": "GSE123456 vascular aging endothelial cell RNA-seq",
                                "summary": "Expression profiling of 12 human endothelial cell samples in vascular aging.",
                                "taxon": "Homo sapiens",
                                "n_samples": 12,
                                "gpl": "RNA-seq",
                            },
                        }
                    }
                )

            with patch("targetcompass_lite.geo_discovery.urllib.request.urlopen", fake_urlopen):
                payload = discover_geo_datasets(project, limit=5)

            self.assertEqual(payload["mode"], "online")
            self.assertEqual(payload["recommendations"][0]["accession"], "GSE123456")
            self.assertGreaterEqual(payload["recommendations"][0]["score"], 80)
            self.assertTrue((project / "results" / "geo_discovery" / "geo_recommendations.json").exists())
            self.assertTrue((project / "results" / "geo_discovery" / "geo_recommendations.tsv").exists())

    def test_offline_discovery_uses_registered_geo_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "research_spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
            cards = project / "dataset_cards"
            cards.mkdir()
            (cards / "GSELOCAL.yaml").write_text(
                "\n".join(
                    [
                        "dataset_id: GSELOCAL",
                        "source: GEO",
                        "accession: GSE999",
                        "modality: bulk_expression",
                        "organism: human",
                        "tissue: vascular endothelium",
                        "sample_summary:",
                        "  donor_n: 10",
                        "known_limitations: [local card]",
                    ]
                ),
                encoding="utf-8",
            )

            payload = discover_geo_datasets(project, limit=3, online=False)

            self.assertEqual(payload["mode"], "local_fallback")
            self.assertEqual(payload["recommendations"][0]["accession"], "GSE999")
            self.assertEqual(payload["recommendations"][0]["import_status"], "registered")


if __name__ == "__main__":
    unittest.main()
