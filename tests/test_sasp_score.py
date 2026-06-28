import csv
import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.sasp_score import run_sasp_score


class TestSaspScore(unittest.TestCase):
    def test_run_sasp_score_writes_dataset_and_gene_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "research_spec.json").write_text(json.dumps({"sasp_core": ["IL6", "CXCL8"]}), encoding="utf-8")
            deg_dir = project / "results" / "bulk_deg_ds1"
            deg_dir.mkdir(parents=True)
            (deg_dir / "deg_results.tsv").write_text(
                "gene_symbol\tlogFC\tp_value\tadj_p_value\tdirection\n"
                "IL6\t2.5\t0.0001\t0.001\tup\n"
                "CXCL8\t-1.0\t0.01\t0.04\tdown\n"
                "OTHER\t5\t0.001\t0.01\tup\n",
                encoding="utf-8",
            )

            result = run_sasp_score(project)

            self.assertEqual(result["manifest"]["dataset_count"], 1)
            gene_path = project / "results" / "sasp_score" / "sasp_gene_scores.tsv"
            dataset_path = project / "results" / "sasp_score" / "sasp_dataset_scores.tsv"
            with gene_path.open(encoding="utf-8") as f:
                genes = list(csv.DictReader(f, delimiter="\t"))
            with dataset_path.open(encoding="utf-8") as f:
                datasets = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual([row["gene_symbol"] for row in genes], ["IL6", "CXCL8"])
            self.assertEqual(datasets[0]["matched_sasp_genes"], "2")
            self.assertEqual(datasets[0]["up_sasp_genes"], "1")
            self.assertEqual(datasets[0]["down_sasp_genes"], "1")
            self.assertEqual(datasets[0]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
