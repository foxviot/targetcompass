import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.failure_recovery import build_v5_failure_recovery_report


class CanonicalFailureRecoveryTest(unittest.TestCase):
    def test_dataset_not_found_and_literature_only_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write(
                project / "v5" / "resource_discovery" / "resource_discovery_bundle.json",
                {
                    "query_attempts": [{"source": "geo"}, {"source": "sra"}],
                    "resource_candidates": [
                        {"resource_candidate_id": "lit1", "resource_type": "literature", "verified": True, "accession": "1"}
                    ],
                    "dataset_profiles": [],
                    "dataset_selection_decisions": [],
                },
            )
            report = build_v5_failure_recovery_report(project, write=True)
            ids = {item["item_id"] for item in report["items"]}
            self.assertIn("dataset_not_found", ids)
            self.assertIn("literature_without_omics", ids)
            self.assertTrue((project / "v5" / "recovery" / "failure_recovery_report.json").exists())

    def test_metadata_insufficient_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write(
                project / "v5" / "resource_discovery" / "resource_discovery_bundle.json",
                {
                    "query_attempts": [{"source": "geo"}],
                    "resource_candidates": [
                        {
                            "resource_candidate_id": "rc1",
                            "resource_type": "dataset",
                            "verified": True,
                            "source_status": "metadata_verified",
                            "accession": "GSE1",
                        }
                    ],
                    "dataset_profiles": [
                        {
                            "resource_candidate_id": "rc1",
                            "group_metadata_status": "not_assessed",
                            "sample_size_status": "not_assessed",
                            "organism": "unknown",
                            "tissue": "unknown",
                        }
                    ],
                    "dataset_selection_decisions": [],
                },
            )
            report = build_v5_failure_recovery_report(project, write=False)
            self.assertTrue(any(item["item_id"].startswith("metadata_insufficient:GSE1") for item in report["items"]))

    def test_scrna_bulk_mismatch_failed_task_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write(
                project / "v5" / "task_runs" / "run1.json",
                {
                    "task_run_id": "run1",
                    "task_id": "task1",
                    "module": "scrna_pseudobulk",
                    "result_status": "failed",
                    "failure_reason": "metadata sample IDs do not match expression matrix columns",
                },
            )
            report = build_v5_failure_recovery_report(project, write=False)
            self.assertTrue(any(item["item_id"].startswith("analysis_input_mismatch:run1") for item in report["items"]))

    def test_claim_ceiling_violation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write(
                project / "v5" / "reports" / "question_alignment_report.json",
                {
                    "claim_ceiling_violations": [
                        {"claim_id": "c1", "claim_level": "causal_support", "max_allowed_claim": "association"}
                    ]
                },
            )
            _write(project / "v5" / "reports" / "canonical_report_manifest.json", {"human_review_gate": {"required": True}})
            report = build_v5_failure_recovery_report(project, write=False)
            self.assertIn("claim_ceiling_violation", {item["item_id"] for item in report["items"]})

    def test_clear_when_no_recovery_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write(
                project / "v5" / "resource_discovery" / "resource_discovery_bundle.json",
                {
                    "query_attempts": [{"source": "geo"}],
                    "resource_candidates": [
                        {"resource_candidate_id": "rc1", "resource_type": "dataset", "verified": True, "source_status": "metadata_verified", "accession": "GSE1"}
                    ],
                    "dataset_profiles": [
                        {
                            "resource_candidate_id": "rc1",
                            "group_metadata_status": "ready",
                            "sample_size_status": "ready",
                            "organism": "Homo sapiens",
                            "tissue": "skeletal muscle",
                        }
                    ],
                    "dataset_selection_decisions": [],
                },
            )
            report = build_v5_failure_recovery_report(project, write=False)
            self.assertEqual(report["status"], "clear")


def _write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
