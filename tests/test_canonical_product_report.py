import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.product_report import build_productized_project_report


class CanonicalProductReportTest(unittest.TestCase):
    def test_builds_product_report_from_candidate_scores_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            score_json = {
                "evidence_axis_coverage": {
                    "covered_axes": ["disease_relevant_expression", "SASP_annotation", "secreted_or_surface_annotation"],
                    "missing_axes": ["causal_or_genetic_support"],
                    "coverage_fraction": 0.75,
                },
                "evidence_level_counts": {"L2_database": 1, "L3_omics": 2},
            }
            (project / "candidate_scores.csv").write_text(
                "entity_symbol,route,final_score,tier,hard_gate_status,safety_gate,evidence_refs,score_json,next_experiments\n"
                f"IL6,secreted,95.5,A,PASS,PASS,ev1;ev2,\"{json.dumps(score_json).replace(chr(34), chr(34) + chr(34))}\",qPCR and neutralization assay\n",
                encoding="utf-8",
            )
            cell_dir = project / "results" / "cell_type_evidence"
            cell_dir.mkdir(parents=True)
            (cell_dir / "cell_type_evidence.tsv").write_text(
                "evidence_id\tproject_id\tentity_symbol\tcell_type\ttissue\tevidence_source\tconfidence\tlimitation\n"
                "ct1\tdemo\tIL6\tstromal cell\tskeletal muscle\tHPA\t0.7\tpending validation\n",
                encoding="utf-8",
            )
            (project / "v5" / "recovery").mkdir(parents=True)
            (project / "v5" / "recovery" / "failure_recovery_report.json").write_text(
                json.dumps({"items": [{"category": "metadata", "reason": "manual review required"}]}),
                encoding="utf-8",
            )
            (project / "v5" / "reports").mkdir(parents=True)
            (project / "v5" / "reports" / "canonical_report_manifest.json").write_text(
                json.dumps({"human_review_gate": {"required": True, "reason": "needs reviewer"}, "claim_ceiling": {"max_allowed_claim": "association"}}),
                encoding="utf-8",
            )

            result = build_productized_project_report(project)

            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["top_candidates"][0]["gene"], "IL6")
            self.assertTrue((project / "v5" / "reports" / "product_report.html").exists())
            self.assertTrue((project / "v5" / "reports" / "product_report_manifest.json").exists())
            html = (project / "v5" / "reports" / "product_report.html").read_text(encoding="utf-8")
            self.assertIn("TargetCompass v5 项目报告", html)
            self.assertIn("IL6", html)
            self.assertIn("manual review required", html)
            self.assertIn("causal_or_genetic_support", html)


if __name__ == "__main__":
    unittest.main()
