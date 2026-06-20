import csv
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.enrichment import run_enrichment


class EnrichmentTest(unittest.TestCase):
    def test_run_enrichment_writes_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            deg_dir = project / "results" / "bulk_deg_ds_test"
            deg_dir.mkdir(parents=True)
            (deg_dir / "deg_results.tsv").write_text(
                "gene_symbol\tcase_mean\tcontrol_mean\tlogFC\tp_value\tadj_p_value\tdirection\n"
                "IL6\t10\t1\t3\t0.001\t0.01\tup\n"
                "CXCL8\t10\t1\t3\t0.001\t0.01\tup\n"
                "VCAM1\t10\t1\t3\t0.001\t0.01\tup\n"
                "ACTB\t1\t1\t0\t1\t1\tdown\n",
                encoding="utf-8",
            )
            out = run_enrichment(project)
            self.assertTrue(out.exists())
            with out.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertGreater(len(rows), 0)
            self.assertEqual(rows[0]["dataset_id"], "ds_test")
            self.assertIn("adj_p_value", rows[0])
            self.assertEqual(rows[0]["method"], "ORA")
            self.assertIn("gene_set_hash", rows[0])
            self.assertTrue((out.parent / "gsea_preranked_results.tsv").exists())
            self.assertTrue((out.parent / "gene_set_snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()
