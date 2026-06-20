import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "vascular_aging_demo"


class ReportStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._run(
            [
                sys.executable,
                "tc_lite.py",
                "parse-interest",
                "--project",
                "vascular_aging_demo",
                "--text",
                "Find secreted targets for human endothelial senescence in vascular aging",
            ]
        )
        cls._run([sys.executable, "tc_lite.py", "demo", "--project", "vascular_aging_demo"])

    @classmethod
    def _run(cls, command: list[str]) -> None:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(
                "command failed: "
                + " ".join(command)
                + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

    def test_report_has_research_style_sections(self):
        html = (PROJECT / "reports" / "target_report.html").read_text(encoding="utf-8")
        for heading in [
            "执行摘要",
            "研究问题与边界",
            "方法与模块",
            "数据来源与QC",
            "候选排序",
            "证据链",
            "限制与风险",
            "实验建议",
            "审批与审计",
        ]:
            with self.subTest(heading=heading):
                self.assertIn(heading, html)

    def test_report_contains_qc_modules_evidence_and_review_tables(self):
        html = (PROJECT / "reports" / "target_report.html").read_text(encoding="utf-8")
        self.assertIn("Bulk RNA / microarray QC", html)
        self.assertIn("bulk_deg_v1", html)
        self.assertIn("scrna_pseudobulk_v0", html)
        self.assertIn("CXCL8 evidence chain", html)
        self.assertIn("GSE312006", html)
        self.assertIn("Adapter audit", html)
        self.assertTrue("READY_FOR_EXPLORATORY_VALIDATION" in html or "REVIEW_REQUIRED" in html)

    def test_structured_report_has_stable_research_outputs(self):
        data = json.loads((PROJECT / "reports" / "target_report_structured.json").read_text(encoding="utf-8"))
        self.assertEqual(data["report_version"], "0.4")
        for key in [
            "executive_summary",
            "research_question",
            "methods",
            "data_sources_and_qc",
            "advanced_analysis",
            "candidate_ranking",
            "evidence_chain",
            "report_evidence_refs",
            "scoring_manifest",
            "limitations",
            "experiment_suggestions",
            "approval_and_audit",
        ]:
            with self.subTest(key=key):
                self.assertIn(key, data)

        self.assertIn("analysis_modules", data["methods"])
        self.assertTrue(data["data_sources_and_qc"]["datasets"])
        self.assertTrue(data["data_sources_and_qc"]["bulk_rna_microarray_qc"])
        self.assertIn("meta_analysis", data["advanced_analysis"])
        self.assertIn("causal_evidence", data["advanced_analysis"])
        self.assertIn("mcp_call_audit", data["approval_and_audit"])
        self.assertIn("codex_engineering", data["approval_and_audit"])
        self.assertTrue(data["candidate_ranking"])
        self.assertTrue(data["evidence_chain"])
        self.assertTrue(data["experiment_suggestions"])

    def test_report_includes_v4_advanced_audit_sections(self):
        html = (PROJECT / "reports" / "target_report.html").read_text(encoding="utf-8")
        self.assertIn("Meta-analysis overview", html)
        self.assertIn("Causal evidence grading", html)
        self.assertIn("MCP call audit", html)
        self.assertIn("Codex engineering results", html)

    def test_structured_report_preserves_artifacts_and_qc(self):
        data = json.loads((PROJECT / "reports" / "target_report_structured.json").read_text(encoding="utf-8"))
        qc_rows = data["data_sources_and_qc"]["bulk_rna_microarray_qc"]
        self.assertTrue(any(row["dataset_id"] == "GSE312006" for row in qc_rows))
        for row in qc_rows:
            self.assertIn("matrix_type", row)
            self.assertIn("runner_type", row)
            self.assertIn("qc_status", row)
            self.assertIn("artifact", row)

        evidence = data["evidence_chain"][0]
        self.assertIn("gene", evidence)
        self.assertIn("score_id", evidence)
        self.assertIn("evidence_snapshot_id", evidence)
        self.assertIn("evidence_refs", evidence)
        self.assertIn("evidence", evidence)
        self.assertTrue(evidence["evidence"])
        self.assertIn("evidence_id", evidence["evidence"][0])
        self.assertIn("artifact_path", evidence["evidence"][0])
        self.assertIn("run_id", evidence["evidence"][0])
        self.assertIn("artifact_id", evidence["evidence"][0])

    def test_report_has_no_mojibake_section_titles(self):
        html = (PROJECT / "reports" / "target_report.html").read_text(encoding="utf-8")
        for bad in ["缂傚倷", "闁荤", "闂佽", "閻庡", "鎵ц", "璇佹"]:
            with self.subTest(bad=bad):
                self.assertNotIn(bad, html)

    def test_report_avoids_overclaims(self):
        html = (PROJECT / "reports" / "target_report.html").read_text(encoding="utf-8").lower()
        for phrase in ["clinical recommendation", "cure"]:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase.lower(), html)


if __name__ == "__main__":
    unittest.main()
