import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targetcompass_lite.consistency import run_consistency_check
from targetcompass_lite.evidence_db import build_evidence_db_snapshot, migrate_evidence_db, query_evidence_items
from targetcompass_lite.evidence_index import build_evidence_review_report_index, query_evidence_trace
from targetcompass_lite.registry_snapshots import build_registry_snapshots
from targetcompass_lite.service_boundaries import build_service_boundaries
from targetcompass_lite.service_deployment import build_service_deployment
from targetcompass_lite.services import (
    dispatch_service_request,
    query_service_audit,
    service_runtime_manifest,
)


class ServicesTest(unittest.TestCase):
    def test_service_runtime_manifest_identity_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            runtime = service_runtime_manifest(project)
            self.assertEqual(runtime["mode"], "local_standalone_services")
            self.assertEqual(runtime["external_tool_entrypoint"], "mcp_gateway")
            self.assertEqual(runtime["service_deployment"]["mode"], "multi_process_local_services")
            self.assertEqual(runtime["service_deployment"]["external_entrypoint"]["auth_manifest"], "v4/mcp_external_auth_manifest.json")
            self.assertTrue(runtime["service_deployment"]["production_contract"]["project_level_isolation"])
            service_ids = {row["service_id"] for row in runtime["services"]}
            self.assertIn("project_api", service_ids)
            self.assertIn("orchestrator_service", service_ids)
            self.assertIn("agent_service", service_ids)
            self.assertIn("engineering_service", service_ids)
            self.assertIn("evidence_service", service_ids)
            self.assertIn("registry_service", service_ids)
            self.assertIn("report_service", service_ids)

            result = dispatch_service_request("project_api", "boundaries", project, caller="mcp_gateway")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["result"]["policy"]["mcp_gateway_is_the_only_external_tool_entrypoint"], True)
            readiness = dispatch_service_request("project_api", "mcp_auth_readiness", project, caller="mcp_gateway")["result"]
            self.assertEqual(readiness["schema_version"], "v4.mcp_external_auth_readiness/0.1")
            self.assertTrue((project / "v4" / "mcp_external_auth_readiness.json").exists())

            with self.assertRaises(RuntimeError):
                dispatch_service_request("report_service", "validate", project, caller="registry_service")

            audit = query_service_audit(project)
            self.assertEqual(audit["match_count"], 3)
            statuses = [row["status"] for row in audit["items"]]
            self.assertIn("success", statuses)
            self.assertIn("failed", statuses)
            actions = [row["action"] for row in audit["items"]]
            self.assertIn("mcp_auth_readiness", actions)

    def test_orchestrator_service_contract_unifies_run_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")

            run = dispatch_service_request(
                "orchestrator_service",
                "submit",
                project,
                {"run_type": "status_only", "idempotency_key": "idem_service"},
                caller="mcp_gateway",
            )["result"]
            replay = dispatch_service_request(
                "orchestrator_service",
                "submit",
                project,
                {"run_type": "status_only", "idempotency_key": "idem_service"},
                caller="mcp_gateway",
            )["result"]
            status = dispatch_service_request(
                "orchestrator_service",
                "status",
                project,
                {"orchestrator_run_id": run["orchestrator_run_id"]},
                caller="mcp_gateway",
            )["result"]

            self.assertEqual(run["orchestrator_run_id"], replay["orchestrator_run_id"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(status["status"], "success")
            self.assertIn("orchestrator_runs", status["state_refs"])

            dag_run = dispatch_service_request(
                "orchestrator_service",
                "submit",
                project,
                {"run_type": "work_order_dag", "idempotency_key": "idem_service_dag"},
                caller="mcp_gateway",
            )["result"]
            self.assertEqual(dag_run["result"]["schema_version"], "v4.work_order_dag_run/0.1")
            self.assertTrue(all("executor" in row for row in dag_run["result"]["node_results"]))
            self.assertTrue((project / "v4" / "work_order_dag.json").exists())

    def test_service_mode_matches_monolith_for_core_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            monolith_index = build_evidence_review_report_index(project)
            service_index = dispatch_service_request("evidence_service", "trace_index", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_index["index_id"], monolith_index["index_id"])
            self.assertEqual(service_index["evidence_count"], monolith_index["evidence_count"])

            monolith_query = query_evidence_trace(project, gene="CXCL8")
            service_query = dispatch_service_request(
                "evidence_service",
                "trace_query",
                project,
                {"gene": "CXCL8"},
                caller="mcp_gateway",
            )["result"]
            self.assertEqual(service_query["match_count"], monolith_query["match_count"])
            self.assertEqual(service_query["items"][0]["evidence_id"], monolith_query["items"][0]["evidence_id"])

            monolith_registry = build_registry_snapshots(project)
            service_registry = dispatch_service_request("registry_service", "snapshot", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_registry["snapshot_hash"], monolith_registry["snapshot_hash"])

            monolith_consistency = run_consistency_check(project)
            service_consistency = dispatch_service_request("report_service", "validate", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_consistency["status"], monolith_consistency["status"])
            self.assertEqual(
                [row["check"] for row in service_consistency["checks"]],
                [row["check"] for row in monolith_consistency["checks"]],
            )

    def test_evidence_db_migration_query_snapshot_and_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            migration = migrate_evidence_db(project)
            self.assertEqual(migration["schema_version"], "v4.evidence_db_migration/0.1")
            index_names = {row["name"] for row in migration["indexes"]}
            self.assertIn("idx_evidence_entity_symbol", index_names)
            self.assertIn("idx_evidence_gene_type", index_names)

            query = query_evidence_items(project, gene="CXCL8", evidence_type="qtl_colocalization")
            self.assertEqual(query["schema_version"], "v4.evidence_query/0.1")
            self.assertEqual(query["match_count"], 1)
            self.assertEqual(query["items"][0]["entity_symbol"], "CXCL8")

            snapshot = build_evidence_db_snapshot(project)
            self.assertEqual(snapshot["schema_version"], "v4.evidence_db_snapshot/0.1")
            self.assertEqual(snapshot["row_count"], 1)
            self.assertTrue(snapshot["snapshot_hash"])

            build_evidence_review_report_index(project)
            consistency = run_consistency_check(project)
            check_names = {row["check"]: row for row in consistency["checks"]}
            self.assertEqual(check_names["evidence_snapshot_matches_trace_index"]["status"], "PASS")
            self.assertEqual(check_names["evidence_db_has_required_indexes"]["status"], "PASS")

            service_query = dispatch_service_request(
                "evidence_service",
                "query",
                project,
                {"gene": "CXCL8", "limit": 10},
                caller="mcp_gateway",
            )["result"]
            self.assertEqual(service_query["match_count"], 1)
            service_snapshot = dispatch_service_request("evidence_service", "snapshot", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_snapshot["row_count"], 1)

    def test_agent_engineering_and_registry_actions_are_service_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)
            packet = project / "v4" / "task_packet_wo1.json"
            packet.write_text(json.dumps({"work_order_id": "wo1", "task": "inspect"}), encoding="utf-8")
            (project / "v4" / "work_orders.json").write_text(
                json.dumps({"work_orders": [{"work_order_id": "wo1", "codex_task_packet": "v4/task_packet_wo1.json"}]}),
                encoding="utf-8",
            )

            config = dispatch_service_request("registry_service", "method_config_read", project, caller="mcp_gateway")["result"]
            self.assertEqual(config["config"]["dataset_scout"], "local_dataset_scout_v0")
            updated = dispatch_service_request(
                "registry_service",
                "method_config_update",
                project,
                {"config": {"dataset_scout": "local_dataset_scout_v0"}},
                caller="mcp_gateway",
            )["result"]
            self.assertEqual(updated["config"]["dataset_scout"], "local_dataset_scout_v0")

            adapted = dispatch_service_request("registry_service", "knowledge_adapt_resources", project, caller="mcp_gateway")["result"]
            self.assertEqual(adapted["schema_version"], "v4.knowledge_adaptation/0.1")
            self.assertEqual(adapted["resources"][0]["status"], "adapted")

            runs = dispatch_service_request("agent_service", "role_runs_list", project, caller="mcp_gateway")["result"]
            self.assertEqual(runs["schema_version"], "v4.role_runs/0.1")
            task_packet = dispatch_service_request(
                "engineering_service",
                "codex_task_packet_inspect",
                project,
                {"work_order_id": "wo1"},
                caller="mcp_gateway",
            )["result"]
            self.assertEqual(task_packet["task"], "inspect")

            with patch.dict("os.environ", {}, clear=True):
                llm_task = dispatch_service_request(
                    "agent_service",
                    "llm_task_prepare",
                    project,
                    {"role_id": "result_reviewer", "prompt": "Review TNC evidence", "input_refs": {"report": "reports/target_report_structured.json"}},
                    caller="mcp_gateway",
                )["result"]
            self.assertEqual(llm_task["schema_version"], "v4.llm_task_packet/0.1")
            self.assertEqual(llm_task["execution_mode"], "blocked_missing_api_key")
            self.assertTrue((project / llm_task["path"]).exists())
            llm_audit = dispatch_service_request("agent_service", "llm_audit_query", project, {"role_id": "result_reviewer"}, caller="mcp_gateway")["result"]
            self.assertEqual(llm_audit["match_count"], 1)

    def test_agent_service_executes_llm_task_with_schema_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            def fake_urlopen(req, timeout=90):
                payload = json.loads(req.data.decode("utf-8"))
                self.assertEqual(payload["model"], "deepseek-chat")
                return _FakeChatResponse(
                    {
                        "project_id": "demo",
                        "review_items": [
                            {
                                "review_id": "review_1",
                                "subject_role": "planner",
                                "decision": "needs_review",
                                "reason": "Evidence refs require human review.",
                            }
                        ],
                        "decision": "needs_review",
                    }
                )

            env = {
                "OPENAI_API_KEY": "test-key",
                "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
                "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
                "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
            }
            with patch.dict("os.environ", env, clear=True), patch("targetcompass_lite.llm_gateway.urllib.request.urlopen", side_effect=fake_urlopen):
                execution = dispatch_service_request(
                    "agent_service",
                    "llm_task_execute",
                    project,
                    {"role_id": "result_reviewer", "prompt": "Review current evidence.", "input_refs": {"report": "reports/target_report_structured.json"}},
                    caller="mcp_gateway",
                )["result"]

            self.assertEqual(execution["schema_version"], "v4.llm_task_execution/0.1")
            self.assertEqual(execution["status"], "executed")
            self.assertTrue(execution["schema_validation"]["valid"])
            self.assertTrue((project / execution["artifacts"]["request"]).exists())
            self.assertTrue((project / execution["artifacts"]["response"]).exists())
            self.assertTrue((project / execution["artifacts"]["output"]).exists())
            audit = dispatch_service_request("agent_service", "llm_audit_query", project, {"role_id": "result_reviewer", "status": "executed"}, caller="mcp_gateway")["result"]
            self.assertEqual(audit["match_count"], 1)

    def test_service_deployment_writes_independent_service_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            deployment = build_service_deployment(project, host="127.0.0.1", base_port=8900)

            self.assertEqual(deployment["schema_version"], "v4.service_deployment/0.1")
            self.assertEqual(deployment["external_entrypoint"]["service_id"], "mcp_gateway")
            services = {row["service_id"]: row for row in deployment["services"]}
            self.assertIn("project_api", services)
            self.assertIn("agent_service", services)
            self.assertIn("registry_service", services)
            self.assertEqual(services["project_api"]["port"], 8900)
            self.assertIn("/v1/boundaries", services["project_api"]["endpoints"])
            self.assertTrue((project / "v4" / "service_deployment.json").exists())
            launcher = project / "scripts" / "start_v4_services.ps1"
            self.assertTrue(launcher.exists())
            self.assertIn("service-run", launcher.read_text(encoding="utf-8"))


def _write_project(project: Path) -> None:
    project.mkdir()
    (project / "configs").mkdir()
    (project / "results").mkdir()
    (project / "reports").mkdir()
    (project / "v4").mkdir()
    (project / "research_spec.json").write_text(
        json.dumps({"project_id": "demo", "research_theme": "vascular aging", "disease_scope": {"canonical": "vascular aging"}}),
        encoding="utf-8",
    )
    (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")
    (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
    source = project / "source.tsv"
    source.write_text("gene_symbol\troute\nCXCL8\tsecreted\n", encoding="utf-8")
    (project / "configs" / "knowledge_registry.json").write_text(
        json.dumps(
            [
                {
                    "resource_id": "demo_source",
                    "resource_type": "annotation_table",
                    "source_path": str(source),
                    "adapter": "copy",
                    "status": "registered",
                }
            ]
        ),
        encoding="utf-8",
    )
    _write_evidence_db(project)
    (project / "results" / "review_queue.json").write_text(
        json.dumps({"queue_count": 1, "items": [{"item_type": "causal_grade", "item_id": "CXCL8", "review_status": "pending"}]}),
        encoding="utf-8",
    )
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps(
            {
                "report_evidence_refs": {
                    "CXCL8": {"score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"]}
                },
                "evidence_review_report_index": {"path": "v4/evidence_review_report_index.json", "index_id": ""},
            }
        ),
        encoding="utf-8",
    )
    index = build_evidence_review_report_index(project)
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps(
            {
                "report_evidence_refs": {
                    "CXCL8": {"score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"]}
                },
                "evidence_review_report_index": {"path": "v4/evidence_review_report_index.json", "index_id": index["index_id"]},
            }
        ),
        encoding="utf-8",
    )
    (project / "v4" / "work_order_dag.json").write_text(
        json.dumps({"nodes": [{"work_order_id": "wo1", "outputs": [{"path": "results/x.tsv"}], "evidence_writes": [{"evidence_id": "ev1"}]}]}),
        encoding="utf-8",
    )
    build_service_boundaries(project)


def _write_evidence_db(project: Path) -> None:
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
        (evidence_id, project_id, entity_symbol, evidence_type, source_dataset, artifact_path, run_id, artifact_id, module_version, review_status, created_at)
        VALUES ('ev1', 'demo', 'CXCL8', 'qtl_colocalization', 'genetic_demo', 'results\\genetic_coloc_mr\\genetic_evidence.tsv', 'run_1', 'artifact_1', 'genetic_coloc_mr_v1', 'PENDING', 'now');
        """
    )
    con.commit()
    con.close()


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
