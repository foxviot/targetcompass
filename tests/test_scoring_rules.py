import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from targetcompass_lite.evidence_db import SCHEMA
from targetcompass_lite.scoring import DEFAULT_RULES, load_scoring_rules, score_project


def _write_scoring_project(
    root: Path,
    gene: str,
    *,
    route: str,
    safety_gate: str,
    include_deg: bool = True,
) -> Path:
    project = root / "project"
    annotation = project / "results" / "annotation"
    annotation.mkdir(parents=True)
    (annotation / "accessibility_annotation.tsv").write_text(
        f"gene_symbol\taccessibility_status\troute\n{gene}\tSUPPORTED\t{route}\n",
        encoding="utf-8",
    )
    (annotation / "safety_flags.tsv").write_text(
        f"gene_symbol\tsafety_gate\n{gene}\t{safety_gate}\n",
        encoding="utf-8",
    )
    con = sqlite3.connect(project / "evidence.sqlite")
    con.executescript(SCHEMA)
    created = datetime.now(timezone.utc).isoformat()
    if include_deg:
        con.execute(
            """
            INSERT INTO evidence_item
            (evidence_id, project_id, entity_symbol, disease_context, evidence_type,
             direction, effect_size, p_value, quality_score, source_dataset, artifact_path, limitation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{gene}-deg",
                "project",
                gene,
                "vascular_aging",
                "bulk_deg",
                "up",
                5.0,
                0.001,
                0.8,
                "ds_test",
                "results/bulk_deg_ds_test/deg_results.tsv",
                "test evidence",
                created,
            ),
        )
    else:
        con.execute(
            """
            INSERT INTO evidence_item
            (evidence_id, project_id, entity_symbol, disease_context, route, evidence_type,
             quality_score, artifact_path, limitation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{gene}-accessibility",
                "project",
                gene,
                "vascular_aging",
                route,
                "accessibility",
                0.8,
                "results/annotation/accessibility_annotation.tsv",
                "test evidence",
                created,
            ),
        )
    con.commit()
    con.close()
    return project


def _score_one(project: Path) -> dict:
    out = score_project(project)
    with out.open(encoding="utf-8") as f:
        return next(csv.DictReader(f))


class ScoringRulesTest(unittest.TestCase):
    def test_default_rules_load(self):
        rules = load_scoring_rules()
        self.assertEqual(rules["rule_id"], "vaccine_target_v0")
        self.assertEqual(rules["expression"]["max_score"], 25)
        self.assertEqual(rules["route"]["supported_score"], 15)
        self.assertIn("secreted", rules["route"]["supported_routes"])
        self.assertEqual(rules["safety"]["scores"]["PASS"], 20)
        self.assertEqual(rules["safety"]["scores"]["UNKNOWN"], 5)
        self.assertEqual(rules["tiers"]["A_min_score"], 70)

    def test_default_rules_file_exists(self):
        self.assertTrue(DEFAULT_RULES.exists())

    def test_missing_disease_evidence_sets_hard_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_scoring_project(Path(tmp), "TEST1", route="secreted", safety_gate="PASS", include_deg=False)
            row = _score_one(project)
            self.assertEqual(row["hard_gate_status"], "REJECTED_NO_DISEASE_EVIDENCE")
            self.assertEqual(row["tier"], "C")

    def test_unknown_route_sets_hard_gate_and_blocks_tier_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_scoring_project(Path(tmp), "TEST2", route="unknown", safety_gate="PASS")
            row = _score_one(project)
            self.assertEqual(row["hard_gate_status"], "ROUTE_UNKNOWN")
            self.assertNotEqual(row["tier"], "A")

    def test_safety_excluded_sets_hard_gate_and_blocks_tier_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_scoring_project(Path(tmp), "TEST3", route="secreted", safety_gate="EXCLUDED")
            row = _score_one(project)
            self.assertEqual(row["hard_gate_status"], "EXCLUDED_SAFETY")
            self.assertEqual(row["tier"], "C")


if __name__ == "__main__":
    unittest.main()
