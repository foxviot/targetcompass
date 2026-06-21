import tempfile
import unittest
from pathlib import Path
import subprocess

from targetcompass_lite.nextflow_plane import build_nextflow_execution_plane, validate_nextflow_execution_plane
from targetcompass_lite.nextflow_runner import build_nextflow_tasks, run_nextflow_local
from targetcompass_lite.container_plane import build_container_mount_policy, build_docker_image, inspect_image_digest, resolve_docker_bin, write_apptainer_recipe
from targetcompass_lite.v4 import compile_v4_work_orders, read_work_order_attempts


class NextflowPlaneTest(unittest.TestCase):
    def test_builds_nextflow_dsl2_execution_plane(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            manifest = build_nextflow_execution_plane(project)
            self.assertEqual(manifest["schema_version"], "v4.nextflow_execution_plane/0.1")
            self.assertIn("local", manifest["profiles"])
            self.assertIn("slurm", manifest["profiles"])
            self.assertGreaterEqual(manifest["module_count"], 5)
            self.assertTrue((project / "workflows" / "target_discovery" / "main.nf").exists())
            self.assertTrue((project / "workflows" / "target_discovery" / "nextflow.config").exists())
            self.assertTrue((project / "workflows" / "target_discovery" / "params.schema.json").exists())
            self.assertTrue((project / "workflows" / "target_discovery" / "Dockerfile.targetcompass-lite").exists())
            self.assertTrue((project / "workflows" / "common" / "modules" / "bulk_deg" / "module_contract.json").exists())
            self.assertIn("production containers must replace", " ".join(manifest["limitations"]))

            main_text = (project / "workflows" / "target_discovery" / "main.nf").read_text(encoding="utf-8")
            self.assertIn("nextflow.enable.dsl=2", main_text)
            self.assertIn("include { BULK_DEG }", main_text)
            config_text = (project / "workflows" / "target_discovery" / "nextflow.config").read_text(encoding="utf-8")
            self.assertIn("profiles", config_text)
            self.assertIn("docker.enabled", config_text)
            container_manifest = (project / "workflows" / "target_discovery" / "container_manifest.json").read_text(encoding="utf-8")
            self.assertIn("docker build", container_manifest)

            validation = validate_nextflow_execution_plane(project)
            self.assertEqual(validation["status"], "pass")
            self.assertTrue((project / "workflows" / "target_discovery" / "nextflow_validation.json").exists())

    def test_builds_tasks_and_runs_nextflow_with_attempt_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            plan = {
                "project_id": "demo",
                "modules": [
                    {
                        "module_id": "P4_bulk_deg_ds1",
                        "module": "bulk_deg",
                        "dataset_id": "ds1",
                        "inputs": {"expression_matrix": "data/matrix.tsv", "metadata": "data/meta.tsv"},
                        "parameters": {"case": "old", "control": "young", "resources": {"cpus": 3, "memory": "6 GB", "time": "3h"}},
                        "expected_outputs": ["results/bulk_deg_ds1/deg_results.tsv"],
                    }
                ],
            }
            compile_v4_work_orders(project, plan)
            tasks = build_nextflow_tasks(project)
            self.assertEqual(tasks["task_count"], 1)
            self.assertEqual(tasks["tasks"][0]["module_id"], "bulk_deg_v1")
            self.assertEqual(tasks["tasks"][0]["resources"]["cpus"], 3)
            self.assertTrue((project / "workflows" / "target_discovery" / "tasks.json").exists())

            def fake_runner(command, cwd):
                self.assertIn("nextflow", command[0])
                self.assertIn("-profile", command)
                self.assertIn("local", command)
                self.assertIn("-resume", command)
                report = Path(command[command.index("-with-report") + 1])
                timeline = Path(command[command.index("-with-timeline") + 1])
                trace = Path(command[command.index("-with-trace") + 1])
                dag = Path(command[command.index("-with-dag") + 1])
                for path, text in [(report, "<html>report</html>"), (timeline, "<html>timeline</html>"), (trace, "trace"), (dag, "<html>dag</html>")]:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                (cwd / ".nextflow.log").write_text("nextflow log", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            manifest = run_nextflow_local(project, resume=True, runner=fake_runner)
            self.assertEqual(manifest["status"], "success")
            self.assertTrue(manifest["resume"])
            self.assertIn("workflows/target_discovery/runs/", manifest["artifacts"][0])
            attempts = read_work_order_attempts(project)["attempts"]
            self.assertEqual(attempts[-1]["status"], "success")
            self.assertIn("nextflow", attempts[-1]["metadata"])
            self.assertTrue((project / "workflows" / "target_discovery" / "nextflow_run_manifest.json").exists())

    def test_nextflow_failure_extracts_trace_recovery(self):
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
                trace.write_text("task_id\tprocess\tname\tstatus\texit\n1\tBULK_DEG\tBULK_DEG (ds1)\tFAILED\t1\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="process failed")

            manifest = run_nextflow_local(project, runner=failing_runner)
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["recovery"]["failed_tasks"][0]["process"], "BULK_DEG")
            self.assertIn("-resume", manifest["recovery"]["resume_command"])
            attempts = read_work_order_attempts(project)["attempts"]
            self.assertEqual(attempts[-1]["metadata"]["nextflow"]["recovery"]["failed_tasks"][0]["status"], "FAILED")

    def test_nextflow_missing_records_structured_failure(self):
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
            manifest = run_nextflow_local(project, nextflow_bin="definitely_missing_nextflow_binary")
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["returncode"], 127)
            self.assertIn("Nextflow executable not found", manifest["failure_reason"])
            attempts = read_work_order_attempts(project)["attempts"]
            self.assertEqual(attempts[-1]["status"], "failed")
            self.assertIn("Nextflow executable not found", attempts[-1]["failure_reason"])

    def test_container_policy_apptainer_and_fake_docker_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            policy = build_container_mount_policy(project)
            self.assertTrue(policy["policy"]["no_host_root_mount"])
            self.assertTrue((project / "workflows" / "target_discovery" / "container_mount_policy.json").exists())
            recipe = write_apptainer_recipe(project)
            self.assertTrue(recipe.exists())
            self.assertIn("Bootstrap: docker", recipe.read_text(encoding="utf-8"))

            calls = []

            def fake_docker(command, cwd):
                calls.append(command)
                if command[1:3] == ["image", "inspect"]:
                    return subprocess.CompletedProcess(command, 0, stdout='["targetcompass-lite@sha256:abc123"]', stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="built", stderr="")

            result = build_docker_image(
                project,
                image_tag="targetcompass-lite:test",
                docker_bin="docker",
                base_image="python:3.11-bookworm",
                build_args={"HTTP_PROXY": "http://127.0.0.1:7890"},
                network="host",
                runner=fake_docker,
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["base_image"], "python:3.11-bookworm")
            self.assertEqual(result["digest"], "sha256:abc123")
            self.assertEqual(result["immutable_ref"], "targetcompass-lite@sha256:abc123")
            dockerfile = (project / "workflows" / "target_discovery" / "Dockerfile.targetcompass-lite").read_text(encoding="utf-8")
            self.assertIn("ARG TARGETCOMPASS_BASE_IMAGE=python:3.11-bookworm", dockerfile)
            self.assertIn("ARG TARGETCOMPASS_UPGRADE_PIP=false", dockerfile)
            self.assertIn('TARGETCOMPASS_UPGRADE_PIP" = "true"', dockerfile)
            self.assertTrue((project / "workflows" / "target_discovery" / "container_build_result.json").exists())
            digest = inspect_image_digest(project, image_tag="targetcompass-lite:test", docker_bin="docker", runner=fake_docker)
            self.assertEqual(digest["digest"], "sha256:abc123")
            build_call = next(call for call in calls if "build" in call)
            self.assertIn("--network", build_call)
            self.assertIn("host", build_call)
            self.assertIn("TARGETCOMPASS_BASE_IMAGE=python:3.11-bookworm", build_call)
            self.assertIn("HTTP_PROXY=http://127.0.0.1:7890", build_call)
            self.assertIsInstance(resolve_docker_bin("docker"), str)

    def test_docker_build_network_failure_has_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()

            def failing_docker(command, cwd):
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="failed to resolve reference: Method Not Allowed because Docker Desktop has no HTTPS proxy",
                )

            result = build_docker_image(project, docker_bin="docker", runner=failing_docker)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["recovery"]["category"], "base_image_unavailable")
            self.assertTrue(result["recovery"]["recoverable"])
            self.assertTrue(any("proxy" in action.lower() or "base image" in action.lower() for action in result["recovery"]["actions"]))


if __name__ == "__main__":
    unittest.main()
