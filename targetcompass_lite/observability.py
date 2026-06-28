import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mcp_gateway import load_call_audit
from .services import query_service_audit
from .v4 import content_hash, read_json, v4_dir


OBSERVABILITY_SCHEMA = "v4.observability_manifest/0.1"


def build_observability_manifest(project_dir: Path) -> dict[str, Any]:
    service_audit = query_service_audit(project_dir, limit=200)
    mcp_calls = load_call_audit(project_dir)
    orchestrator = read_json(v4_dir(project_dir) / "orchestrator_runs.json", {"runs": []})
    role_runs = read_json(v4_dir(project_dir) / "role_runs.json", {"runs": []})
    payload = {
        "schema_version": OBSERVABILITY_SCHEMA,
        "project_id": project_dir.name,
        "mode": "local_artifact_observability",
        "signals": {
            "traces": {
                "source": "v4/service_request_audit.jsonl",
                "trace_id_field": "trace_id",
                "count": service_audit.get("match_count", 0),
            },
            "mcp_audit": {
                "source": "v4/mcp_call_audit.jsonl",
                "count": len(mcp_calls),
                "failure_count": len([row for row in mcp_calls if row.get("status") == "failed"]),
            },
            "orchestrator_runs": {
                "source": "v4/orchestrator_runs.json",
                "count": len(orchestrator.get("runs", [])),
            },
            "agent_role_runs": {
                "source": "v4/role_runs.json",
                "count": len(role_runs.get("runs", [])),
            },
        },
        "otel_contract": {
            "enabled": False,
            "endpoint_env": "OTEL_EXPORTER_OTLP_ENDPOINT",
            "service_name_env": "OTEL_SERVICE_NAME",
            "required_span_attributes": ["project_id", "service_id", "action", "trace_id", "status"],
            "production_gap": "OpenTelemetry exporter is not wired to a collector in this local build.",
        },
        "prometheus_contract": {
            "enabled": False,
            "scrape_path": "/metrics",
            "required_metrics": ["targetcompass_service_requests_total", "targetcompass_orchestrator_runs_total", "targetcompass_mcp_failures_total"],
            "production_gap": "Prometheus endpoint is not running in this local build.",
        },
        "loki_contract": {
            "enabled": False,
            "log_sources": ["v4/service_request_audit.jsonl", "v4/mcp_call_audit.jsonl", "results/run_status.json"],
            "production_gap": "Loki push/scrape configuration is not attached in this local build.",
        },
        "slo": {
            "service_request_success_rate": ">= 0.99 over 24h",
            "orchestrator_recoverable_failure_rate": "<= 0.05 over 24h",
            "mcp_auth_denial_visibility": "100% denied calls must have policy decision records",
        },
        "runbook": _runbook(project_dir),
        "generated_at": _now(),
    }
    payload["observability_hash"] = content_hash(payload)
    path = observability_manifest_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_runbook(project_dir, payload)
    return payload


def observability_manifest_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "observability_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _runbook(project_dir: Path) -> dict[str, Any]:
    return {
        "artifact": "v4/observability_runbook.md",
        "incidents": [
            {"name": "MCP auth failures", "inspect": ["v4/mcp_call_audit.jsonl", "v4/mcp_policy_decisions.jsonl"], "first_action": "Check token project claim, role scopes, and require-token policy."},
            {"name": "Service request failures", "inspect": ["v4/service_request_audit.jsonl"], "first_action": "Filter by trace_id and failed service/action."},
            {"name": "Orchestrator stuck run", "inspect": ["v4/orchestrator_runs.json", "results/run_status.json"], "first_action": "Cancel or resume by orchestrator_run_id."},
            {"name": "Evidence/report mismatch", "inspect": ["v4/consistency_check.json"], "first_action": "Run consistency check and rebuild evidence trace index."},
        ],
        "local_commands": [
            f"python tc_lite.py service-call --project {project_dir.name} --service-id project_api --action status",
            f"python tc_lite.py service-call --project {project_dir.name} --service-id report_service --action validate",
        ],
    }


def _write_runbook(project_dir: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# TargetCompass v4 Observability Runbook",
        "",
        f"Project: `{project_dir.name}`",
        "",
        "## Local Signals",
    ]
    for key, value in manifest.get("signals", {}).items():
        lines.append(f"- `{key}`: `{value.get('source', '')}` count={value.get('count', 0)}")
    lines.extend(["", "## Incidents"])
    for incident in manifest.get("runbook", {}).get("incidents", []):
        lines.append(f"- {incident['name']}: inspect {', '.join(incident['inspect'])}; first action: {incident['first_action']}")
    lines.extend(["", "## Production Gaps"])
    for section in ["otel_contract", "prometheus_contract", "loki_contract"]:
        lines.append(f"- `{section}`: {manifest.get(section, {}).get('production_gap', '')}")
    path = v4_dir(project_dir) / "observability_runbook.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
