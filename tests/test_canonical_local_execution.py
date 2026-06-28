import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import load_artifact_registry
from targetcompass_lite.canonical.local_demo_runner import run_v5_local_demo
from targetcompass_lite.canonical.local_execution import (
    compile_registered_analysis_task_packets,
    execute_registered_analysis_task_packets,
)
from targetcompass_lite.canonical.nextflow_execution import load_qc_reports, load_task_runs


BULK_CARD = """dataset_id: ds_bulk
source: local
accession: BULK001
modality: bulk_expression
organism: human
tissue: skeletal muscle
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 3
  control_n: 3
  donor_n: 6
metadata_fields: [sample_id, group, donor_id]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_bulk/expression_matrix.tsv
  metadata: data/ds_bulk/metadata.tsv
known_limitations: [small fixture]
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(root: Path) -> Path:
    project = root / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_bulk").mkdir(parents=True)
    (project / "research_spec.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "goal": "target_prioritization",
                "research_theme": "sarcopenia skeletal muscle SASP surface marker discovery",
                "disease_scope": {"canonical": "sarcopenia"},
                "organisms": ["human"],
                "priority_tissues": ["skeletal muscle"],
                "priority_cells": ["stromal cell"],
                "target_routes": ["surface", "secreted"],
                "modalities_mvp": {"required": ["bulk_expression"], "optional": ["enrichment"]},
                "constraints": {"causal_requirement": "preferred_not_mandatory"},
            }
        ),
        encoding="utf-8",
    )
    (project / "dataset_cards" / "ds_bulk.yaml").write_text(BULK_CARD, encoding="utf-8")
    (project / "data" / "ds_bulk" / "expression_matrix.tsv").write_text(
        "gene_symbol\tS1\tS2\tS3\tS4\tS5\tS6\n"
        "IL6\t10\t11\t12\t2\t3\t2\n"
        "CXCL8\t8\t9\t8\t1\t1\t2\n"
        "CDKN1A\t7\t7\t8\t2\t2\t3\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_bulk" / "metadata.tsv").write_text(
        "sample_id\tgroup\tdonor_id\n"
        "S1\taged\tD1\nS2\taged\tD2\nS3\taged\tD3\n"
        "S4\tyoung\tD4\nS5\tyoung\tD5\nS6\tyoung\tD6\n",
        encoding="utf-8",
    )
    return project


def fake_fetch_json(url: str, timeout: int):
    if "db=gds" in url and "esearch.fcgi" in url:
        return {"esearchresult": {"idlist": ["1001"]}}
    if "db=gds" in url and "esummary.fcgi" in url:
        return {
            "result": {
                "uids": ["1001"],
                "1001": {
                    "uid": "1001",
                    "accession": "GSE_LOCAL",
                    "title": "Human sarcopenia skeletal muscle expression",
                    "summary": "Muscle transcriptome metadata.",
                    "organism": "Homo sapiens",
                    "platform": "GPL570",
                },
            }
        }
    if "db=pubmed" in url and "esearch.fcgi" in url:
        return {"esearchresult": {"idlist": ["3001"]}}
    if "db=pubmed" in url and "esummary.fcgi" in url:
        return {"result": {"uids": ["3001"], "3001": {"uid": "3001", "title": "SASP in muscle", "source": "Journal"}}}
    return {"resultList": {"result": [{"id": "PMC_LOCAL", "title": "Sarcopenia SASP review", "abstractText": "Abstract"}]}}


class CanonicalLocalExecutionTest(unittest.TestCase):
    def test_analysis_task_packet_dispatches_to_local_bulk_runner_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(Path(tmp))
            compile_result = compile_registered_analysis_task_packets(project, subquestion_id="sq_demo")
            bulk_packets = [packet for packet in compile_result["packets"] if packet.get("module") == "bulk_deg"]
            self.assertTrue(bulk_packets)

            result = execute_registered_analysis_task_packets(project, bulk_packets, max_packets=1)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completed_count"], 1)
            self.assertTrue((project / "results" / "bulk_deg_ds_bulk" / "deg_results.tsv").exists())
            self.assertTrue((project / "reports" / "target_report.html").exists())
            self.assertGreaterEqual(len(load_task_runs(project)), 1)
            self.assertGreaterEqual(len(load_qc_reports(project)), 1)
            self.assertTrue(any(row["path"].endswith("deg_results.tsv") for row in load_artifact_registry(project)))

    def test_v5_run_local_uses_registered_modules_when_project_inputs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(Path(tmp))
            result = run_v5_local_demo(
                project,
                "Are there high-SASP surface molecules in sarcopenia skeletal muscle cells?",
                sources=("geo", "pubmed"),
                fetch_json=fake_fetch_json,
                max_analysis_packets=1,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["claim_scope"], "registered_local_analysis")
            self.assertGreaterEqual(result["analysis_task_count"], 1)
            state = json.loads((project / "v5" / "project_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current_stage"], "REPORT_READY")
            self.assertTrue((project / "v5" / "local_execution" / "local_execution_bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
