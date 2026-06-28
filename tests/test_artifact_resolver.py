import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.artifact_resolver import resolve_work_order_inputs
from targetcompass_lite.orchestrator import submit_orchestrator_run
from targetcompass_lite.v4 import compile_v4_work_orders


class ArtifactResolverTest(unittest.TestCase):
    def test_scrna_work_order_missing_inputs_fail_before_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            plan = {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "P_scrna",
                        "module": "scrna_pseudobulk",
                        "dataset_id": "sc1",
                        "inputs": {},
                        "parameters": {},
                        "expected_outputs": ["results/scrna_pseudobulk_sc1/pseudobulk_matrix.tsv"],
                    }
                ],
            }
            compile_v4_work_orders(project, plan)

            run = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_scrna_missing", force=True)
            node = run["result"]["node_results"][0]

            self.assertEqual(node["status"], "failed")
            self.assertIn("count_matrix", node["reason"])
            self.assertTrue((project / "v4" / "artifact_resolution").exists())

    def test_scrna_work_order_with_real_matrix_and_metadata_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            data = project / "data" / "sc1"
            data.mkdir(parents=True)
            (data / "counts.tsv").write_text(
                "gene_symbol\tc1\tc2\tc3\tc4\nIL6\t1\t2\t8\t9\nCXCL8\t0\t1\t5\t6\n",
                encoding="utf-8",
            )
            (data / "metadata.tsv").write_text(
                "cell_id\tdonor_id\tgroup\tcell_type\nc1\td1\tcontrol\tFAP\nc2\td1\tcontrol\tFAP\nc3\td2\tcase\tFAP\nc4\td2\tcase\tFAP\n",
                encoding="utf-8",
            )
            plan = {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "P_scrna",
                        "module": "scrna_pseudobulk",
                        "dataset_id": "sc1",
                        "inputs": {"count_matrix": "data/sc1/counts.tsv", "metadata": "data/sc1/metadata.tsv"},
                        "parameters": {"cell_type": "FAP", "min_cells_per_donor": 1, "min_donors_per_group": 1},
                        "expected_outputs": ["results/scrna_pseudobulk_sc1/pseudobulk_matrix.tsv"],
                    }
                ],
            }
            orders = compile_v4_work_orders(project, plan)
            resolution = resolve_work_order_inputs(project, orders[0])

            run = submit_orchestrator_run(project, run_type="work_order_dag", idempotency_key="idem_scrna_run", force=True)

            self.assertEqual(resolution["status"], "pass")
            self.assertEqual(run["result"]["status"], "success")
            self.assertTrue((project / "results" / "scrna_pseudobulk_sc1" / "pseudobulk_matrix.tsv").exists())


if __name__ == "__main__":
    unittest.main()
