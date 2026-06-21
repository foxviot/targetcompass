import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.orchestration_graph import (
    build_typed_orchestration_graph,
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
                lambda: {"module_count": 1},
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


def _write_planner_outputs(project: Path) -> None:
    (project / "v4").mkdir(exist_ok=True)
    (project / "analysis_plan.json").write_text(json.dumps({"modules": [{"module_id": "P1"}]}), encoding="utf-8")
    (project / "v4" / "work_orders.json").write_text(json.dumps({"work_orders": []}), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
