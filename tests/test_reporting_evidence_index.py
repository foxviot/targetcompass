import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.reporting import build_report


class ReportingEvidenceIndexTest(unittest.TestCase):
    def test_build_report_refreshes_evidence_review_report_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_minimal_project(project)
            html_path, _ = build_report(project)

            index_path = project / "v4" / "evidence_review_report_index.json"
            structured_path = project / "reports" / "target_report_structured.json"
            self.assertTrue(index_path.exists())
            structured = json.loads(structured_path.read_text(encoding="utf-8"))
            self.assertEqual(structured["evidence_review_report_index"]["path"], "v4/evidence_review_report_index.json")
            self.assertEqual(structured["evidence_review_report_index"]["evidence_count"], 4)
            self.assertIn("candidate_evidence_cards", structured)
            card = structured["candidate_evidence_cards"][0]
            self.assertEqual(card["gene"], "CXCL8")
            self.assertEqual(card["sasp"]["status"], "present")
            self.assertEqual(card["cell_type"]["status"], "present")
            self.assertEqual(card["surface_or_secreted"]["status"], "present")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Evidence trace index", html)
            self.assertIn("候选基因证据卡", html)
            self.assertIn("SASP", html)
            self.assertIn("Surface / secreted", html)


def _write_minimal_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "results" / "annotation").mkdir(parents=True)
    (project / "results" / "scoring").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "research_spec.json").write_text(
        json.dumps({"research_theme": "vascular aging", "disease_scope": {"canonical": "vascular aging"}}),
        encoding="utf-8",
    )
    _write_csv(
        project / "candidate_scores.csv",
        [
            {
                "score_id": "score_1",
                "entity_symbol": "CXCL8",
                "route": "secreted",
                "final_score": "80",
                "tier": "A",
                "hard_gate_status": "PASS",
                "safety_gate": "PASS",
                "next_experiments": "ELISA",
                "evidence_snapshot_id": "es_1",
                "evidence_refs": "ev1",
            }
        ],
    )
    _write_csv(project / "eligible_datasets.csv", [{"dataset_id": "ds1", "source_class": "fixture", "grade": "A", "modality": "bulk", "metadata_quality_label": "good", "metadata_quality_score": "0.9", "recommended_use": "deg"}])
    _write_csv(project / "dataset_match_report.csv", [{"dataset_id": "ds1", "match_status": "MATCH", "warnings": ""}])
    (project / "results" / "annotation" / "unknown_review.tsv").write_text("gene_symbol\tmissing_fields\troute\tsafety_gate\trecommended_action\n", encoding="utf-8")
    (project / "results" / "review_queue.json").write_text(
        json.dumps({"items": [{"item_type": "causal_grade", "item_id": "CXCL8", "title": "Causal evidence CXCL8", "review_status": "pending", "reason": "human_review_required", "report_ref": "reports/target_report.html#causal-grade-cxcl8"}]}),
        encoding="utf-8",
    )
    (project / "results" / "scoring" / "target_score_manifest.json").write_text("{}", encoding="utf-8")
    con = sqlite3.connect(project / "evidence.sqlite")
    con.executescript(
        """
        CREATE TABLE evidence_item (
          evidence_id TEXT PRIMARY KEY, project_id TEXT, entity_symbol TEXT, entity_type TEXT,
          disease_context TEXT, organism TEXT, tissue TEXT, route TEXT, evidence_type TEXT,
          direction TEXT, effect_size REAL, p_value REAL, quality_score REAL,
          evidence_level TEXT, evidence_weight REAL, evidence_basis TEXT, review_status TEXT,
          source_dataset TEXT, artifact_path TEXT, run_id TEXT, artifact_id TEXT, module_version TEXT,
          limitation TEXT, created_at TEXT
        );
        INSERT INTO evidence_item
        (evidence_id, project_id, entity_symbol, evidence_type, route, direction, effect_size, quality_score, evidence_level, evidence_weight, source_dataset, artifact_path, run_id, artifact_id, module_version, review_status, limitation, created_at)
        VALUES
        ('ev1', 'demo', 'CXCL8', 'qtl_colocalization', '', '', NULL, 0.8, 'L4_genetic', 0.9, 'genetic_demo', 'results/genetic_coloc_mr/genetic_evidence.tsv', 'run_1', 'artifact_1', 'genetic_coloc_mr_v1', 'PENDING', 'genetic evidence', 'now'),
        ('ev_sasp', 'demo', 'CXCL8', 'sasp_score', '', 'up', 12.5, 0.84, 'L3_omics', 0.8, 'GSE_TEST', 'results/sasp_score/sasp_gene_scores.tsv', 'run_1', 'artifact_sasp', 'sasp_score_v1', 'PENDING', 'SASP score evidence', 'now'),
        ('ev_cell', 'demo', 'CXCL8', 'cell_type_expression', '', '', NULL, 0.6, 'L2_database', 0.65, 'HPA', 'results/cell_type_evidence/cell_type_evidence.tsv', 'run_1', 'artifact_cell', 'cell_type_evidence_v1', 'PENDING', 'cell type evidence', 'now'),
        ('ev_surface', 'demo', 'CXCL8', 'surface_marker_annotation', 'secreted', '', NULL, 0.72, 'L2_database', 0.65, 'annotation_accessibility', 'results/annotation/accessibility_annotation.tsv', 'run_1', 'artifact_surface', 'surface_marker_annotation_v1', 'PENDING', 'annotation-level evidence', 'now');
        """
    )
    con.commit()
    con.close()


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
