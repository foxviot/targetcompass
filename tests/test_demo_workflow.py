import csv
import hashlib
import sqlite3
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "vascular_aging_demo"


class DemoWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(
            [
                sys.executable,
                "tc_lite.py",
                "parse-interest",
                "--project",
                "vascular_aging_demo",
                "--text",
                "Find secreted targets for human endothelial senescence in vascular aging",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            [sys.executable, "tc_lite.py", "demo", "--project", "vascular_aging_demo"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )

    def test_key_outputs_exist(self):
        expected = [
            PROJECT / "screening_report.md",
            PROJECT / "analysis_plan.json",
            PROJECT / "analysis_module_registry.json",
            PROJECT / "results" / "bulk_deg_ds_fixture_vascular_aging" / "deg_results.tsv",
            PROJECT / "results" / "bulk_deg_ds_fixture_vascular_aging" / "run_manifest.json",
            PROJECT / "results" / "bulk_deg_ds_fixture_vascular_aging" / "qc_summary.json",
            PROJECT / "results" / "annotation" / "accessibility_annotation.tsv",
            PROJECT / "results" / "annotation" / "safety_flags.tsv",
            PROJECT / "evidence.sqlite",
            PROJECT / "candidate_scores.csv",
            PROJECT / "reports" / "target_report.html",
            PROJECT / "reports" / "target_report.docx",
            PROJECT / "reports" / "target_report_structured.json",
        ]
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.exists(), f"missing expected output: {path}")

    def test_candidate_scores_have_expected_top_candidate(self):
        with (PROJECT / "candidate_scores.csv").open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual(rows[0]["entity_symbol"], "CXCL8")
        self.assertEqual(rows[0]["hard_gate_status"], "PASS")
        self.assertEqual(rows[0]["safety_gate"], "PASS")

    def test_evidence_database_is_traceable(self):
        con = sqlite3.connect(PROJECT / "evidence.sqlite")
        try:
            count = con.execute("SELECT COUNT(*) FROM evidence_item").fetchone()[0]
            real_count = con.execute(
                "SELECT COUNT(*) FROM evidence_item WHERE source_dataset = ?",
                ("GSE312006",),
            ).fetchone()[0]
            missing_lineage = con.execute(
                """
                SELECT COUNT(*)
                FROM evidence_item
                WHERE COALESCE(source_dataset, '') = ''
                  AND COALESCE(artifact_path, '') = ''
                """
            ).fetchone()[0]
        finally:
            con.close()
        self.assertGreater(count, 1000)
        self.assertGreater(real_count, 1000)
        self.assertEqual(missing_lineage, 0)

    def test_score_output_is_stable(self):
        score_path = PROJECT / "candidate_scores.csv"
        before = hashlib.sha256(score_path.read_bytes()).hexdigest()
        subprocess.run(
            [sys.executable, "tc_lite.py", "score", "--project", "vascular_aging_demo"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        after = hashlib.sha256(score_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_word_report_is_valid_docx_package(self):
        with zipfile.ZipFile(PROJECT / "reports" / "target_report.docx") as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("word/document.xml", zf.namelist())


if __name__ == "__main__":
    unittest.main()
