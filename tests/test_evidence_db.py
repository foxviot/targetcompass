import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_db import SCHEMA_VERSION, import_evidence


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    deg_dir = project / "results" / "bulk_deg_ds_test"
    annotation_dir = project / "results" / "annotation"
    deg_dir.mkdir(parents=True)
    annotation_dir.mkdir(parents=True)
    (project / "research_spec.json").write_text(
        json.dumps({"disease_scope": {"canonical": "vascular aging"}}),
        encoding="utf-8",
    )
    (deg_dir / "deg_results.tsv").write_text(
        "\n".join(
            [
                "gene_symbol\tcase_mean\tcontrol_mean\tlogFC\tp_value\tadj_p_value\tdirection",
                "IL6\t10\t2\t2.3\t0.001\t0.01\tup",
                "\t5\t3\tbad\t0.1\t0.2\tup",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (annotation_dir / "accessibility_annotation.tsv").write_text(
        "gene_symbol\taccessibility_status\troute\nIL6\tSUPPORTED\tsecreted\n",
        encoding="utf-8",
    )
    return project


class EvidenceImportTest(unittest.TestCase):
    def test_import_writes_schema_version_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            db_path = import_evidence(project)
            con = sqlite3.connect(db_path)
            try:
                version = con.execute(
                    "SELECT value FROM evidence_metadata WHERE key = 'schema_version'"
                ).fetchone()[0]
                count = con.execute("SELECT COUNT(*) FROM evidence_item").fetchone()[0]
                lineage_missing = con.execute(
                    """
                    SELECT COUNT(*)
                    FROM evidence_item
                    WHERE COALESCE(run_id, '') = ''
                       OR COALESCE(artifact_id, '') = ''
                       OR COALESCE(module_version, '') = ''
                    """
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertEqual(count, 3)
            self.assertEqual(lineage_missing, 0)

            summary = json.loads((project / "results" / "evidence_import" / "import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["schema_version"], SCHEMA_VERSION)
            self.assertEqual(summary["inserted_rows"], 3)
            self.assertEqual(summary["rejected_rows"], 1)
            self.assertEqual(summary["by_evidence_type"]["bulk_deg"], 1)
            self.assertEqual(summary["by_evidence_type"]["accessibility"], 1)
            self.assertEqual(summary["by_evidence_type"]["surface_marker_annotation"], 1)

    def test_invalid_rows_are_written_to_rejected_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            import_evidence(project)
            with (project / "results" / "evidence_import" / "rejected_rows.tsv").open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["evidence_type"], "bulk_deg")
            self.assertIn("EvidenceItem.entity_symbol: must not be empty", rows[0]["reason"])
            self.assertIn("effect_size must be numeric", rows[0]["reason"])

    def test_qc_gate_does_not_pass_evidence_on_module_hint_without_artifact_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            qc_dir = project / "results" / "qc"
            qc_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "qc_report_id": "qc_other",
                "work_order_id": "wo_other",
                "module_id": "P4_bulk_other",
                "dataset_id": "other",
                "overall_status": "pass",
                "artifacts": ["results/bulk_deg_other/deg_results.tsv"],
            }
            (qc_dir / "qc_other.json").write_text(json.dumps(report), encoding="utf-8")
            (qc_dir / "task_qc_reports.json").write_text(
                json.dumps(
                    {
                        "schema_version": "v0.1.task_qc_report_index",
                        "project_id": "demo",
                        "reports": [
                            {
                                "qc_report_id": "qc_other",
                                "work_order_id": "wo_other",
                                "module_id": "P4_bulk_other",
                                "dataset_id": "other",
                                "overall_status": "pass",
                                "path": "results/qc/qc_other.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            db_path = import_evidence(project)
            con = sqlite3.connect(db_path)
            try:
                status = con.execute(
                    "SELECT review_status FROM evidence_item WHERE evidence_type = 'bulk_deg' AND entity_symbol = 'IL6'"
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(status, "QC_REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
