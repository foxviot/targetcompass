import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from targetcompass_lite.mcp_gateway import build_mcp_gateway, call_tool, load_call_audit, read_resource
from targetcompass_lite.mcp_policy import load_policy_decisions, parse_token
from targetcompass_lite.services import query_service_audit
from targetcompass_lite.v4 import build_v4_manifest
from targetcompass_lite.webapp import _v4_work_order_panel


class McpGatewayTest(unittest.TestCase):
    def test_gateway_writes_resource_tool_and_audit_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging", encoding="utf-8")
            (project / "research_spec.json").write_text(json.dumps({"project_id": "demo", "disease_scope": {"canonical": "vascular aging"}}), encoding="utf-8")
            (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")

            manifest = build_v4_manifest(project)
            self.assertTrue(manifest["objects"]["mcp_tools"]["exists"])
            gateway = build_mcp_gateway(project)
            self.assertEqual(gateway["tools"]["schema_version"], "v4.mcp_tool_manifest/0.2")
            self.assertEqual(gateway["resources"]["schema_version"], "v4.mcp_resource_manifest/0.3")
            self.assertIn("resource.read", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.index.build", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.db.migrate", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.db.snapshot", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.db.query", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.storage.manifest", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.storage.readiness", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence.trace.query", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("codex.engineering.closure", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("codex.engineering.release_gate", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("codex.engineering.sbom", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("method.registry.list", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("role.runs.list", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("orchestration.graph.build", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("orchestration.run", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("orchestrator.submit", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("orchestrator.status", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("llm.task.prepare", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("llm.task.execute", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("llm.audit.query", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("mcp.auth.readiness", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("observability.manifest", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("service.topology.build", {row["tool_id"] for row in gateway["tools"]["tools"]})
            self.assertIn("evidence://demo/review-report-index/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertIn("mcp-policy://demo/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertIn("service-boundary://demo/latest", {row["uri"] for row in gateway["resources"]["resources"]})
            self.assertTrue((project / "v4" / "mcp_policy.json").exists())
            self.assertTrue((project / "v4" / "service_boundaries.json").exists())

            uri = "project://demo"
            result = read_resource(project, uri, actor="unit_test")
            self.assertEqual(result["uri"], uri)
            audit = load_call_audit(project)
            self.assertEqual(audit[-1]["tool_id"], "resource.read")
            self.assertEqual(audit[-1]["status"], "success")
            self.assertEqual(audit[-1]["role"], "local_admin")
            self.assertTrue((project / "v4" / "mcp_call_audit_summary.json").exists())
            self.assertTrue(load_policy_decisions(project))

            methods = call_tool(project, "method.registry.list", actor="unit_test")
            self.assertIn("dataset_scout", methods["methods"])
            service_audit = query_service_audit(project)
            self.assertTrue(any(row["service_id"] == "registry_service" for row in service_audit["items"]))
            config = call_tool(
                project,
                "method.config.update",
                {"config": {"dataset_scout": "local_dataset_scout_v0"}},
                actor="unit_test",
            )
            self.assertEqual(config["config"]["dataset_scout"], "local_dataset_scout_v0")
            config_read = call_tool(project, "method.config.read", actor="unit_test")
            self.assertEqual(config_read["config"]["dataset_scout"], "local_dataset_scout_v0")
            role_runs = call_tool(project, "role.runs.list", actor="unit_test")
            self.assertEqual(role_runs["schema_version"], "v4.role_runs/0.1")

            html = _v4_work_order_panel(project)
            self.assertIn("Local MCP Gateway", html)
            self.assertIn("resource.read", html)
            self.assertIn("Evidence -> Review -> Report index", html)
            self.assertIn("Search trace", html)

    def test_reader_token_filters_tools_and_denies_registry_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging", encoding="utf-8")
            token = json.dumps(
                {
                    "principal": "reader-agent",
                    "role": "agent_reader",
                    "project": "demo",
                    "scopes": ["resource:read", "tool:read"],
                    "token_id": "tok_reader",
                }
            )

            gateway = build_mcp_gateway(project)
            self.assertIn("method.config.update", {row["tool_id"] for row in gateway["tools"]["tools"]})
            reader_gateway = build_mcp_gateway(project, principal=parse_token(project, token))
            reader_tools = {row["tool_id"] for row in reader_gateway["tools"]["tools"]}
            self.assertIn("evidence.trace.query", reader_tools)
            self.assertIn("evidence.db.query", reader_tools)
            self.assertNotIn("evidence.storage.manifest", reader_tools)
            self.assertNotIn("evidence.db.migrate", reader_tools)
            self.assertIn("method.config.read", reader_tools)
            self.assertNotIn("method.config.update", reader_tools)
            self.assertNotIn("orchestration.run", reader_tools)
            self.assertIn("orchestrator.status", reader_tools)
            self.assertNotIn("orchestrator.submit", reader_tools)
            self.assertIn("llm.audit.query", reader_tools)
            self.assertNotIn("llm.task.prepare", reader_tools)
            self.assertNotIn("llm.task.execute", reader_tools)

            read = read_resource(project, "project://demo", actor="external_client", token=token)
            self.assertEqual(read["uri"], "project://demo")
            with self.assertRaises(PermissionError):
                call_tool(project, "method.config.update", {"config": {"dataset_scout": "x"}}, actor="external_client", token=token)

            audit = load_call_audit(project)
            self.assertEqual(audit[-1]["status"], "failed")
            self.assertEqual(audit[-1]["principal"], "reader-agent")
            decisions = load_policy_decisions(project)
            self.assertFalse(decisions[-1]["allow"])
            self.assertEqual(decisions[-1]["required_scope"], "registry:write")

    def test_call_tool_records_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("demo", encoding="utf-8")
            build_mcp_gateway(project)

            out = call_tool(project, "resource.read", {"uri": "project://demo"}, actor="unit_test")
            self.assertEqual(out["text"], "demo")
            call_tool(project, "v4.build_manifest", actor="unit_test")
            service_audit = query_service_audit(project, service_id="project_api")
            self.assertGreaterEqual(service_audit["match_count"], 1)
            with self.assertRaises(ValueError):
                call_tool(project, "resource.read", {"uri": "missing://demo"}, actor="unit_test")
            audit = load_call_audit(project)
            self.assertEqual(audit[-2]["status"], "success")
            self.assertEqual(audit[-1]["status"], "failed")
            self.assertEqual(audit[-1]["tool_id"], "resource.read")

    def test_remaining_mcp_tools_dispatch_through_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "configs").mkdir()
            (project / "v4").mkdir()
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
            task = project / "v4" / "task_packet_wo1.json"
            task.write_text(json.dumps({"work_order_id": "wo1", "task": "inspect"}), encoding="utf-8")
            (project / "v4" / "work_orders.json").write_text(
                json.dumps({"work_orders": [{"work_order_id": "wo1", "codex_task_packet": "v4/task_packet_wo1.json"}]}),
                encoding="utf-8",
            )
            build_mcp_gateway(project)

            adapted = call_tool(project, "knowledge.adapt_resources", actor="unit_test")
            migration = call_tool(project, "evidence.db.migrate", actor="unit_test")
            snapshot = call_tool(project, "evidence.db.snapshot", actor="unit_test")
            storage = call_tool(project, "evidence.storage.manifest", actor="unit_test")
            storage_readiness = call_tool(project, "evidence.storage.readiness", actor="unit_test")
            backend_stack = call_tool(project, "evidence.local_backends.prepare", actor="unit_test")
            evidence_query = call_tool(project, "evidence.db.query", {"gene": "CXCL8"}, actor="unit_test")
            task_packet = call_tool(project, "codex.task_packet.inspect", {"work_order_id": "wo1"}, actor="unit_test")
            closure = call_tool(project, "codex.engineering.closure", actor="unit_test")
            release_gate = call_tool(project, "codex.engineering.release_gate", actor="unit_test")
            sbom = call_tool(project, "codex.engineering.sbom", actor="unit_test")
            runs = call_tool(project, "role.runs.list", actor="unit_test")
            graph = call_tool(project, "orchestration.graph.build", actor="unit_test")
            orch = call_tool(project, "orchestrator.submit", {"run_type": "status_only", "idempotency_key": "idem_mcp"}, actor="unit_test")
            orch_status = call_tool(project, "orchestrator.status", {"orchestrator_run_id": orch["orchestrator_run_id"]}, actor="unit_test")
            llm_task = call_tool(
                project,
                "llm.task.prepare",
                {"role_id": "result_reviewer", "prompt": "review results", "input_refs": {"scores": "candidate_scores.csv"}},
                actor="unit_test",
            )
            with mock.patch.dict(
                "os.environ",
                {
                    "OPENAI_API_KEY": "test-key",
                    "TARGETCOMPASS_LLM_PROVIDER": "deepseek",
                    "TARGETCOMPASS_LLM_BASE_URL": "https://api.deepseek.com",
                    "TARGETCOMPASS_OPENAI_MODEL": "deepseek-chat",
                },
                clear=True,
            ), mock.patch("targetcompass_lite.llm_gateway.urllib.request.urlopen", return_value=_FakeChatResponse()):
                llm_execution = call_tool(
                    project,
                    "llm.task.execute",
                    {"packet_id": llm_task["packet_id"]},
                    actor="unit_test",
                )
            llm_audit = call_tool(project, "llm.audit.query", {"role_id": "result_reviewer"}, actor="unit_test")
            readiness = call_tool(project, "mcp.auth.readiness", actor="unit_test")
            observability = call_tool(project, "observability.manifest", actor="unit_test")
            topology = call_tool(project, "service.topology.build", actor="unit_test")

            self.assertEqual(adapted["resources"][0]["status"], "adapted")
            self.assertEqual(migration["schema_version"], "v4.evidence_db_migration/0.1")
            self.assertEqual(snapshot["schema_version"], "v4.evidence_db_snapshot/0.1")
            self.assertEqual(storage["schema_version"], "v4.storage_backend_manifest/0.1")
            self.assertEqual(storage_readiness["schema_version"], "v4.production_storage_readiness/0.1")
            self.assertEqual(backend_stack["schema_version"], "v4.local_backend_stack/0.1")
            self.assertEqual(evidence_query["match_count"], 0)
            self.assertEqual(task_packet["task"], "inspect")
            self.assertEqual(closure["schema_version"], "v4.engineering_closure/0.1")
            self.assertEqual(release_gate["schema_version"], "v4.codex_engineering_release_gate/0.1")
            self.assertEqual(sbom["schema_version"], "v4.sbom_manifest/0.1")
            self.assertEqual(runs["schema_version"], "v4.role_runs/0.1")
            self.assertEqual(graph["schema_version"], "v4.typed_orchestration_graph/0.1")
            self.assertEqual(orch["schema_version"], "v4.orchestrator_run/0.1")
            self.assertEqual(orch_status["schema_version"], "v4.orchestrator_status/0.1")
            self.assertEqual(llm_task["schema_version"], "v4.llm_task_packet/0.1")
            self.assertEqual(llm_execution["schema_version"], "v4.llm_task_execution/0.1")
            self.assertEqual(llm_execution["status"], "executed")
            self.assertEqual(llm_audit["match_count"], 2)
            self.assertEqual(readiness["schema_version"], "v4.mcp_external_auth_readiness/0.1")
            self.assertEqual(observability["schema_version"], "v4.observability_manifest/0.1")
            self.assertEqual(topology["schema_version"], "v4.service_topology/0.1")
            self.assertTrue((project / "v4" / "mcp_external_auth_readiness.json").exists())
            service_audit = query_service_audit(project)
            services = {row["service_id"] for row in service_audit["items"]}
            self.assertIn("registry_service", services)
            self.assertIn("engineering_service", services)
            self.assertIn("agent_service", services)
            self.assertIn("orchestrator_service", services)
            self.assertIn("project_api", services)

class _FakeChatResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "project_id": "demo",
                                    "review_items": [
                                        {
                                            "review_id": "review_mcp",
                                            "subject_role": "planner",
                                            "decision": "needs_review",
                                            "reason": "MCP execution test.",
                                        }
                                    ],
                                    "decision": "needs_review",
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
