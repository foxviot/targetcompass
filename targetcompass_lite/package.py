import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_FILES = [
    "research_interest.md",
    "research_spec.json",
    "analysis_module_registry.json",
    "configs/agent_methods.json",
    "configs/role_models.json",
    "configs/causal_review_rubric.json",
    "configs/knowledge_registry.json",
    "results/agent_trace.json",
    "results/run_status.json",
    "results/review_actions.tsv",
    "results/review_actions.jsonl",
    "results/review_queue.json",
    "results/approval_state.json",
    "results/adapter_audit/adapter_audit.tsv",
    "results/geo_discovery/geo_recommendations.json",
    "results/geo_discovery/geo_recommendations.tsv",
    "results/ideas/idea_batch.json",
    "results/ideas/feasibility_audit.json",
    "results/experiments/experiment_designs.json",
    "candidate_scores.csv",
    "dataset_match_report.csv",
    "eligible_datasets.csv",
    "reports/target_report.html",
    "reports/target_report.docx",
    "reports/target_report_structured.json",
    "v4/object_manifest.json",
    "v4/work_order_dag.json",
    "v4/evidence_snapshot.json",
    "v4/evidence_db_migration.json",
    "v4/evidence_db_snapshot.json",
    "v4/evidence_review_report_index.json",
    "v4/traceability_refresh.json",
    "v4/mcp_resources.json",
    "v4/mcp_tools.json",
    "v4/mcp_policy.json",
    "v4/mcp_policy_decisions.jsonl",
    "v4/mcp_sessions.json",
    "v4/mcp_tokens.json",
    "v4/mcp_external_auth_manifest.json",
    "v4/mcp_external_auth_readiness.json",
    "v4/mcp_client_config.json",
    "v4/mcp_call_audit.jsonl",
    "v4/mcp_call_audit_summary.json",
    "v4/service_boundaries.json",
    "v4/service_runtime.json",
    "v4/service_deployment.json",
    "v4/service_request_audit.jsonl",
    "v4/registry_snapshots.json",
    "v4/kb_snapshot.json",
    "v4/role_runs.json",
    "v4/agent_method_calls/*.json",
    "v4/agent_recovery/*.json",
    "v4/llm_tasks/*.json",
    "v4/llm_call_audit.jsonl",
    "v4/agent_roles.json",
    "v4/typed_orchestration_graph.json",
    "v4/typed_orchestration_last_run.json",
    "v4/orchestrator_runs.json",
    "v4/storage_backend_manifest.json",
    "v4/production_storage_readiness.json",
    "v4/observability_manifest.json",
    "v4/observability_runbook.md",
    "v4/service_topology.json",
    "v4/codex_engineering/engineering_closure.json",
    "v4/codex_engineering/release_gate.json",
    "v4/codex_engineering/sbom_manifest.json",
    "scripts/start_v4_services.ps1",
    "workflows/target_discovery/main.nf",
    "workflows/target_discovery/nextflow.config",
    "workflows/target_discovery/params.schema.json",
    "workflows/target_discovery/container_manifest.json",
    "workflows/target_discovery/Dockerfile.targetcompass-lite",
    "workflows/target_discovery/container_mount_policy.json",
    "workflows/target_discovery/container_build_result.json",
    "workflows/target_discovery/targetcompass-lite.def",
    "workflows/target_discovery/resume_manifest.template.json",
    "workflows/target_discovery/nextflow_execution_plane.json",
    "workflows/target_discovery/nextflow_validation.json",
    "workflows/target_discovery/execution_profile_matrix.json",
    "workflows/target_discovery/resource_policy_validation.json",
    "workflows/target_discovery/tasks.json",
    "workflows/target_discovery/nextflow_run_manifest.json",
]


def export_run_package(project_dir: Path) -> Path:
    out_dir = project_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_path = out_dir / f"{project_dir.name}_run_package_{stamp}.zip"
    manifest = {
        "project": project_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
    }
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for relative in PACKAGE_FILES:
            paths = sorted(project_dir.glob(relative)) if "*" in relative else [project_dir / relative]
            for path in paths:
                if path.exists() and path.is_file():
                    arcname = str(path.relative_to(project_dir)).replace("\\", "/")
                    zf.write(path, arcname)
                    manifest["files"].append(arcname)
        zf.writestr("package_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return package_path
