import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.adapter_audit import build_adapter_audit
from targetcompass_lite.ideas import generate_idea_batch
from targetcompass_lite.methods.contracts import MethodContext
from targetcompass_lite.methods.registry import run_method
from targetcompass_lite.package import export_run_package
from targetcompass_lite.review import build_review_queue, load_reviews, record_review


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "configs").mkdir(parents=True)
    (project / "results" / "ideas").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
    (project / "research_spec.json").write_text('{"project_id":"demo"}', encoding="utf-8")
    (project / "reports" / "target_report.html").write_text("<html>report</html>", encoding="utf-8")
    return project


class DeliveryTest(unittest.TestCase):
    def test_gpt_idea_query_falls_back_without_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            context = MethodContext(project, "vascular aging targets", "rule_based", [], False, 3)
            with patch.dict("os.environ", {}, clear=True):
                result = run_method("query", context, method_id="gpt_idea_query_v0")
            self.assertEqual(result.details["source"], "local_fallback")
            self.assertEqual(len(json.loads((project / "results" / "ideas" / "idea_batch.json").read_text())), 3)

    def test_review_action_updates_idea_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            ideas = generate_idea_batch(project, "vascular aging targets", 1)
            record_review(project, "idea", ideas[0]["idea_id"], "approve", "looks feasible")
            updated = json.loads((project / "results" / "ideas" / "idea_batch.json").read_text(encoding="utf-8"))
            self.assertEqual(updated[0]["review_status"], "approve")
            self.assertEqual(load_reviews(project)[0]["note"], "looks feasible")

    def test_causal_grade_review_enters_queue_and_updates_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            causal_dir = project / "results" / "causal_evidence"
            causal_dir.mkdir(parents=True)
            (causal_dir / "causal_evidence_grades.tsv").write_text(
                "gene_symbol\tcausal_grade\tsupport_level\tmethods\tevidence_types\tevidence_count\tbest_p_value\trationale\tevidence_ids\tartifact_refs\treview_flags\treview_status\tlimitation\n"
                "CXCL8\tA\ttriage_high\tcoloc;mr\tmendelian_randomization;qtl_colocalization\t2\t1e-6\tMR and coloc present\tev1;ev2\tresults/genetic_coloc_mr/genetic_evidence.tsv\thuman_review_required;ld_locus_review_required\tHUMAN_REVIEW_REQUIRED\treview required\n",
                encoding="utf-8",
            )

            queue = build_review_queue(project)
            self.assertTrue(any(item["item_type"] == "causal_grade" and item["item_id"] == "CXCL8" for item in queue["items"]))
            record_review(project, "causal_grade", "CXCL8", "approve", reason="LD locus and MR proxy reviewed")
            text = (causal_dir / "causal_evidence_grades.tsv").read_text(encoding="utf-8")
            self.assertIn("CXCL8\tA", text)
            self.assertIn("approve", text)
            self.assertIn("LD locus and MR proxy reviewed", text)

    def test_adapter_audit_and_run_package_are_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            (project / "configs" / "knowledge_registry.json").write_text(
                json.dumps(
                    [
                        {
                            "resource_id": "db1",
                            "resource_type": "external_database",
                            "adapter": "sqlite_evidence_v0",
                            "status": "adapted",
                            "database_adapter": "sqlite_evidence_v0",
                            "normalized_rows": 2,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            json_path, tsv_path = build_adapter_audit(project)
            self.assertTrue(json_path.exists())
            self.assertIn("sqlite_evidence_v0", tsv_path.read_text(encoding="utf-8"))
            package = export_run_package(project)
            self.assertTrue(package.exists())
            with zipfile.ZipFile(package) as zf:
                self.assertIn("package_manifest.json", zf.namelist())
                self.assertIn("results/adapter_audit/adapter_audit.tsv", zf.namelist())


if __name__ == "__main__":
    unittest.main()
