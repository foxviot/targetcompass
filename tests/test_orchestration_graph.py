import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.orchestration_graph import (
    build_typed_orchestration_graph,
    run_typed_orchestration,
    validate_role_output_packet,
)
from targetcompass_lite.role_runner import run_role


class OrchestrationGraphTest(unittest.TestCase):
    def test_typed_graph_declares_role_schemas_retry_fallback_and_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_planner_outputs(project)
            _, record = run_role(
                project,
                "planner",
                {"eligible_datasets": "eligible_datasets.csv"},
                lambda: {
                    "project_id": "demo",
                    "analysis_plan_ref": "analysis_plan.json",
                    "work_orders_ref": "v4/work_orders.json",
                    "module_count": 1,
                },
                method_id="local_planner_v0",
            )
            validation = validate_role_output_packet(project, "planner", record)
            self.assertTrue(validation["valid"])

            graph = build_typed_orchestration_graph(project)
            self.assertEqual(graph["schema_version"], "v4.typed_orchestration_graph/0.1")
            self.assertEqual(len(graph["role_schemas"]), 7)
            node_ids = {row["node_id"] for row in graph["nodes"]}
            self.assertIn("role:disease_normalizer", node_ids)
            self.assertIn("role:report_writer", node_ids)
            self.assertIn({"from": "role:dataset_scout", "to": "role:planner", "edge_type": "requires_output"}, graph["edges"])
            planner = next(row for row in graph["nodes"] if row["role_id"] == "planner")
            self.assertTrue(planner["schema_valid"])
            self.assertEqual(planner["retry_policy"]["max_attempts"], 1)
            self.assertEqual(planner["fallback_policy"]["fallback_method"], "local_planner_v0")
            reviewer = next(row for row in graph["nodes"] if row["role_id"] == "method_reviewer")
            self.assertTrue(reviewer["approval_policy"]["must_write_review_items"])
            self.assertTrue((project / "v4" / "typed_orchestration_graph.json").exists())

    def test_no_self_approval_and_reviewer_reviewitem_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            out_dir = project / "v4" / "role_runs"
            out_dir.mkdir(parents=True)
            generator_packet = {
                "role_id": "planner",
                "status": "success",
                "output_refs": ["analysis_plan.json", "v4/work_orders.json"],
                "output_summary": {"module_count": 1},
                "approved_subjects": ["planner"],
            }
            generator_path = out_dir / "planner_output.json"
            generator_path.write_text(json.dumps(generator_packet), encoding="utf-8")
            generator_record = {"role_id": "planner", "output_packet": "v4/role_runs/planner_output.json"}
            errors = validate_role_output_packet(project, "planner", generator_record)["errors"]
            self.assertTrue(any("generator role cannot approve" in err for err in errors))

            reviewer_packet = {
                "role_id": "method_reviewer",
                "status": "success",
                "output_refs": ["results/review_queue.json"],
                "output_summary": {"decision": "approve", "review_items": []},
            }
            reviewer_path = out_dir / "reviewer_output.json"
            reviewer_path.write_text(json.dumps(reviewer_packet), encoding="utf-8")
            reviewer_record = {"role_id": "method_reviewer", "output_packet": "v4/role_runs/reviewer_output.json"}
            errors = validate_role_output_packet(project, "method_reviewer", reviewer_record)["errors"]
            self.assertTrue(any("reviewer role must write ReviewItem" in err for err in errors))

    def test_run_typed_orchestration_executes_roles_in_dependency_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)

            result = run_typed_orchestration(project, actor="unit_test")

            self.assertEqual(result["schema_version"], "v4.typed_orchestration_run/0.1")
            self.assertEqual(result["status"], "success")
            self.assertEqual([row["role_id"] for row in result["attempts"]], [
                "disease_normalizer",
                "dataset_scout",
                "planner",
                "method_reviewer",
                "result_reviewer",
                "causal_reviewer",
                "report_writer",
            ])
            self.assertTrue(all(row["status"] == "success" for row in result["attempts"]))
            self.assertTrue((project / "v4" / "typed_orchestration_last_run.json").exists())
            method_requests = list((project / "v4" / "agent_method_calls").glob("*_request.json"))
            method_results = list((project / "v4" / "agent_method_calls").glob("*_result.json"))
            self.assertGreaterEqual(len(method_requests), 7)
            self.assertGreaterEqual(len(method_results), 7)
            request = json.loads(method_requests[0].read_text(encoding="utf-8"))
            self.assertEqual(request["schema_version"], "v4.agent_method_call/0.1")
            self.assertIn("llm_call_packet", request)
            role_runs = json.loads((project / "v4" / "role_runs.json").read_text(encoding="utf-8"))
            self.assertTrue(all(row["executor_backend"] == "local" for row in role_runs["runs"]))
            graph = build_typed_orchestration_graph(project)
            self.assertTrue(all(row["schema_valid"] for row in graph["nodes"]))

    def test_typed_orchestration_uses_llm_backend_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)

            def fake_urlopen(req, timeout=90):
                payload = json.loads(req.data.decode("utf-8"))
                self.assertEqual(payload["model"], "deepseek-chat")
                return _FakeChatResponse(
                    {
                        "project_id": "demo",
                        "analysis_plan_ref": "analysis_plan.json",
                        "work_orders_ref": "v4/work_orders.json",
                        "module_count": 1,
                    }
                )

            env = {
                "OPENAI_API_KEY": "test-key",
                "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
                "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
                "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
            }
            with patch.dict("os.environ", env, clear=True), patch("targetcompass_lite.llm_gateway.urllib.request.urlopen", side_effect=fake_urlopen):
                result = run_typed_orchestration(project, role_id="planner", force=True, actor="unit_test")

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["attempts"][0]["attempts"][0]["executor_backend"], "local")
            self.assertEqual(result["attempts"][1]["attempts"][0]["executor_backend"], "local")
            self.assertEqual(result["attempts"][2]["attempts"][0]["executor_backend"], "llm")
            role_runs = json.loads((project / "v4" / "role_runs.json").read_text(encoding="utf-8"))["runs"]
            planner_run = next(row for row in role_runs if row["role_id"] == "planner")
            self.assertEqual(planner_run["executor_backend"], "llm")
            output_packet = json.loads((project / planner_run["output_packet"]).read_text(encoding="utf-8"))
            self.assertEqual(output_packet["execution_dispatch"]["executor_backend"], "llm")
            self.assertIn("request", output_packet["execution_dispatch"]["artifacts"])

    def test_partial_run_includes_dependencies_and_skips_valid_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)
            run_typed_orchestration(project, actor="unit_test")

            result = run_typed_orchestration(project, role_id="planner", actor="unit_test")

            self.assertEqual(result["status"], "success")
            self.assertEqual([row["role_id"] for row in result["attempts"]], ["disease_normalizer", "dataset_scout", "planner"])
            self.assertTrue(all(row["status"] == "skipped" for row in result["attempts"]))

    def test_force_rerun_executes_schema_valid_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)
            run_typed_orchestration(project, role_id="planner", actor="unit_test")
            before = json.loads((project / "v4" / "role_runs.json").read_text(encoding="utf-8"))

            result = run_typed_orchestration(project, role_id="planner", force=True, actor="unit_test")
            after = json.loads((project / "v4" / "role_runs.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "success")
            self.assertTrue(all(row["status"] == "success" for row in result["attempts"]))
            self.assertEqual(len(after["runs"]), len(before["runs"]) + 3)

    def test_invalid_dependency_blocks_downstream_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            _write_full_orchestration_inputs(project)
            (project / "results" / "geo_discovery" / "geo_recommendations.json").unlink()

            result = run_typed_orchestration(project, role_id="planner", actor="unit_test")

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["attempts"][1]["role_id"], "dataset_scout")
            self.assertEqual(result["attempts"][1]["status"], "failed")
            self.assertEqual(result["attempts"][2]["role_id"], "planner")
            self.assertEqual(result["attempts"][2]["status"], "blocked")
            self.assertIn("dataset_scout", result["attempts"][2]["dependencies"])
            recovery = project / "v4" / "agent_recovery" / "dataset_scout_last_failure.json"
            self.assertTrue(recovery.exists())
            recovery_payload = json.loads(recovery.read_text(encoding="utf-8"))
            self.assertEqual(recovery_payload["schema_version"], "v4.agent_method_recovery/0.1")
            self.assertEqual(recovery_payload["fallback_method"], "local_dataset_scout_v0")


def _write_planner_outputs(project: Path) -> None:
    (project / "v4").mkdir(exist_ok=True)
    (project / "analysis_plan.json").write_text(json.dumps({"modules": [{"module_id": "P1"}]}), encoding="utf-8")
    (project / "v4" / "work_orders.json").write_text(json.dumps({"work_orders": []}), encoding="utf-8")


def _write_full_orchestration_inputs(project: Path) -> None:
    (project / "v4").mkdir(parents=True, exist_ok=True)
    (project / "results" / "geo_discovery").mkdir(parents=True, exist_ok=True)
    (project / "results" / "causal_evidence").mkdir(parents=True, exist_ok=True)
    (project / "reports").mkdir(parents=True, exist_ok=True)
    (project / "research_spec.json").write_text(
        json.dumps({"project_id": "demo", "research_theme": "vascular aging"}),
        encoding="utf-8",
    )
    (project / "v4" / "disease_spec.json").write_text(
        json.dumps({"project_id": "demo", "canonical": "vascular aging"}),
        encoding="utf-8",
    )
    (project / "dataset_match_report.csv").write_text("dataset_id,score\nGSE1,0.9\n", encoding="utf-8")
    (project / "eligible_datasets.csv").write_text("dataset_id\nGSE1\n", encoding="utf-8")
    (project / "results" / "geo_discovery" / "geo_recommendations.json").write_text(
        json.dumps({"recommendations": [{"dataset_id": "GSE1"}]}),
        encoding="utf-8",
    )
    (project / "analysis_plan.json").write_text(
        json.dumps({"project_id": "demo", "modules": [{"module_id": "bulk_deg"}]}),
        encoding="utf-8",
    )
    (project / "v4" / "work_orders.json").write_text(
        json.dumps({"work_orders": [{"work_order_id": "wo1"}]}),
        encoding="utf-8",
    )
    (project / "results" / "causal_evidence" / "causal_evidence_grades.tsv").write_text(
        "gene\tgrade\nCXCL8\tC2\n",
        encoding="utf-8",
    )
    (project / "reports" / "target_report.html").write_text("<html>demo</html>", encoding="utf-8")
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps({"report_evidence_refs": {"CXCL8": {"evidence_refs": ["ev1"]}}}),
        encoding="utf-8",
    )


class _FakeChatResponse:
    def __init__(self, content: dict):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": json.dumps(self.content)}}]}).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
