import json
import socket
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.screening import screen_project
from targetcompass_lite.v4 import compile_v4_work_orders
from targetcompass_lite.evidence_db import build_evidence_db_snapshot, migrate_evidence_db, query_evidence_items
from targetcompass_lite.webapp import (
    _codex_task_queue_panel,
    _dataset_controls,
    _evidence_db_audit_panel,
    _evidence_trace_detail_page,
    _find_available_port,
    _mcp_gateway_panel,
    _orchestration_graph_panel,
    _qc_review_detail_page,
    _run_status,
    _v4_role_method_fields,
    _v4_work_order_panel,
    _work_order_dag_panel,
    _write_status,
    _v5_access_page,
    _v5_analysis_main_path_page,
    _v5_artifacts_page,
    _v5_audit_page,
    _v5_backend_writes_page,
    _v5_cache_page,
    _v5_evidence_claims_page,
    _v5_pilotdeck_panel,
    _v5_platform_p2_readiness_page,
    _v5_platform_readiness_page,
    _v5_production_readiness_page,
    _v5_product_report_page,
    _v5_projects_page,
    _v5_release_acceptance_page,
    _v5_memory_page,
    _v5_resource_gate_page,
    _v5_services_page,
    _v5_setup_page,
    _v5_storage_page,
    _v5_update_page,
    _v5_wetlab_page,
    _zh_ui_script,
)


CARD = """dataset_id: ds_web
source: local
accession: WEB001
modality: bulk_expression
organism: human
tissue: vascular endothelium
contrast:
  case: aged
  control: young
sample_summary:
  case_n: 3
  control_n: 3
  donor_n: 6
metadata_fields: [sample_id, group]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: data/ds_web/expression_matrix.tsv
  metadata: data/ds_web/metadata.tsv
recommended_use: [bulk_deg]
blocked_use: []
"""


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    (project / "dataset_cards").mkdir(parents=True)
    (project / "data" / "ds_web").mkdir(parents=True)
    (project / "dataset_cards" / "ds_web.yaml").write_text(CARD, encoding="utf-8")
    (project / "data" / "ds_web" / "expression_matrix.tsv").write_text(
        "gene_symbol\tS1\tS2\nIL6\t1\t2\n",
        encoding="utf-8",
    )
    (project / "data" / "ds_web" / "metadata.tsv").write_text(
        "sample_id\tgroup\nS1\tyoung\nS2\taged\n",
        encoding="utf-8",
    )
    return project


class WebAppTest(unittest.TestCase):
    def test_dataset_controls_render_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            html = _dataset_controls(project)
            self.assertIn('name="dataset"', html)
            self.assertIn('value="ds_web"', html)
            self.assertIn("WEB001", html)

    def test_run_status_is_persisted_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            _write_status(project, "failed", "Workflow failed.", "stdout line", "stderr line")
            status = json.loads((project / "results" / "run_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            html = _run_status(project)
            self.assertIn("Workflow failed.", html)
            self.assertIn("stdout line", html)
            self.assertIn("stderr line", html)
            self.assertIn("Stage cards", html)
            self.assertIn("Orchestrator Run API", html)
            self.assertIn("Recovery center", html)
            self.assertIn("Rebuild report", html)
            self.assertIn("run_status.json", html)

    def test_screen_project_can_limit_to_selected_datasets(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            (project / "dataset_cards" / "ds_other.yaml").write_text(CARD.replace("ds_web", "ds_other"), encoding="utf-8")
            rows = screen_project(project, {"ds_web"})
            self.assertEqual([row["dataset_id"] for row in rows], ["ds_web"])

    def test_find_available_port_skips_occupied_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            occupied = int(sock.getsockname()[1])
            chosen = _find_available_port("127.0.0.1", occupied, attempts=3)
            self.assertNotEqual(chosen, occupied)

    def test_find_available_port_accepts_ephemeral_port(self):
        chosen = _find_available_port("127.0.0.1", 0)
        self.assertGreater(chosen, 0)

    def test_v4_work_order_panel_exposes_codex_task_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            compile_v4_work_orders(
                project,
                {
                    "project_id": "demo",
                    "modules": [
                        {
                            "module_id": "P9_new_adapter_x",
                            "module": "new_external_adapter",
                            "dataset_id": "external_x",
                            "inputs": {},
                            "parameters": {},
                            "expected_outputs": ["results/external_x/normalized.tsv"],
                            "qc_checks": ["schema validated"],
                            "allowed_files": ["targetcompass_lite/db_adapters/**"],
                        }
                    ],
                },
            )
            html = _v4_work_order_panel(project)
            self.assertIn("BUILD_ADAPTER", html)
            self.assertIn("Codex task packet", html)
            self.assertIn('name="item_type" value="work_order"', html)
            self.assertIn('name="item_type" value="codex_task"', html)

    def test_evidence_trace_detail_page_shows_trace_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v4").mkdir()
            (project / "v4" / "evidence_review_report_index.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "evidence_id": "ev1",
                                "entity_symbol": "CXCL8",
                                "evidence_type": "qtl_colocalization",
                                "source_dataset": "genetic_demo",
                                "artifact_path": "results/genetic_coloc_mr/genetic_evidence.tsv",
                                "review_status": "PENDING",
                                "review_items": [{"source": "queue", "item_type": "causal_grade", "item_id": "CXCL8", "review_status": "pending", "reason": "review", "report_ref": "reports/target_report.html#causal-grade-cxcl8"}],
                                "report_refs": [{"gene": "CXCL8", "score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"], "report_ref": "reports/target_report.html#evidence-cxcl8"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "work_order_dag.json").write_text(json.dumps({"nodes": [{"work_order_id": "wo1", "module_id": "P4", "module": "bulk_deg", "status": "success", "outputs": [{"path": "results/genetic_coloc_mr/genetic_evidence.tsv"}], "evidence_writes": [{"evidence_id": "ev1"}]}]}), encoding="utf-8")
            html = _evidence_trace_detail_page(project, evidence_id="ev1").decode("utf-8")
            for text in ["EvidenceItem", "ReviewItem", "ReportRef", "WorkOrder / DAG node", "Artifact"]:
                self.assertIn(text, html)

    def test_evidence_db_audit_panel_shows_query_snapshot_and_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            migrate_evidence_db(project)
            import sqlite3

            con = sqlite3.connect(project / "evidence.sqlite")
            con.execute(
                """
                INSERT INTO evidence_item
                (evidence_id, project_id, entity_symbol, evidence_type, source_dataset, review_status, quality_score, artifact_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("ev1", "demo", "CXCL8", "bulk_deg", "GSE_TEST", "PENDING", 0.82, "results/bulk.tsv", "2026-01-01T00:00:00Z"),
            )
            con.commit()
            con.close()
            build_evidence_db_snapshot(project)
            query = query_evidence_items(project, gene="CXCL8", evidence_type="bulk_deg")
            (project / "v4" / "evidence_db_last_query.json").write_text(json.dumps(query, indent=2), encoding="utf-8")
            html = _evidence_db_audit_panel(project)
            for text in ["Evidence DB production audit", "idx_evidence_entity_symbol", "CXCL8", "GSE_TEST", "Run migration", "Build snapshot"]:
                self.assertIn(text, html)

    def test_work_order_dag_panel_shows_executor_dispatch_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v4").mkdir()
            (project / "v4" / "work_order_dag.json").write_text(
                json.dumps(
                    {
                        "node_count": 1,
                        "edge_count": 0,
                        "status_summary": {"failed": 1},
                        "nodes": [
                            {
                                "node_id": "wo1",
                                "work_order_id": "wo1",
                                "module_id": "P4_bulk",
                                "module": "bulk_deg",
                                "status": "failed",
                                "outputs": [{"path": "results/bulk/deg.tsv"}],
                                "evidence_writes": [],
                                "dependencies": [],
                                "resume_key": "resume_123",
                                "input_resolution_ref": "v4/artifact_resolution/wo1.json",
                                "input_resolution": {
                                    "status": "failed",
                                    "resolved": [
                                        {"key": "metadata", "declared": "data/GSE/metadata.tsv", "status": "missing", "source": "declared_path", "path": ""},
                                        {"key": "expression_matrix", "declared": "data/GSE/expression.tsv", "status": "available", "source": "declared_path", "path": "data/GSE/expression.tsv"},
                                    ],
                                    "missing": [
                                        {"key": "metadata", "declared": "data/GSE/metadata.tsv", "status": "missing", "source": "declared_path", "path": ""}
                                    ],
                                    "recovery": [
                                        {"type": "provide_input", "message": "Upload metadata.tsv with sample group columns."}
                                    ],
                                },
                                "latest_attempt": {"failure_reason": "input missing"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "orchestrator_runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "result": {
                                    "node_results": [
                                        {
                                            "node_id": "wo1",
                                            "status": "failed",
                                            "resume_key": "resume_123",
                                            "reason": "input missing",
                                            "recovery": {"suggested_action": "fix metadata and rerun"},
                                            "executor": {
                                                "backend": "local_executor",
                                                "module_id": "P4_bulk",
                                                "executor_manifest": "results/executor/P4_bulk/executor_manifest.json",
                                                "artifacts": ["results/executor/P4_bulk/executor_manifest.json"],
                                                "failure_reason": "input missing",
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            html = _work_order_dag_panel(project)
            for text in [
                "Executor dispatch details",
                "Input parsing and recovery advice",
                "Missing inputs",
                "metadata",
                "data/GSE/metadata.tsv",
                "Upload metadata.tsv with sample group columns.",
                "Retry this node",
                "local_executor",
                "executor_manifest.json",
                "fix metadata and rerun",
                "resume_123",
            ]:
                self.assertIn(text, html)

    def test_v5_pilotdeck_panel_shows_execution_storage_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v5" / "task_runs").mkdir(parents=True)
            (project / "v5" / "qc_reports").mkdir(parents=True)
            (project / "v5" / "codex" / "approved").mkdir(parents=True)
            (project / "v4").mkdir(parents=True)
            (project / "v5" / "project_state.json").write_text(
                json.dumps({"current_stage": "TASKS_READY"}),
                encoding="utf-8",
            )
            (project / "v5" / "events.jsonl").write_text(
                json.dumps({"event_type": "TASKS_READY"}) + "\n",
                encoding="utf-8",
            )
            (project / "v5" / "task_runs" / "tr1.json").write_text(
                json.dumps(
                    {
                        "task_run_id": "tr1",
                        "task_id": "analysis_task_1",
                        "executor": "nextflow",
                        "result_status": "completed",
                        "qc_report_ref": "qc1",
                        "artifact_refs": ["artifact1"],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "qc_reports" / "qc1.json").write_text(
                json.dumps(
                    {
                        "qc_report_id": "qc1",
                        "task_id": "analysis_task_1",
                        "overall_status": "pass",
                        "checks": [{"check_id": "nextflow_returncode", "status": "pass"}],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "artifact_registry.jsonl").write_text(
                json.dumps(
                    {
                        "artifact_id": "artifact1",
                        "artifact_type": "nextflow_report",
                        "path": "workflows/target_discovery/runs/a/report.html",
                        "exists": True,
                        "qc_status": "pass",
                        "checksum_sha256": "abc123",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (project / "v5" / "local_execution").mkdir(parents=True)
            (project / "v5" / "local_execution" / "local_execution_bundle.json").write_text(
                json.dumps({"status": "completed", "task_count": 2, "completed_count": 2, "failed_count": 0, "post_analysis": {"status": "completed"}}),
                encoding="utf-8",
            )
            (project / "v5" / "reports").mkdir(parents=True)
            (project / "v5" / "reports" / "canonical_report_manifest.json").write_text(
                json.dumps({"human_review_gate": {"required": True, "reason": "alignment decision is needs_review"}}),
                encoding="utf-8",
            )
            (project / "v5" / "codex" / "approved" / "eng1.json").write_text(
                json.dumps({"task_id": "eng1", "status": "approved"}),
                encoding="utf-8",
            )
            (project / "v4" / "local_backend_check.json").write_text(
                json.dumps(
                    {
                        "active_backends": {"evidence_db": "postgres_local", "object_store": "minio_local"},
                        "postgres": {"status": "PASS"},
                        "minio": {"status": "PASS"},
                        "checks": [{"check_id": "postgres_live", "status": "PASS", "message": "PostgreSQL accepted a live query."}],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "production_storage_readiness.json").write_text(
                json.dumps({"status": "READY"}),
                encoding="utf-8",
            )
            (project / "v4" / "mcp_policy.json").write_text(
                json.dumps({"roles": {"reviewer": ["resource:read", "review:write"]}}),
                encoding="utf-8",
            )
            html = _v5_pilotdeck_panel(project)
            for text in [
                "v5 PilotDeck control plane",
                "Run v5 local full workflow",
                "Analysis main path",
                "Product report",
                "Projects",
                "Access",
                "Backend writes",
                "Artifacts",
                "Evidence / claims",
                "Wet-lab signoff",
                "Local registered-module execution",
                "Canonical agent workflow",
                "question_normalizer",
                "TASKS_READY",
                "analysis_task_1",
                "nextflow",
                "nextflow_returncode",
                "artifact1",
                "eng1",
                "postgres_local",
                "minio_local",
                "local-backends-check",
                "reviewer",
            ]:
                self.assertIn(text, html)

    def test_v5_platform_pages_render_projects_access_and_backend_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            from targetcompass_lite.canonical.access_control import issue_access_token, set_project_member
            from targetcompass_lite.canonical.backend_writer import write_json_artifact

            set_project_member(project, "reviewer1", "reviewer")
            issue_access_token(project, "reviewer1", ttl_minutes=30, scopes=["project:read"])
            write_json_artifact(project, "v5/demo/object.json", {"ok": True}, producer="unit", artifact_type="json")

            projects_html = _v5_projects_page(project).decode("utf-8")
            setup_html = _v5_setup_page(project).decode("utf-8")
            services_html = _v5_services_page(project).decode("utf-8")
            storage_html = _v5_storage_page(project).decode("utf-8")
            audit_html = _v5_audit_page(project).decode("utf-8")
            cache_html = _v5_cache_page(project).decode("utf-8")
            update_html = _v5_update_page(project).decode("utf-8")
            access_html = _v5_access_page(project).decode("utf-8")
            backend_html = _v5_backend_writes_page(project).decode("utf-8")
            p1_html = _v5_platform_readiness_page(project).decode("utf-8")
            p2_html = _v5_platform_p2_readiness_page(project).decode("utf-8")
            production_html = _v5_production_readiness_page(project).decode("utf-8")
            release_html = _v5_release_acceptance_page(project).decode("utf-8")
            memory_html = _v5_memory_page(project).decode("utf-8")

            for text in ["Projects", "demo", "Create project", "Import project"]:
                self.assertIn(text, projects_html)
            for text in ["Setup Wizard", "LLM provider", "API key", "Docker backend"]:
                self.assertIn(text, setup_html)
            for text in ["Service Manager", "Start / restart command", "Activate backends"]:
                self.assertIn(text, services_html)
            for text in ["Production Storage", "Legacy writer migration coverage", "Storage migration plan", "Evidence DB", "Professor demo slim storage", "Build demo slim storage"]:
                self.assertIn(text, storage_html)
            for text in ["Platform Audit", "Search audit", "access"]:
                self.assertIn(text, audit_html)
            for text in ["Data Cache", "Cache roots", "Cleanup policy"]:
                self.assertIn(text, cache_html)
            for text in ["Update", "preserved", "update_manifest.json"]:
                self.assertIn(text, update_html)
            for text in ["Access Control", "reviewer1", "Token lifecycle", "Access audit", "Role permissions", "Admin actions required"]:
                self.assertIn(text, access_html)
            for text in ["Backend Write Details", "v5/demo/object.json", "ArtifactStore records"]:
                self.assertIn(text, backend_html)
            for text in ["P1 Platform Readiness", "project_lifecycle", "storage_primary_path"]:
                self.assertIn(text, p1_html)
            for text in ["P2 Platform Readiness", "multi_user_permissions", "postgres_minio_primary_path", "wet_lab_protocol_signoff"]:
                self.assertIn(text, p2_html)
            for text in ["v5 Production Readiness", "formal_auth_oidc_vault_sessions", "codex_worker_large_sample_validation", "online_question_longrun_validation"]:
                self.assertIn(text, production_html)
            for text in ["Release Acceptance", "quick_regression", "full_regression", "real_question_longrun", "clean_windows_installer_smoke"]:
                self.assertIn(text, release_html)
            for text in ["长期 Memory 审计", "版本列表", "审计事件", "科学证据边界"]:
                self.assertIn(text, memory_html)

    def test_chinese_ui_script_does_not_corrupt_targetcompass_filename(self):
        script = _zh_ui_script()
        self.assertNotIn("replaceAll('pass'", script)
        self.assertNotIn("replaceAll('clear'", script)
        self.assertNotIn("replaceAll('to '", script)
        self.assertIn("const map =", script)
        self.assertIn("发布前验收", script)

    def test_v5_artifact_and_claim_drilldown_pages_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            from targetcompass_lite.artifact_store import put_artifact
            from targetcompass_lite.canonical.artifacts import register_artifact

            artifact = project / "reports" / "target_report.html"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("<html>report</html>", encoding="utf-8")
            put_artifact(project, "reports/target_report.html", producer="report", artifact_type="html_report")
            register_artifact(project, "reports/target_report.html", "report", "html_report", ["task_report"], ["sq1"])
            (project / "v4").mkdir(exist_ok=True)
            (project / "v4" / "evidence_db_last_query.json").write_text(
                json.dumps({"items": [{"evidence_id": "ev1", "entity_symbol": "IL6", "evidence_type": "bulk_deg", "review_status": "PENDING"}]}),
                encoding="utf-8",
            )
            (project / "v5" / "alignment").mkdir(parents=True)
            (project / "v5" / "alignment" / "question_alignment_report.json").write_text(
                json.dumps({"final_decision": "needs_review", "unsupported_claims": [{"claim_id": "claim1"}], "claim_ceiling_violations": []}),
                encoding="utf-8",
            )

            artifact_html = _v5_artifacts_page(project, selected_path="reports/target_report.html").decode("utf-8")
            claim_html = _v5_evidence_claims_page(project).decode("utf-8")

            for text in ["Artifact Drill-down", "target_report.html", "Selected artifact verification", "local_cache"]:
                self.assertIn(text, artifact_html)
            for text in ["Evidence / Claim Drill-down", "IL6", "Claim alignment reports", "claim1"]:
                self.assertIn(text, claim_html)

    def test_v5_analysis_main_path_and_product_report_pages_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            (project / "v5" / "analysis_main_path").mkdir(parents=True)
            (project / "v5" / "analysis_main_path" / "main_path_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "selected_dataset": {"accession": "GSE_TEST", "selection_mode": "explicit_cli"},
                        "task_packet_count": 0,
                        "stages": [{"stage": "dataset_lock", "status": "blocked", "message": "needs metadata"}],
                        "recovery": [{"category": "dataset_lock", "severity": "high", "reason": "needs metadata", "recovery_actions": ["fix metadata"]}],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "reports").mkdir(parents=True)
            (project / "v5" / "reports" / "product_report_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "candidate_review_required",
                        "candidate_count": 1,
                        "top_candidates": [{"rank": 1, "gene": "IL6", "route": "secreted", "final_score": "95", "tier": "A", "covered_axes": ["SASP_annotation"], "missing_axes": ["causal_or_genetic_support"]}],
                        "evidence_chain": {"artifact_count": 2, "human_review_gate": {"required": True}},
                        "limitations": ["human review required"],
                    }
                ),
                encoding="utf-8",
            )

            analysis_html = _v5_analysis_main_path_page(project).decode("utf-8")
            product_html = _v5_product_report_page(project).decode("utf-8")

            for text in ["v5 Analysis Main Path", "GSE_TEST", "dataset_lock", "fix metadata"]:
                self.assertIn(text, analysis_html)
            for text in ["v5 Product Report", "IL6", "causal_or_genetic_support", "human review required"]:
                self.assertIn(text, product_html)

    def test_v5_resource_gate_lockable_dataset_exposes_analysis_run_button(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            (project / "v5" / "resource_discovery").mkdir(parents=True)
            (project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").write_text(
                json.dumps(
                    {
                        "resource_candidates": [
                            {
                                "resource_candidate_id": "rc1",
                                "resource_type": "dataset",
                                "source_database": "geo",
                                "accession": "GSE_LOCK",
                                "verified": True,
                                "source_status": "metadata_verified",
                            }
                        ],
                        "dataset_profiles": [
                            {
                                "resource_candidate_id": "rc1",
                                "dataset_profile_id": "dp1",
                                "modality": "bulk_expression",
                                "group_metadata_status": "not_assessed",
                                "sample_size_status": "not_assessed",
                                "organism": "unknown",
                                "tissue": "unknown",
                                "platform": "unknown",
                            }
                        ],
                        "dataset_selection_decisions": [],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v5" / "resource_discovery" / "resource_manual_corrections.jsonl").write_text(
                json.dumps(
                    {
                        "resource_candidate_id": "rc1",
                        "group_metadata_status": "case_control_selected",
                        "sample_size_status": "sufficient",
                        "group_column": "condition",
                        "case_label": "case",
                        "control_label": "control",
                        "organism": "human",
                        "tissue": "skeletal muscle",
                        "platform": "GPLTEST",
                        "sample_count": "4",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            data_dir = project / "data" / "GSE_LOCK"
            data_dir.mkdir(parents=True)
            (data_dir / "expression_matrix.tsv").write_text("gene_symbol\tS1\tS2\nIL6\t1\t2\n", encoding="utf-8")
            (data_dir / "metadata.tsv").write_text("sample_id\tcondition\nS1\tcase\nS2\tcontrol\n", encoding="utf-8")

            html = _v5_resource_gate_page(project).decode("utf-8")

            self.assertIn("datasets_locked_ready", html)
            self.assertIn("运行锁库分析", html)
            self.assertIn("GSE_LOCK", html)

    def test_v5_wetlab_page_renders_protocol_signoff_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            (project / "candidate_scores.csv").write_text("gene,total_score,route\nIL6,0.9,secreted\n", encoding="utf-8")

            page = _v5_wetlab_page(project).decode("utf-8")

            for text in ["Wet-lab Protocol Signoff", "IL6", "Sign off", "needs_revision", "Protocol drafts"]:
                self.assertIn(text, page)

    def test_codex_task_queue_panel_shows_queue_and_registry_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "v4").mkdir()
            (project / "v4" / "codex_task_queue.json").write_text(
                json.dumps(
                    {
                        "schema_version": "v0.1.codex_task_queue",
                        "project_id": "demo",
                        "task_count": 1,
                        "status_summary": {"succeeded": 1},
                        "tasks": [
                            {
                                "task_id": "ctp_bulk",
                                "module_id": "ED_bulk",
                                "task_kind": "analysis_execution",
                                "status": "succeeded",
                                "work_order_id": "wo1",
                                "claim": {"worker_id": "ui_codex_worker"},
                                "refs": {"result": "v4/codex_task_queue_results.json#cqresult_1"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "task_registry.json").write_text(
                json.dumps({"status_summary": {"succeeded": 1}}),
                encoding="utf-8",
            )
            (project / "v4" / "codex_task_queue_results.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "result_record_id": "cqresult_1",
                                "task_id": "ctp_bulk",
                                "status": "success",
                                "orchestrator_run_id": "orun_1",
                                "artifacts": ["results/qc/qc1.json"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "codex_task_queue_tests.json").write_text(
                json.dumps({"tests": [{"test_record_id": "cqtest_1", "task_id": "ctp_bulk", "command": "four_layer_qc", "status": "passed"}]}),
                encoding="utf-8",
            )
            (project / "v4" / "codex_task_queue_patches.json").write_text(
                json.dumps({"patches": [{"patch_record_id": "cqpatch_1", "task_id": "ctp_bulk", "status": "not_applicable", "summary": "Analysis packet executed."}]}),
                encoding="utf-8",
            )
            html = _codex_task_queue_panel(project)
            for text in [
                "Codex Task Queue",
                "QC review gate",
                "Sync packets",
                "Run next task",
                "Execute selected task",
                "ED_bulk",
                "analysis_execution",
                "cqresult_1",
                "four_layer_qc",
                "not_applicable",
                "Task Registry",
            ]:
                self.assertIn(text, html)

    def test_qc_review_detail_page_shows_layer_and_batch_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "v4").mkdir(parents=True)
            (project / "results" / "qc").mkdir(parents=True)
            (project / "v4" / "task_registry.json").write_text(
                json.dumps(
                    {
                        "task_count": 1,
                        "status_summary": {"qc_review_required": 1},
                        "tasks": [
                            {
                                "task_id": "ctp_bulk",
                                "work_order_id": "wo_bulk",
                                "module_id": "ED_bulk",
                                "dataset_id": "ds",
                                "status": "qc_review_required",
                                "refs": {"qc_report": "results/qc/qc_bulk.json", "queue_result": "v4/codex_task_queue_results.json#r1"},
                                "qc_gate": {"decision": "Evidence can be imported only as QC_REVIEW_REQUIRED.", "reason": "statistical review", "evidence_import": "QC_REVIEW_REQUIRED"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project / "results" / "qc" / "qc_bulk.json").write_text(
                json.dumps(
                    {
                        "overall_status": "review",
                        "layers": [
                            {"layer": "Execution", "status": "pass", "messages": ["ran"]},
                            {"layer": "Statistical", "status": "review", "warnings": ["check dispersion"]},
                        ],
                        "artifacts": ["results/bulk_deg_ds/deg_results.tsv"],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "qc_review_queue.json").write_text(
                json.dumps(
                    {
                        "queue_count": 1,
                        "items": [
                            {
                                "item_id": "wo_bulk",
                                "task_id": "ctp_bulk",
                                "module_id": "ED_bulk",
                                "dataset_id": "ds",
                                "qc_report": "results/qc/qc_bulk.json",
                                "evidence_import": "QC_REVIEW_REQUIRED",
                                "reason": "statistical review",
                                "evidence_summary": {"match_count": 1, "by_review_status": {"QC_REVIEW_REQUIRED": 1}},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            html = _qc_review_detail_page(project, "wo_bulk")
            self.assertIn("QC Review Detail", html.decode("utf-8"))
            self.assertIn("Four-layer QC", html.decode("utf-8"))
            self.assertIn("Statistical", html.decode("utf-8"))
            self.assertIn("Approve QC", html.decode("utf-8"))

    def test_agent_dag_panel_shows_role_backend_artifacts_and_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "configs").mkdir()
            (project / "v4").mkdir()
            (project / "configs" / "role_execution_backends.json").write_text(
                json.dumps({"planner": "llm"}),
                encoding="utf-8",
            )
            fields = _v4_role_method_fields(project)
            self.assertIn('name="backend__planner"', fields)
            self.assertIn('<option value="llm" selected>llm</option>', fields)

            (project / "v4" / "typed_orchestration_graph.json").write_text(
                json.dumps(
                    {
                        "graph_hash": "abc123456789",
                        "role_schemas": {"planner": {}},
                        "edges": [],
                        "nodes": [
                            {
                                "role_id": "planner",
                                "schema": "PlannerOutput",
                                "status": "success",
                                "schema_valid": True,
                                "selected_method": "local_planner_v0",
                                "selected_model": "deepseek-chat",
                                "fallback_policy": {"fallback_method": "local_planner_v0"},
                                "schema_errors": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project / "v4" / "role_runs.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "role_id": "planner",
                                "status": "success",
                                "role_run_id": "role_run_1",
                                "executor_backend": "llm",
                                "execution_dispatch": {
                                    "executor_backend": "llm",
                                    "artifacts": {
                                        "request": "v4/llm_tasks/task_request.json",
                                        "response": "v4/llm_tasks/task_response.json",
                                    },
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            html = _orchestration_graph_panel(project)
            for text in ["Configured backend", "Actual backend", "llm", "task_request.json", "local_planner_v0"]:
                self.assertIn(text, html)

    def test_mcp_gateway_panel_shows_external_auth_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
            html = _mcp_gateway_panel(project)
            for text in ["External auth readiness", "Refresh readiness", "project_bound_tokens", "mcp_external_auth_readiness.json"]:
                self.assertIn(text, html)
            self.assertTrue((project / "v4" / "mcp_external_auth_readiness.json").exists())


if __name__ == "__main__":
    unittest.main()
