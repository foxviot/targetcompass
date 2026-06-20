import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, v4_dir


SERVICE_BOUNDARY_SCHEMA = "v4.service_boundaries/0.1"


SERVICES = [
    {
        "service_id": "project_api",
        "owns": ["Project", "DiseaseSpec", "ResearchSpec", "AnalysisPlan", "ReviewDecision", "WorkflowRun"],
        "interfaces": ["project.create", "spec.validate", "spec.freeze", "plan.approve", "run.status"],
        "storage": "project_state_store",
        "may_call": ["registry_service", "agent_service", "mcp_gateway"],
    },
    {
        "service_id": "agent_service",
        "owns": ["AgentRole", "RoleRun", "PromptBundle", "ModelCall"],
        "interfaces": ["role.run", "role.inspect", "prompt.resolve"],
        "storage": "agent_audit_store",
        "may_call": ["project_api", "registry_service", "mcp_gateway"],
    },
    {
        "service_id": "registry_service",
        "owns": ["MethodRegistry", "SourceRegistry", "RubricRegistry", "SafetyRegistry", "RegistrySnapshot"],
        "interfaces": ["registry.snapshot", "method.list", "method.config.update", "rubric.read"],
        "storage": "registry_store",
        "may_call": [],
    },
    {
        "service_id": "artifact_service",
        "owns": ["Artifact", "ArtifactManifest", "SignedArtifactUrl", "LineageRef"],
        "interfaces": ["artifact.register", "artifact.read_summary", "artifact.export"],
        "storage": "object_store",
        "may_call": ["project_api"],
    },
    {
        "service_id": "evidence_service",
        "owns": ["EvidenceItem", "EvidenceSnapshot", "CandidateTarget", "TargetScore", "EvidenceTraceIndex"],
        "interfaces": ["evidence.import", "evidence.query", "evidence.snapshot", "score.compute", "trace.query"],
        "storage": "evidence_db",
        "may_call": ["artifact_service", "registry_service"],
    },
    {
        "service_id": "report_service",
        "owns": ["Report", "ReportRef", "ExportPackage", "Signoff"],
        "interfaces": ["report.build", "report.validate_refs", "package.export", "signoff.record"],
        "storage": "report_store",
        "may_call": ["evidence_service", "artifact_service", "project_api"],
    },
    {
        "service_id": "mcp_gateway",
        "owns": ["McpResource", "McpTool", "McpSession", "ToolCallAudit", "PolicyDecision"],
        "interfaces": ["resources.list", "resources.read", "tools.list", "tools.call"],
        "storage": "mcp_audit_store",
        "may_call": ["project_api", "agent_service", "registry_service", "evidence_service", "report_service", "artifact_service"],
    },
]


def build_service_boundaries(project_dir: Path) -> dict[str, Any]:
    payload = {
        "schema_version": SERVICE_BOUNDARY_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deployment_stage": "local_modular_monolith_with_service_contracts",
        "policy": {
            "public_interfaces_accept_business_objects_only": True,
            "no_service_accepts_raw_sql_shell_or_arbitrary_url": True,
            "service_to_service_calls_require_identity": True,
            "mcp_gateway_is_the_only_external_tool_entrypoint": True,
            "artifact_paths_do_not_grant_authorization": True,
        },
        "services": SERVICES,
        "boundary_hash": content_hash(SERVICES),
        "migration_notes": [
            "Current implementation remains a local modular monolith; this manifest defines split boundaries for future service extraction.",
            "Each service owns its objects and exposes only typed interfaces listed here.",
            "MCP Gateway must enforce identity, scope, project binding, and audit before dispatching internal calls.",
        ],
    }
    path = service_boundaries_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def service_boundaries_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "service_boundaries.json"
