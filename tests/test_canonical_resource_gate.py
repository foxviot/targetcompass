import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.resource_discovery import discover_real_resources
from targetcompass_lite.canonical.resource_gate import (
    apply_resource_manual_correction,
    apply_suggested_resource_corrections,
    build_resource_gate_report,
)


class CanonicalResourceGateTest(unittest.TestCase):
    def test_resource_gate_requires_manual_grouping_before_dataset_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            bundle = discover_real_resources(
                project,
                {"evidence_axes": ["SASP_annotation", "cell_type_specificity"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("geo", "sra"),
                fetch_json=_fake_dataset_fetch,
                write=True,
            )

            report = build_resource_gate_report(project, bundle)

            self.assertEqual(report["candidate_count"], 2)
            self.assertEqual(report["verified_metadata_count"], 2)
            self.assertEqual(report["manual_review_count"], 2)
            self.assertTrue(all(item["can_enter_datasets_locked"] is False for item in report["gate_items"]))
            self.assertTrue(any("group_metadata_not_assessed" in item["blocking_issues"] for item in report["gate_items"]))
            self.assertTrue(any("group_column" in item["missing_required_fields"] for item in report["gate_items"]))
            self.assertIn("Fill required metadata fields", report["gate_items"][0]["next_human_action"])
            self.assertTrue((project / "v5" / "resource_discovery" / "resource_gate_report.json").exists())

    def test_manual_correction_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            correction = apply_resource_manual_correction(
                project,
                "rc1",
                group_metadata_status="case_control_selected",
                sample_size_status="sufficient",
                tissue="skeletal muscle",
                notes="curator selected metadata columns",
            )

            self.assertEqual(correction["resource_candidate_id"], "rc1")
            path = project / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl"
            self.assertTrue(path.exists())
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_manual_correction_makes_dataset_lockable_after_regate(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            bundle = discover_real_resources(
                project,
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("geo",),
                fetch_json=_fake_dataset_fetch,
                write=True,
            )
            candidate_id = bundle["resource_candidates"][0]["resource_candidate_id"]
            _write_ready_matrix(project, "GSE12345")

            apply_resource_manual_correction(
                project,
                candidate_id,
                group_metadata_status="case_control_selected",
                sample_size_status="sufficient",
                organism="Homo sapiens",
                tissue="skeletal muscle",
                platform="GPL570",
                group_column="diagnosis",
                case_label="sarcopenia",
                control_label="control",
                sample_count="24",
            )
            report = build_resource_gate_report(project)
            item = report["gate_items"][0]

            self.assertEqual(report["datasets_lockable_count"], 1)
            self.assertEqual(item["gate_status"], "datasets_locked_ready")
            self.assertTrue(item["can_enter_datasets_locked"])
            self.assertEqual(item["manual_correction"]["group_column"], "diagnosis")
            self.assertEqual(item["missing_required_fields"], [])
            self.assertIn("ready for DATASETS_LOCKED", item["next_human_action"])
            self.assertTrue(item["metadata_value_preview"])
            self.assertEqual(item["metadata_value_preview"][0]["name"], "sample_id")

    def test_inferred_metadata_prefills_manual_correction_without_auto_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"

            bundle = discover_real_resources(
                project,
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("geo",),
                fetch_json=_fake_inferable_dataset_fetch,
                write=True,
            )
            report = build_resource_gate_report(project)
            item = report["gate_items"][0]

            self.assertEqual(report["datasets_lockable_count"], 0)
            self.assertEqual(item["gate_status"], "analysis_ready_after_review")
            self.assertFalse(item["can_enter_datasets_locked"])
            self.assertEqual(item["suggested_manual_correction"]["tissue"], "skeletal muscle")
            self.assertEqual(item["suggested_manual_correction"]["sample_count"], "24")
            self.assertEqual(item["suggested_manual_correction"]["group_column"], "condition")

            candidate_id = bundle["resource_candidates"][0]["resource_candidate_id"]
            _write_ready_matrix(project, "GSE3001")
            apply_resource_manual_correction(
                project,
                candidate_id,
                group_metadata_status="case_control_selected",
                sample_size_status="sufficient",
                organism="Homo sapiens",
                tissue=item["suggested_manual_correction"]["tissue"],
                platform=item["suggested_manual_correction"]["platform"],
                group_column=item["suggested_manual_correction"]["group_column"],
                case_label=item["suggested_manual_correction"]["case_label"],
                control_label=item["suggested_manual_correction"]["control_label"],
                sample_count=item["suggested_manual_correction"]["sample_count"],
                notes="human accepted inferred public metadata",
            )
            locked = build_resource_gate_report(project)

            self.assertEqual(locked["datasets_lockable_count"], 1)
            self.assertEqual(locked["gate_items"][0]["missing_required_fields"], [])

    def test_batch_accept_suggested_correction_locks_complete_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            discover_real_resources(
                project,
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["sarcopenia"], "tissues": ["skeletal muscle"], "species": ["human"]},
                sources=("geo",),
                fetch_json=_fake_inferable_dataset_fetch,
                write=True,
            )
            _write_ready_matrix(project, "GSE3001")

            batch = apply_suggested_resource_corrections(project)
            report = build_resource_gate_report(project)

            self.assertEqual(batch["accepted_count"], 1)
            self.assertEqual(batch["datasets_lockable_count"], 1)
            self.assertEqual(report["datasets_lockable_count"], 1)
            self.assertEqual(report["gate_items"][0]["manual_correction"]["actor"], "human_batch_accept_suggested")

    def test_scope_suggestion_can_complete_tissue_after_human_batch_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            discover_real_resources(
                project,
                {"evidence_axes": ["SASP_annotation"]},
                {"conditions": ["aging"], "tissues": ["endothelium"], "species": ["human"]},
                sources=("geo",),
                fetch_json=_fake_missing_tissue_dataset_fetch,
                write=True,
            )
            before = build_resource_gate_report(project)

            self.assertEqual(before["datasets_lockable_count"], 0)
            self.assertEqual(before["gate_items"][0]["suggested_manual_correction"]["tissue"], "endothelium")
            _write_ready_matrix(project, "GSE4001")

            batch = apply_suggested_resource_corrections(project)

            self.assertEqual(batch["accepted_count"], 1)
            self.assertEqual(batch["datasets_lockable_count"], 1)

    def test_sra_requires_quantification_manifest_for_matrix_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "data" / "SRP12345").mkdir(parents=True)
            (project / "data" / "SRP12345" / "expression_matrix.tsv").write_text("gene_symbol\tS1\nIL6\t1\n", encoding="utf-8")
            (project / "data" / "SRP12345" / "metadata.tsv").write_text("sample_id\tgroup\nS1\tcase\n", encoding="utf-8")
            (project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").write_text(
                json.dumps(
                    {
                        "resource_candidates": [
                            {
                                "resource_candidate_id": "rc_sra",
                                "resource_type": "dataset",
                                "source_database": "sra",
                                "accession": "SRP12345",
                                "verified": True,
                                "source_status": "metadata_verified",
                            }
                        ],
                        "dataset_profiles": [
                            {
                                "resource_candidate_id": "rc_sra",
                                "dataset_profile_id": "dp_sra",
                                "modality": "bulk_expression",
                                "group_metadata_status": "case_control_selected",
                                "sample_size_status": "sufficient",
                                "organism": "Homo sapiens",
                                "tissue": "skeletal muscle",
                                "platform": "Illumina",
                            }
                        ],
                        "dataset_selection_decisions": [],
                    }
                ),
                encoding="utf-8",
            )
            apply_resource_manual_correction(
                project,
                "rc_sra",
                group_metadata_status="case_control_selected",
                sample_size_status="sufficient",
                organism="Homo sapiens",
                tissue="skeletal muscle",
                platform="Illumina",
                group_column="group",
                case_label="case",
                control_label="control",
                sample_count="1",
                notes="human says matrix_parse_ready=true but quantification manifest is missing",
            )

            report = build_resource_gate_report(project)

            self.assertEqual(report["datasets_lockable_count"], 0)
            self.assertFalse(report["gate_items"][0]["matrix_parse_ready"])
            self.assertIn("matrix_parse_not_ready", report["gate_items"][0]["blocking_issues"])


def _fake_dataset_fetch(url: str, timeout: int):
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
    raise AssertionError(url)


def _write_ready_matrix(project: Path, accession: str) -> None:
    data_dir = project / "data" / accession
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "expression_matrix.tsv").write_text("gene_symbol\tS1\tS2\nIL6\t1\t2\n", encoding="utf-8")
    (data_dir / "metadata.tsv").write_text("sample_id\tgroup\nS1\tcase\nS2\tcontrol\n", encoding="utf-8")


def _fake_inferable_dataset_fetch(url: str, timeout: int):
    if "esearch.fcgi" in url and "db=gds" in url:
        return {"esearchresult": {"idlist": ["3001"]}}
    if "esummary.fcgi" in url and "db=gds" in url:
        return {
            "result": {
                "uids": ["3001"],
                "3001": {
                    "uid": "3001",
                    "accession": "GSE3001",
                    "title": "Human skeletal muscle sarcopenia versus healthy control transcriptome",
                    "summary": "Expression profiling of 24 samples from sarcopenia patients and healthy controls.",
                    "organism": "Homo sapiens",
                    "platform": "GPL570",
                },
            }
        }
    raise AssertionError(url)


def _fake_missing_tissue_dataset_fetch(url: str, timeout: int):
    if "esearch.fcgi" in url and "db=gds" in url:
        return {"esearchresult": {"idlist": ["4001"]}}
    if "esummary.fcgi" in url and "db=gds" in url:
        return {
            "result": {
                "uids": ["4001"],
                "4001": {
                    "uid": "4001",
                    "accession": "GSE4001",
                    "title": "Human aging versus healthy control transcriptome",
                    "summary": "Expression profiling of 14 samples from aged cases and healthy controls.",
                    "organism": "Homo sapiens",
                    "platform": "GPL33022",
                },
            }
        }
    raise AssertionError(url)


if __name__ == "__main__":
    unittest.main()
