import subprocess
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.artifact_store import load_artifact_store
from targetcompass_lite.canonical.nextflow_production import build_nextflow_module_profiles, run_nextflow_production_validation
from targetcompass_lite.v4 import compile_v4_work_orders


class CanonicalNextflowProductionTest(unittest.TestCase):
    def test_build_nextflow_module_profiles_for_bulk_scrna_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            profiles = build_nextflow_module_profiles(project)

            self.assertEqual(profiles["schema_version"], "v5.nextflow_module_profiles/0.1")
            self.assertIn("bulk_deg", profiles["module_profiles"])
            self.assertIn("scrna_pseudobulk", profiles["module_profiles"])
            self.assertIn("enrichment", profiles["module_profiles"])
            self.assertIn("slurm", profiles["available_profiles"])
            self.assertTrue((project / "v5" / "nextflow" / "module_profiles.json").exists())

    def test_production_validation_records_resume_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)
            calls = {"count": 0}

            def flaky_runner(command, cwd):
                calls["count"] += 1
                trace = Path(command[command.index("-with-trace") + 1])
                trace.parent.mkdir(parents=True, exist_ok=True)
                report = Path(command[command.index("-with-report") + 1])
                report.parent.mkdir(parents=True, exist_ok=True)
                if calls["count"] == 1:
                    trace.write_text("task_id\tprocess\tname\tstatus\texit\n1\tBULK_DEG\tBULK_DEG\tFAILED\t1\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")
                self.assertIn("-resume", command)
                trace.write_text("task_id\tprocess\tname\tstatus\texit\n1\tBULK_DEG\tBULK_DEG\tCOMPLETED\t0\n", encoding="utf-8")
                report.write_text("<html>report</html>", encoding="utf-8")
                Path(command[command.index("-with-timeline") + 1]).write_text("<html>timeline</html>", encoding="utf-8")
                Path(command[command.index("-with-dag") + 1]).write_text("<html>dag</html>", encoding="utf-8")
                (cwd / ".nextflow.log").write_text("ok", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            run = run_nextflow_production_validation(project, _packet(), runner=flaky_runner)

            self.assertEqual(run["status"], "completed")
            self.assertTrue(run["resume_validated"])
            self.assertEqual(calls["count"], 2)
            self.assertTrue((project / "v5" / "nextflow" / "production_validation.json").exists())

    def test_production_validation_registers_run_results_in_artifact_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp)

            def runner(command, cwd):
                outdir = Path(command[command.index("--outdir") + 1])
                outdir.mkdir(parents=True, exist_ok=True)
                (outdir / "deg_results.tsv").write_text("gene\tlogFC\nIL6\t1.2\n", encoding="utf-8")
                Path(command[command.index("-with-trace") + 1]).write_text(
                    "task_id\tprocess\tname\tstatus\texit\n1\tBULK_DEG\tBULK_DEG\tCOMPLETED\t0\n",
                    encoding="utf-8",
                )
                Path(command[command.index("-with-report") + 1]).write_text("<html>report</html>", encoding="utf-8")
                Path(command[command.index("-with-timeline") + 1]).write_text("<html>timeline</html>", encoding="utf-8")
                Path(command[command.index("-with-dag") + 1]).write_text("<html>dag</html>", encoding="utf-8")
                (cwd / ".nextflow.log").write_text("ok", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            run_nextflow_production_validation(project, _packet(), runner=runner)

            registered = {row["relative_path"] for row in load_artifact_store(project)}
            self.assertTrue(any(path.endswith("/results/deg_results.tsv") for path in registered))


def _project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    project.mkdir()
    compile_v4_work_orders(
        project,
        {
            "project_id": "demo",
            "modules": [
                {
                    "module_id": "P4_bulk_deg_ds1",
                    "module": "bulk_deg",
                    "dataset_id": "ds1",
                    "inputs": {"expression_matrix": "data/matrix.tsv", "metadata": "data/meta.tsv"},
                    "parameters": {"resources": {"cpus": 2, "memory": "4 GB", "time": "2h"}},
                    "expected_outputs": ["results/bulk_deg_ds1/deg_results.tsv"],
                }
            ],
        },
    )
    return project


def _packet():
    return {
        "task_id": "analysis_task_bulk_deg",
        "packet_type": "AnalysisTaskPacket",
        "subquestion_id": "sq1",
        "method_name": "bulk_deg",
        "module_id": "bulk_deg_v1",
        "expected_inputs": ["expression_matrix", "metadata"],
        "expected_outputs": ["deg_results.tsv"],
        "qc_requirements": ["nextflow_returncode_0"],
        "failure_conditions": ["nextflow_failed"],
    }


if __name__ == "__main__":
    unittest.main()
