import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.causal_evidence import grade_causal_evidence
from targetcompass_lite.enrichment import run_enrichment
from targetcompass_lite.evidence_db import import_evidence
from targetcompass_lite.genetic import run_genetic_coloc_mr
from targetcompass_lite.meta_analysis import run_meta_analysis
from targetcompass_lite.scrna import run_scrna_pseudobulk


def _write_spec(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "research_spec.json").write_text(
        json.dumps({"disease_scope": {"canonical": "vascular aging"}}),
        encoding="utf-8",
    )


def _write_deg(project: Path, dataset_id: str, il6_logfc: float, cxcl8_logfc: float) -> None:
    out = project / "results" / f"bulk_deg_{dataset_id}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "deg_results.tsv").write_text(
        "gene_symbol\tcase_mean\tcontrol_mean\tlogFC\tp_value\tadj_p_value\tdirection\n"
        f"IL6\t10\t1\t{il6_logfc}\t0.001\t0.01\tup\n"
        f"CXCL8\t10\t1\t{cxcl8_logfc}\t0.001\t0.02\tup\n"
        "ACTB\t1\t1\t0\t1\t1\tflat\n",
        encoding="utf-8",
    )


class AnalysisExtensionsTest(unittest.TestCase):
    def test_scrna_pseudobulk_writes_donor_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_spec(project)
            data = project / "data" / "scrna"
            data.mkdir(parents=True)
            (data / "counts.tsv").write_text(
                "gene_symbol\tc1\tc2\tc3\tc4\n"
                "IL6\t1\t2\t3\t4\n"
                "CXCL8\t0\t1\t0\t1\n",
                encoding="utf-8",
            )
            (data / "metadata.tsv").write_text(
                "cell_id\tdonor_id\tgroup\tcell_type\n"
                "c1\td1\tcase\tendo\n"
                "c2\td1\tcase\tendo\n"
                "c3\td2\tcontrol\tendo\n"
                "c4\td2\tcontrol\tendo\n",
                encoding="utf-8",
            )
            out = run_scrna_pseudobulk(project, "scrna_demo", "data/scrna/counts.tsv", "data/scrna/metadata.tsv", cell_type="endo")
            self.assertTrue(out.exists())
            qc = json.loads((out.parent / "qc_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(qc["pseudobulk_samples"], 2)
            self.assertIn("d1__case", out.read_text(encoding="utf-8"))

    def test_enrichment_writes_manifest_and_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_spec(project)
            _write_deg(project, "ds1", 3, 2)
            out = run_enrichment(project)
            self.assertTrue(out.exists())
            manifest = json.loads((out.parent / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["module_id"], "enrichment_v2")
            self.assertIn("output_hash", manifest)

    def test_meta_analysis_and_causal_grade_enter_evidence_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_spec(project)
            _write_deg(project, "ds1", 3, 2)
            _write_deg(project, "ds2", 2, 1)
            meta = run_meta_analysis(project)
            with meta.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(rows[0]["dataset_count"], "2")

            con = sqlite3.connect(project / "evidence.sqlite")
            con.executescript(
                """
                CREATE TABLE evidence_item (
                  evidence_id TEXT PRIMARY KEY, project_id TEXT, entity_symbol TEXT, entity_type TEXT,
                  disease_context TEXT, organism TEXT, tissue TEXT, route TEXT, evidence_type TEXT,
                  direction TEXT, effect_size REAL, p_value REAL, quality_score REAL, review_status TEXT,
                  source_dataset TEXT, artifact_path TEXT, run_id TEXT, artifact_id TEXT, module_version TEXT,
                  limitation TEXT, created_at TEXT
                );
                INSERT INTO evidence_item
                (evidence_id, project_id, entity_symbol, evidence_type, p_value, quality_score, created_at)
                VALUES ('ev1', 'demo', 'CXCL8', 'gwas_association', 1e-9, 0.8, 'now');
                """
            )
            con.commit()
            con.close()
            causal = grade_causal_evidence(project)
            self.assertIn("CXCL8\tB", causal.read_text(encoding="utf-8"))
            import_evidence(project)
            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                types = {row[0] for row in con.execute("SELECT DISTINCT evidence_type FROM evidence_item").fetchall()}
            finally:
                con.close()
            self.assertIn("deg_meta_analysis", types)
            self.assertIn("causal_grade", types)

    def test_genetic_coloc_mr_feeds_causal_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_spec(project)
            data = project / "data" / "genetic"
            data.mkdir(parents=True)
            (data / "gwas.tsv").write_text(
                "variant_id\tgene_symbol\tbeta\tp_value\teffect_allele\tother_allele\ttrait\n"
                "rs1\tCXCL8\t0.4\t1e-9\tA\tG\tinflammation\n",
                encoding="utf-8",
            )
            (data / "qtl.tsv").write_text(
                "variant_id\tgene_symbol\tbeta\tp_value\ttissue\n"
                "rs1\tCXCL8\t0.2\t1e-6\tartery\n",
                encoding="utf-8",
            )

            evidence = run_genetic_coloc_mr(
                project,
                "data/genetic/gwas.tsv",
                "data/genetic/qtl.tsv",
                dataset_id="genetic_demo",
            )
            self.assertTrue(evidence.exists())
            evidence_text = evidence.read_text(encoding="utf-8")
            self.assertIn("qtl_colocalization", evidence_text)
            self.assertIn("mendelian_randomization", evidence_text)

            import_evidence(project)
            causal = grade_causal_evidence(project)
            self.assertIn("CXCL8\tA", causal.read_text(encoding="utf-8"))

            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                types = {row[0] for row in con.execute("SELECT DISTINCT evidence_type FROM evidence_item").fetchall()}
            finally:
                con.close()
            self.assertIn("qtl_colocalization", types)
            self.assertIn("mendelian_randomization", types)


if __name__ == "__main__":
    unittest.main()
