import subprocess
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import load_artifact_registry
from targetcompass_lite.canonical.nextflow_execution import (
    load_qc_reports,
    load_task_runs,
    run_nextflow_task_packet,
)
from targetcompass_lite.v4 import compile_v4_work_orders


def analysis_packet():
    return {
        "task_id": "analysis_task_bulk_deg",
        "packet_type": "AnalysisTaskPacket",
        "subquestion_id": "sq_sarcopenia",
        "method_name": "bulk_deg",
        "module_id": "bulk_deg_v1",
        "expected_inputs": ["expression_matrix", "metadata"],
        "expected_outputs": ["deg_results.tsv"],
        "qc_requirements": ["nextflow_returncode_0"],
        "failure_conditions": ["nextflow_failed"],
    }


class CanonicalNextflowExecutionTest(unittest.TestCase):
    def test_successful_nextflow_run_writes_taskrun_qc_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
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
                            "parameters": {},
                            "expected_outputs": ["results/bulk_deg_ds1/deg_results.tsv"],
                        }
                    ],
                },
            )

            def fake_runner(command, cwd):
                for flag, text in [
                    ("-with-report", "<html>report</html>"),
                    ("-with-timeline", "<html>timeline</html>"),
                    ("-with-trace", "task_id\tprocess\tname\tstatus\texit\n1\tBULK_DEG\tBULK_DEG\tCOMPLETED\t0\n"),
                    ("-with-dag", "<html>dag</html>"),
                ]:
                    path = Path(command[command.index(flag) + 1])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                (cwd / ".nextflow.log").write_text("nextflow log", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            bundle = run_nextflow_task_packet(project, analysis_packet(), resume=True, runner=fake_runner)
            self.assertEqual(bundle["task_run"]["result_status"], "completed")
            self.assertEqual(bundle["qc_report"]["overall_status"], "pass")
            self.assertGreaterEqual(len(bundle["artifacts"]), 4)
            self.assertTrue((project / "v5" / "nextflow").exists())
            self.assertEqual(len(load_task_runs(project)), 1)
            self.assertEqual(len(load_qc_reports(project)), 1)
            registry = load_artifact_registry(project)
            self.assertGreaterEqual(len(registry), 4)
            self.assertTrue(all(row["checksum_sha256"] for row in registry))

    def test_failed_nextflow_run_writes_failed_taskrun_and_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [{"module_id": "P4_bulk_deg_ds1", "module": "bulk_deg", "dataset_id": "ds1", "inputs": {}, "parameters": {}}],
                },
            )

            def failing_runner(command, cwd):
                trace = Path(command[command.index("-with-trace") + 1])
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.write_text("task_id\tprocess\tname\tstatus\texit\n1\tBULK_DEG\tBULK_DEG\tFAILED\t1\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="process failed")

            bundle = run_nextflow_task_packet(project, analysis_packet(), runner=failing_runner)
            self.assertEqual(bundle["task_run"]["result_status"], "failed")
            self.assertEqual(bundle["qc_report"]["overall_status"], "fail")
            self.assertIn("failed_tasks", bundle["task_run"]["recovery"])

    def test_invalid_packet_is_rejected_before_nextflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            with self.assertRaises(ValueError):
                run_nextflow_task_packet(project, {"task_id": "bad", "packet_type": "ReviewTaskPacket"}, runner=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
