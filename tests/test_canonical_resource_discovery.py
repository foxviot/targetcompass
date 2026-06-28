import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.resource_discovery import (
    build_resource_query,
    discover_real_resources,
    relaxed_resource_queries,
    validate_resource_discovery_bundle,
)


def fake_fetch_json(url: str, timeout: int):
    if "esearch.fcgi" in url and "db=gds" in url:
        return {"esearchresult": {"idlist": ["1001"]}}
    if "esummary.fcgi" in url and "db=gds" in url:
        return {
            "result": {
                "uids": ["1001"],
                "1001": {
                    "uid": "1001",
                    "accession": "GSE12345",
                    "title": "Human sarcopenia muscle transcriptome",
                    "summary": "Expression profiling of skeletal muscle in sarcopenia.",
                    "organism": "Homo sapiens",
                    "platform": "GPL570",
                },
            }
        }
    if "esearch.fcgi" in url and "db=sra" in url:
        return {"esearchresult": {"idlist": ["2001"]}}
    if "esummary.fcgi" in url and "db=sra" in url:
        return {
            "result": {
                "uids": ["2001"],
                "2001": {
                    "uid": "2001",
                    "accession": "SRP12345",
                    "title": "Single-cell muscle aging study",
                    "summary": "snRNA-seq study of aging skeletal muscle.",
                    "organism": "Homo sapiens",
                    "platform": "Illumina",
                },
            }
        }
    if "europepmc" in url:
        return {
            "resultList": {
                "result": [
                    {
                        "id": "PMC123",
                        "title": "Senescence in sarcopenia muscle",
                        "abstractText": "A literature abstract.",
                        "journalTitle": "Journal",
                    }
                ]
            }
        }
    raise AssertionError(f"unexpected URL: {url}")


class CanonicalResourceDiscoveryTest(unittest.TestCase):
    def test_build_resource_query_from_scope_and_evidence_plan(self):
        query = build_resource_query(
            {"evidence_axes": ["SASP_annotation", "cell_type_specificity"]},
            {"conditions": ["sarcopenia"], "tissues": ["muscle"], "species": ["human"]},
        )
        self.assertIn("sarcopenia", query)
        self.assertIn("muscle", query)
        self.assertIn("human", query)

    def test_discover_real_resources_outputs_canonical_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            bundle = discover_real_resources(
                project_dir,
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["muscle"], "species": ["human"]},
                sources=("geo", "sra", "europe_pmc"),
                fetch_json=fake_fetch_json,
                write=True,
            )
            self.assertEqual(len(bundle["resource_candidates"]), 3)
            self.assertEqual(len(bundle["dataset_profiles"]), 2)
            self.assertEqual(len(bundle["dataset_selection_decisions"]), 2)
            self.assertTrue((project_dir / "v5" / "resource_discovery" / "resource_discovery_bundle.json").exists())
            self.assertFalse((project_dir / "results").exists())

    def test_real_metadata_can_be_verified_but_not_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["muscle"], "species": ["human"]},
                sources=("geo",),
                fetch_json=fake_fetch_json,
                write=False,
            )
            candidate = bundle["resource_candidates"][0]
            decision = bundle["dataset_selection_decisions"][0]
            self.assertTrue(candidate["verified"])
            self.assertEqual(candidate["source_status"], "metadata_verified")
            self.assertEqual(decision["decision"], "candidate_review_required")
            self.assertNotEqual(decision["decision"], "locked")

    def test_incomplete_metadata_is_not_verified(self):
        def incomplete_fetch(url: str, timeout: int):
            if "esearch.fcgi" in url:
                return {"esearchresult": {"idlist": ["1001"]}}
            return {"result": {"uids": ["1001"], "1001": {"uid": "1001", "accession": "GSE12345", "summary": "No title"}}}

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["expression"]},
                {"conditions": ["aging"], "tissues": ["muscle"], "species": ["human"]},
                sources=("geo",),
                fetch_json=incomplete_fetch,
                write=False,
            )
            self.assertFalse(bundle["resource_candidates"][0]["verified"])
            self.assertEqual(bundle["resource_candidates"][0]["source_status"], "metadata_incomplete")

    def test_locked_unverified_dataset_is_rejected(self):
        bundle = {
            "resource_candidates": [{"resource_candidate_id": "rc1", "verified": False, "source_status": "metadata_incomplete", "accession": "GSE1"}],
            "dataset_profiles": [],
            "dataset_selection_decisions": [{"decision_id": "d1", "resource_candidate_id": "rc1", "decision": "locked", "verified": False}],
        }
        errors = validate_resource_discovery_bundle(bundle)
        self.assertTrue(any("cannot be locked" in error for error in errors))

    def test_source_failure_is_recorded_without_fake_verified_candidate(self):
        def failing_fetch(url: str, timeout: int):
            raise RuntimeError("network down")

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["expression"]},
                {"conditions": ["aging"], "tissues": ["muscle"], "species": ["human"]},
                sources=("geo",),
                fetch_json=failing_fetch,
                write=False,
            )
            self.assertEqual(bundle["resource_candidates"], [])
            self.assertEqual(bundle["query_attempts"][0]["status"], "failed")

    def test_relaxed_query_can_recover_empty_source(self):
        calls = []

        def relaxed_fetch(url: str, timeout: int):
            calls.append(url)
            if "esearch.fcgi" in url:
                if "SASP" in url or "single" in url:
                    return {"esearchresult": {"idlist": []}}
                return {"esearchresult": {"idlist": ["3001"]}}
            return {
                "result": {
                    "uids": ["3001"],
                    "3001": {
                        "uid": "3001",
                        "accession": "SRP3001",
                        "title": "Human skeletal muscle RNA-seq",
                        "summary": "SRA metadata result recovered by relaxed query.",
                        "organism": "Homo sapiens",
                        "platform": "Illumina",
                    },
                }
            }

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["SASP_annotation", "cell_type_specificity"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("sra",),
                fetch_json=relaxed_fetch,
                write=False,
            )
            self.assertEqual(len(bundle["resource_candidates"]), 1)
            self.assertTrue(any(attempt["status"] == "relaxed_success" for attempt in bundle["query_attempts"]))

    def test_relaxed_resource_queries_remove_internal_axis_terms(self):
        queries = relaxed_resource_queries("sarcopenia skeletal muscle senescence SASP single cell type human")
        self.assertIn("sarcopenia skeletal muscle human", queries)

    def test_sra_run_xml_accession_is_cleaned(self):
        def sra_fetch(url: str, timeout: int):
            if "esearch.fcgi" in url:
                return {"esearchresult": {"idlist": ["4001"]}}
            return {
                "result": {
                    "uids": ["4001"],
                    "4001": {
                        "uid": "4001",
                        "runs": '<Run acc="SRR4001" total_spots="10"/>',
                        "summary": "SRA run metadata without explicit title.",
                    },
                }
            }

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["expression"]},
                {"conditions": ["aging"], "tissues": ["muscle"], "species": ["human"]},
                sources=("sra",),
                fetch_json=sra_fetch,
                write=False,
            )
            self.assertEqual(bundle["resource_candidates"][0]["accession"], "SRR4001")
            self.assertFalse(bundle["resource_candidates"][0]["verified"])

    def test_pubmed_and_europe_pmc_are_literature_not_datasets(self):
        def literature_fetch(url: str, timeout: int):
            if "esearch.fcgi" in url and "db=pubmed" in url:
                return {"esearchresult": {"idlist": ["5001"]}}
            if "esummary.fcgi" in url and "db=pubmed" in url:
                return {
                    "result": {
                        "uids": ["5001"],
                        "5001": {
                            "uid": "5001",
                            "title": "Review of sarcopenia and skeletal muscle senescence",
                            "source": "PubMed Journal",
                            "pubtype": ["Review"],
                        },
                    }
                }
            if "europepmc" in url:
                return {
                    "resultList": {
                        "result": [
                            {
                                "id": "PMC5001",
                                "title": "Cellular senescence in skeletal muscle",
                                "abstractText": "Single-cell RNA-seq and immunohistochemistry identify SASP expression in skeletal muscle biopsies.",
                                "pubType": "research-article",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("pubmed", "europe_pmc"),
                fetch_json=literature_fetch,
                write=False,
            )
            self.assertEqual(len(bundle["resource_candidates"]), 2)
            self.assertEqual({item["resource_type"] for item in bundle["resource_candidates"]}, {"literature"})
            self.assertTrue(all(item["verified"] for item in bundle["resource_candidates"]))
            self.assertEqual(bundle["dataset_profiles"], [])
            self.assertEqual(bundle["dataset_selection_decisions"], [])
            by_source = {item["source_database"]: item for item in bundle["resource_candidates"]}
            self.assertEqual(by_source["pubmed"]["paper_type"], "review")
            self.assertEqual(by_source["pubmed"]["fulltext_extraction_priority"], "low")
            self.assertEqual(by_source["pubmed"]["literature_screening_decision"], "not_default_validation_target")
            self.assertEqual(by_source["europe_pmc"]["paper_type"], "mechanism_experiment")
            self.assertEqual(by_source["europe_pmc"]["fulltext_extraction_priority"], "high")
            self.assertEqual(by_source["europe_pmc"]["literature_screening_decision"], "eligible_for_validation_extraction")
            self.assertEqual(by_source["europe_pmc"]["core_workflow_role"], "validation_only_not_required_for_target_discovery")
            self.assertEqual(bundle["resource_candidates"][0]["paper_type"], "mechanism_experiment")
            self.assertEqual(bundle["validation_extraction_candidate_count"], 1)
            self.assertEqual(bundle["filtered_literature_count"], 1)

    def test_literature_paper_type_classifier_marks_method_papers_for_review(self):
        def method_fetch(url: str, timeout: int):
            if "esearch.fcgi" in url and "db=pubmed" in url:
                return {"esearchresult": {"idlist": ["6001"]}}
            if "esummary.fcgi" in url and "db=pubmed" in url:
                return {
                    "result": {
                        "uids": ["6001"],
                        "6001": {
                            "uid": "6001",
                            "title": "Determining the feasibility of characterising cellular senescence in human skeletal muscle",
                            "source": "PubMed Journal",
                        },
                    }
                }
            raise AssertionError(f"unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("pubmed",),
                fetch_json=method_fetch,
                write=False,
            )
            candidate = bundle["resource_candidates"][0]
            self.assertEqual(candidate["paper_type"], "method")
            self.assertEqual(candidate["fulltext_extraction_priority"], "review")
            self.assertEqual(candidate["literature_screening_decision"], "not_default_validation_target")
            self.assertIn("feasibility", candidate["paper_type_reason"])

    def test_explicit_review_literature_request_allows_review_evidence_but_not_molecular_proof(self):
        def review_fetch(url: str, timeout: int):
            if "esearch.fcgi" in url and "db=pubmed" in url:
                return {"esearchresult": {"idlist": ["7001"]}}
            if "esummary.fcgi" in url and "db=pubmed" in url:
                return {
                    "result": {
                        "uids": ["7001"],
                        "7001": {
                            "uid": "7001",
                            "title": "Systematic review of sarcopenia mechanisms",
                            "source": "PubMed Journal",
                            "pubtype": ["Systematic Review"],
                        },
                    }
                }
            raise AssertionError(f"unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as tmp:
            bundle = discover_real_resources(
                Path(tmp) / "demo_project",
                {"evidence_axes": ["background_literature"], "include_review_literature": True},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("pubmed",),
                fetch_json=review_fetch,
                write=False,
            )
            candidate = bundle["resource_candidates"][0]
            self.assertEqual(candidate["paper_type"], "review")
            self.assertEqual(candidate["literature_screening_decision"], "eligible_for_review_evidence")
            self.assertEqual(candidate["validation_extraction_suitability"], "review_only")
            self.assertEqual(bundle["validation_extraction_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
