import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.causal_evidence import DEFAULT_CAUSAL_RUBRIC, grade_causal_evidence
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
            self.assertEqual(qc["contrast"]["source"], "inferred_two_groups")
            self.assertTrue((out.parent / "donor_group_qc.tsv").exists())
            self.assertTrue((out.parent / "group_qc.tsv").exists())
            manifest = json.loads((out.parent / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["inputs"]["count_matrix_hash"])
            self.assertIn("d1__case", out.read_text(encoding="utf-8"))

    def test_scrna_pseudobulk_enforces_donor_group_qc_and_explicit_contrast(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_spec(project)
            data = project / "data" / "scrna"
            data.mkdir(parents=True)
            (data / "counts.tsv").write_text(
                "gene_symbol\tc1\tc2\tc3\tc4\tc5\tc6\n"
                "IL6\t1\t2\t3\t4\t5\t6\n"
                "CXCL8\t0\t1\t0\t1\t0\t1\n",
                encoding="utf-8",
            )
            (data / "metadata.tsv").write_text(
                "cell_id\tdonor_id\tgroup\tcell_type\n"
                "c1\td1\tcase\tendo\n"
                "c2\td1\tcase\tendo\n"
                "c3\td2\tcase\tendo\n"
                "c4\td2\tcase\tendo\n"
                "c5\td3\tcontrol\tendo\n"
                "c6\td3\tcontrol\tendo\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "below min_donors_per_group"):
                run_scrna_pseudobulk(
                    project,
                    "scrna_demo",
                    "data/scrna/counts.tsv",
                    "data/scrna/metadata.tsv",
                    cell_type="endo",
                    min_donors_per_group=2,
                    case_group="case",
                    control_group="control",
                )
            out = run_scrna_pseudobulk(
                project,
                "scrna_demo",
                "data/scrna/counts.tsv",
                "data/scrna/metadata.tsv",
                cell_type="endo",
                min_donors_per_group=1,
                case_group="case",
                control_group="control",
            )
            metadata_text = (out.parent / "pseudobulk_metadata.tsv").read_text(encoding="utf-8")
            self.assertIn("contrast_role", metadata_text)
            self.assertIn("case", metadata_text)
            self.assertIn("control", metadata_text)

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
            self.assertIn("gene_set_snapshot_hash", manifest)
            self.assertIn("gsea_output_hash", manifest)

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
            self.assertIn("random_effect_logFC", rows[0])
            self.assertIn("heterogeneity_i2", rows[0])
            self.assertIn("qc_flags", rows[0])
            manifest_meta = json.loads((project / "results" / "meta_analysis" / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_meta["schema_version"], "v4.meta_analysis_manifest/0.2")
            self.assertTrue((project / "results" / "meta_analysis" / "forest_plot_index.tsv").exists())
            self.assertTrue((project / rows[0]["forest_plot"]).exists())

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
            manifest = json.loads((project / "results" / "causal_evidence" / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["rubric_id"], "causal_review")
            self.assertEqual(manifest["rubric_version"], "0.1.0")
            self.assertTrue(manifest["rubric_hash"])
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
            causal_text = causal.read_text(encoding="utf-8")
            self.assertIn("CXCL8\tA", causal_text)
            self.assertIn("triage_high", causal_text)
            self.assertIn("qtl_colocalization", causal_text)
            self.assertIn("mendelian_randomization", causal_text)
            self.assertIn("human_review_required", causal_text)
            self.assertIn("results/genetic_coloc_mr/genetic_evidence.tsv", causal_text)

            con = sqlite3.connect(project / "evidence.sqlite")
            try:
                types = {row[0] for row in con.execute("SELECT DISTINCT evidence_type FROM evidence_item").fetchall()}
            finally:
                con.close()
            self.assertIn("qtl_colocalization", types)
            self.assertIn("mendelian_randomization", types)

    def test_project_causal_rubric_overrides_support_and_review_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_spec(project)
            (project / "configs").mkdir(parents=True)
            rubric = json.loads(DEFAULT_CAUSAL_RUBRIC.read_text(encoding="utf-8"))
            rubric["version"] = "0.1.1-test"
            rubric["support_levels"]["B"] = "custom_moderate"
            rubric["review_flags"]["method_flags"]["association"].append("custom_association_review")
            (project / "configs" / "causal_review_rubric.json").write_text(
                json.dumps(rubric, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

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
                (evidence_id, project_id, entity_symbol, evidence_type, p_value, quality_score, limitation, created_at)
                VALUES ('ev1', 'demo', 'CXCL8', 'gwas_association', 1e-9, 0.8, 'single variant proxy', 'now');
                """
            )
            con.commit()
            con.close()

            causal = grade_causal_evidence(project)
            causal_text = causal.read_text(encoding="utf-8")
            self.assertIn("CXCL8\tB\tcustom_moderate", causal_text)
            self.assertIn("custom_association_review", causal_text)
            self.assertIn("single_variant_mr_proxy", causal_text)
            manifest = json.loads((project / "results" / "causal_evidence" / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["rubric_version"], "0.1.1-test")
            self.assertIn("configs\\causal_review_rubric.json", manifest["rubric_path"])
            self.assertTrue(manifest["rubric_hash"])


if __name__ == "__main__":
    unittest.main()
