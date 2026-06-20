import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.annotation import annotate_project
from targetcompass_lite.enrichment import run_enrichment
from targetcompass_lite.evidence_db import import_evidence
from targetcompass_lite.db_adapters import available_database_adapters
from targetcompass_lite.knowledge import add_resource, adapt_resources


SPEC = {
    "project_id": "demo",
    "goal": "target_prioritization",
    "research_theme": "vascular aging",
    "disease_scope": {"canonical": "vascular aging", "related_phenotypes": []},
    "organisms": ["human"],
    "priority_tissues": ["vascular endothelium"],
    "priority_cells": ["endothelial cell"],
    "target_routes": ["surface", "secreted"],
    "modalities_mvp": {"required": ["bulk_expression"], "optional": []},
    "constraints": {"claim_policy": "association_only_without_genetic_or_experimental_validation"},
}


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    deg_dir = project / "results" / "bulk_deg_ds"
    deg_dir.mkdir(parents=True)
    (project / "configs").mkdir(parents=True)
    (project / "research_spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    with (deg_dir / "deg_results.tsv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["gene_symbol", "logFC", "p_value", "adj_p_value", "direction"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({"gene_symbol": "GENEA", "logFC": "2.1", "p_value": "0.001", "adj_p_value": "0.01", "direction": "up"})
        writer.writerow({"gene_symbol": "GENEB", "logFC": "1.5", "p_value": "0.002", "adj_p_value": "0.02", "direction": "up"})
    return project


class KnowledgeAdapterTest(unittest.TestCase):
    def test_standard_tables_are_normalized_and_used_by_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            annotation = Path(tmp) / "annotation.csv"
            annotation.write_text(
                "Approved Symbol,Subcellular Location,Safety,Description\n"
                "GENEA,cell surface,pass,custom surface target\n"
                "GENEB,secreted,review,custom secreted target\n",
                encoding="utf-8",
            )
            gene_sets = Path(tmp) / "sets.tsv"
            gene_sets.write_text("pathway_id\tpathway_name\tgene_symbols\nCUSTOM\tCustom pathway\tGENEA;GENEB\n", encoding="utf-8")
            evidence = Path(tmp) / "evidence.csv"
            evidence.write_text(
                "target_symbol,type,score,pvalue,confidence,association\n"
                "GENEA,genetic_association,0.8,0.03,0.7,positive\n",
                encoding="utf-8",
            )
            add_resource(project, "custom_annotation", "annotation_table", str(annotation))
            add_resource(project, "custom_sets", "gene_set", str(gene_sets))
            add_resource(project, "custom_external", "external_database", str(evidence))
            adapted = adapt_resources(project)
            self.assertEqual(sum(row.get("normalized_rows", 0) for row in adapted), 6)

            annotate_project(project)
            access_text = (project / "results" / "annotation" / "accessibility_annotation.tsv").read_text(encoding="utf-8")
            self.assertIn("GENEA\tsurface\tSUPPORTED\tcustom_annotation", access_text)

            enrichment_path = run_enrichment(project)
            self.assertIn("CUSTOM", enrichment_path.read_text(encoding="utf-8"))

            import_evidence(project)
            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                count = con.execute(
                    "SELECT COUNT(*) FROM evidence_item WHERE evidence_type = 'genetic_association'"
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(count, 1)

    def test_sqlite_database_adapter_normalizes_target_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            source = Path(tmp) / "targets.sqlite"
            con = sqlite3.connect(source)
            try:
                con.execute(
                    "CREATE TABLE target_evidence (target_symbol TEXT, type TEXT, score REAL, pvalue REAL, confidence REAL, location TEXT)"
                )
                con.execute(
                    "INSERT INTO target_evidence VALUES ('GENEA', 'database_prior', 1.2, 0.04, 0.8, 'plasma membrane')"
                )
                con.commit()
            finally:
                con.close()
            adapters = {row["adapter_id"] for row in available_database_adapters()}
            self.assertIn("sqlite_evidence_v0", adapters)
            add_resource(project, "sqlite_targets", "external_database", str(source), adapter="sqlite_evidence_v0")
            adapted = adapt_resources(project)
            sqlite_row = next(row for row in adapted if row["resource_id"] == "sqlite_targets")
            self.assertEqual(sqlite_row["database_adapter"], "sqlite_evidence_v0")
            self.assertEqual(sqlite_row["normalized_rows"], 1)
            self.assertEqual(sqlite_row["input_rows"], 1)
            self.assertEqual(sqlite_row["dropped_rows"], 0)
            self.assertEqual(sqlite_row["field_mapping"]["entity_symbol"], "target_symbol")
            import_evidence(project)
            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                count = con.execute(
                    "SELECT COUNT(*) FROM evidence_item WHERE evidence_type = 'database_prior'"
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
