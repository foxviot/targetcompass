import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.annotation import annotate_project
from targetcompass_lite.db_adapters import available_database_adapters
from targetcompass_lite.enrichment import run_enrichment
from targetcompass_lite.evidence_db import import_evidence
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
    deg = project / "results" / "bulk_deg_ds" / "deg_results.tsv"
    deg.parent.mkdir(parents=True)
    (project / "configs").mkdir(parents=True)
    (project / "research_spec.json").write_text(json.dumps(SPEC), encoding="utf-8")
    with deg.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["gene_symbol", "logFC", "p_value", "adj_p_value", "direction"], delimiter="\t")
        writer.writeheader()
        writer.writerow({"gene_symbol": "CXCL8", "logFC": "2", "p_value": "0.001", "adj_p_value": "0.01", "direction": "up"})
        writer.writerow({"gene_symbol": "VCAM1", "logFC": "2", "p_value": "0.001", "adj_p_value": "0.01", "direction": "up"})
    return project


class StandardDatabaseAdaptersTest(unittest.TestCase):
    def test_adapter_catalog_contains_standard_sources(self):
        ids = {row["adapter_id"] for row in available_database_adapters()}
        for adapter_id in {
            "uniprot_target_v0",
            "hpa_safety_accessibility_v0",
            "opentargets_evidence_v0",
            "disgenet_evidence_v0",
            "gwas_catalog_evidence_v0",
            "msigdb_gene_sets_v0",
            "reactome_gene_sets_v0",
        }:
            self.assertIn(adapter_id, ids)

    def test_uniprot_and_hpa_feed_annotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            uniprot = Path(tmp) / "uniprot.tsv"
            uniprot.write_text(
                "Entry\tGene Names (primary)\tProtein names\tReviewed\tSubcellular location [CC]\n"
                "P10145\tCXCL8\tInterleukin-8\treviewed\tSecreted\n",
                encoding="utf-8",
            )
            hpa = Path(tmp) / "hpa.tsv"
            hpa.write_text(
                "Gene\tSubcellular location\tRNA tissue specificity\tBrain\tHeart\n"
                "VCAM1\tPlasma membrane\tTissue enhanced\tLow\tLow\n",
                encoding="utf-8",
            )
            add_resource(project, "uniprot_demo", "external_database", str(uniprot), adapter="uniprot_target_v0")
            add_resource(project, "hpa_demo", "external_database", str(hpa), adapter="hpa_safety_accessibility_v0")
            adapted = adapt_resources(project)
            self.assertTrue(any(row.get("normalized_accessibility") for row in adapted))
            annotate_project(project)
            access = (project / "results" / "annotation" / "accessibility_annotation.tsv").read_text(encoding="utf-8")
            safety = (project / "results" / "annotation" / "safety_flags.tsv").read_text(encoding="utf-8")
            self.assertIn("CXCL8\tsecreted\tSUPPORTED\tuniprot_demo", access)
            self.assertIn("VCAM1\tsurface\tSUPPORTED\thpa_demo", access)
            self.assertIn("VCAM1\tPASS", safety)

    def test_association_adapters_feed_evidence_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            open_targets = Path(tmp) / "opentargets.csv"
            open_targets.write_text("approvedSymbol,overallScore,diseaseName\nCXCL8,0.91,Vascular disease\n", encoding="utf-8")
            disgenet = Path(tmp) / "disgenet.tsv"
            disgenet.write_text("geneSymbol\tscore\tdiseaseName\nVCAM1\t0.72\tAtherosclerosis\n", encoding="utf-8")
            gwas = Path(tmp) / "gwas_catalog.tsv"
            gwas.write_text("MAPPED_GENE\tP-VALUE\tDISEASE/TRAIT\nCXCL8\t1e-9\tInflammation\n", encoding="utf-8")
            add_resource(project, "ot_demo", "external_database", str(open_targets), adapter="opentargets_evidence_v0")
            add_resource(project, "disgenet_demo", "external_database", str(disgenet), adapter="disgenet_evidence_v0")
            add_resource(project, "gwas_demo", "external_database", str(gwas), adapter="gwas_catalog_evidence_v0")
            adapt_resources(project)
            import_evidence(project)
            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                types = {
                    row[0]
                    for row in con.execute(
                        "SELECT DISTINCT evidence_type FROM evidence_item WHERE evidence_type LIKE '%association%'"
                    )
                }
            finally:
                con.close()
            self.assertIn("opentargets_association", types)
            self.assertIn("disgenet_association", types)
            self.assertIn("gwas_association", types)

    def test_msigdb_and_reactome_feed_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            msigdb = Path(tmp) / "msigdb_demo.gmt"
            msigdb.write_text("HALLMARK_INFLAMMATION\tInflammation\tCXCL8\tVCAM1\n", encoding="utf-8")
            reactome = Path(tmp) / "reactome.tsv"
            reactome.write_text("pathway_id\tpathway_name\tgene_symbols\nR-HSA-1\tCytokine signaling\tCXCL8;VCAM1\n", encoding="utf-8")
            add_resource(project, "msigdb_demo", "external_database", str(msigdb), adapter="msigdb_gene_sets_v0")
            add_resource(project, "reactome_demo", "external_database", str(reactome), adapter="reactome_gene_sets_v0")
            adapted = adapt_resources(project)
            self.assertTrue(any(row.get("normalized_gene_sets") for row in adapted))
            out = run_enrichment(project)
            text = out.read_text(encoding="utf-8")
            self.assertIn("HALLMARK_INFLAMMATION", text)
            self.assertIn("R-HSA-1", text)


if __name__ == "__main__":
    unittest.main()
