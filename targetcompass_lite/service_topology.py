import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .service_deployment import build_service_deployment
from .services import SERVICE_ENDPOINTS, SERVICE_IDENTITIES
from .v4 import content_hash, v4_dir


SERVICE_TOPOLOGY_SCHEMA = "v4.service_topology/0.1"


def build_service_topology(project_dir: Path) -> dict[str, Any]:
    deployment = build_service_deployment(project_dir)
    nodes = []
    for service_id, endpoints in SERVICE_ENDPOINTS.items():
        nodes.append(
            {
                "service_id": service_id,
                "process_model": "standalone_http_process" if service_id != "mcp_gateway" else "external_gateway_process",
                "endpoints": endpoints,
                "state_ownership": _state_ownership(service_id),
                "allowed_callers": _allowed_callers(service_id),
                "may_call": SERVICE_IDENTITIES.get(service_id, {}).get("can_call", []),
            }
        )
    edges = []
    for caller, identity in SERVICE_IDENTITIES.items():
        for target in identity.get("can_call", []):
            edges.append({"from": caller, "to": target, "edge_type": "service_contract_call"})
    payload = {
        "schema_version": SERVICE_TOPOLOGY_SCHEMA,
        "project_id": project_dir.name,
        "mode": "local_multi_process_contract",
        "external_entrypoint": "mcp_gateway",
        "deployment_ref": "v4/service_deployment.json",
        "runtime_ref": "v4/service_runtime.json",
        "nodes": nodes,
        "edges": edges,
        "production_contract": {
            "service_to_service_identity": True,
            "request_audit": "v4/service_request_audit.jsonl",
            "mcp_gateway_only_external_tool_entrypoint": True,
            "independent_process_launcher": deployment.get("launcher", {}).get("powershell", ""),
            "production_gap": "Services share the local filesystem and Python package in this build; containerized independent deploy units are still pending.",
        },
        "generated_at": _now(),
    }
    payload["topology_hash"] = content_hash(payload)
    path = service_topology_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def service_topology_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "service_topology.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _state_ownership(service_id: str) -> list[str]:
    return {
        "project_api": ["research_spec.json", "v4/object_manifest.json", "results/review_queue.json"],
        "evidence_service": ["evidence.sqlite", "v4/evidence_db_snapshot.json", "v4/evidence_review_report_index.json"],
        "registry_service": ["configs/knowledge_registry.json", "v4/registry_snapshots.json"],
        "report_service": ["reports/target_report.html", "exports/*.zip", "results/approval_state.json"],
        "agent_service": ["v4/role_runs.json", "v4/llm_tasks/*.json", "v4/typed_orchestration_graph.json"],
        "engineering_service": ["v4/codex_engineering/*.json"],
        "orchestrator_service": ["v4/orchestrator_runs.json", "v4/work_order_dag.json"],
        "mcp_gateway": ["v4/mcp_*.json", "v4/mcp_*.jsonl"],
    }.get(service_id, [])


def _allowed_callers(service_id: str) -> list[str]:
    return sorted(caller for caller, identity in SERVICE_IDENTITIES.items() if service_id in identity.get("can_call", []) or caller == service_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
