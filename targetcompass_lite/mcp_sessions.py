import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mcp_gateway import load_call_audit, summarize_call_audit
from .mcp_policy import ROLE_SCOPES, load_policy_decisions, parse_token, policy_path, write_default_policy
from .v4 import content_hash, v4_dir


SESSION_SCHEMA = "v4.mcp_sessions/0.1"
TOKEN_REGISTRY_SCHEMA = "v4.mcp_token_registry/0.1"
EXTERNAL_AUTH_SCHEMA = "v4.mcp_external_auth_manifest/0.1"
EXTERNAL_AUTH_READINESS_SCHEMA = "v4.mcp_external_auth_readiness/0.1"


def session_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_sessions.json"


def token_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_tokens.json"


def external_auth_manifest_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_external_auth_manifest.json"


def external_auth_readiness_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_external_auth_readiness.json"


def load_token_from_sources(token_json: str = "", token_file: str = "", env_var: str = "TARGETCOMPASS_MCP_TOKEN") -> str:
    if token_json.strip():
        return token_json.strip()
    if token_file.strip():
        path = Path(token_file).expanduser()
        return path.read_text(encoding="utf-8").strip()
    value = os.environ.get(env_var, "").strip()
    return value


def create_token(project_dir: Path, principal: str, role: str, scopes: list[str] | None = None, token_id: str = "") -> dict[str, Any]:
    policy = write_default_policy(project_dir)
    if role not in policy.get("roles", {}):
        raise ValueError(f"unknown MCP role: {role}")
    allowed = set(policy["roles"][role])
    selected = set(scopes or sorted(allowed))
    if not selected.issubset(allowed):
        raise ValueError("requested scopes exceed role grants")
    payload = {
        "principal": principal.strip() or "external_client",
        "role": role,
        "project": project_dir.name,
        "scopes": sorted(selected),
        "token_id": token_id.strip() or "tok_" + content_hash({"principal": principal, "role": role, "time": _now()})[:16],
    }
    register_token(project_dir, payload)
    return payload


def register_token(project_dir: Path, token_payload: dict[str, Any]) -> dict[str, Any]:
    parse_token(project_dir, json.dumps(token_payload, ensure_ascii=False), actor="token_registry")
    registry = load_token_registry(project_dir)
    token_id = token_payload.get("token_id", "")
    rows = [row for row in registry.get("tokens", []) if row.get("token_id") != token_id]
    rows.append(
        {
            "token_id": token_id,
            "principal": token_payload.get("principal", ""),
            "role": token_payload.get("role", ""),
            "project": token_payload.get("project", ""),
            "scopes": token_payload.get("scopes", []),
            "token_hash": content_hash(token_payload),
            "created_at": _now(),
            "status": "active",
        }
    )
    registry["tokens"] = rows
    registry["updated_at"] = _now()
    _write_json(token_registry_path(project_dir), registry)
    return registry


def load_token_registry(project_dir: Path) -> dict[str, Any]:
    path = token_registry_path(project_dir)
    if not path.exists():
        return {"schema_version": TOKEN_REGISTRY_SCHEMA, "project_id": project_dir.name, "tokens": [], "updated_at": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def start_session(project_dir: Path, token: str | None, client_id: str = "external_client", transport: str = "stdio") -> dict[str, Any]:
    principal = parse_token(project_dir, token, actor=client_id)
    session = {
        "session_id": "mcp_session_" + content_hash({"client": client_id, "principal": principal.principal_id, "time": _now()})[:16],
        "client_id": client_id,
        "transport": transport,
        "principal": principal.principal_id,
        "role": principal.role,
        "project_id": project_dir.name,
        "scopes": sorted(principal.scopes),
        "token_id": principal.token_id,
        "authenticated": principal.authenticated,
        "status": "active",
        "started_at": _now(),
        "last_seen_at": _now(),
    }
    registry = load_sessions(project_dir)
    registry["sessions"].append(session)
    registry["updated_at"] = _now()
    _write_json(session_registry_path(project_dir), registry)
    return session


def touch_session(project_dir: Path, session_id: str, status: str = "active") -> dict[str, Any]:
    registry = load_sessions(project_dir)
    updated = {}
    for row in registry.get("sessions", []):
        if row.get("session_id") == session_id:
            row["status"] = status
            row["last_seen_at"] = _now()
            updated = row
            break
    if updated:
        registry["updated_at"] = _now()
        _write_json(session_registry_path(project_dir), registry)
    return updated


def load_sessions(project_dir: Path) -> dict[str, Any]:
    path = session_registry_path(project_dir)
    if not path.exists():
        return {"schema_version": SESSION_SCHEMA, "project_id": project_dir.name, "sessions": [], "updated_at": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def update_policy(project_dir: Path, default_role: str = "", require_token: bool | None = None) -> dict[str, Any]:
    policy = write_default_policy(project_dir)
    if default_role:
        if default_role not in policy.get("roles", ROLE_SCOPES):
            raise ValueError(f"unknown default role: {default_role}")
        policy["default_role"] = default_role
    if require_token is not None:
        policy["require_token_for_external_clients"] = bool(require_token)
    policy["updated_at"] = _now()
    _write_json(policy_path(project_dir), policy)
    return policy


def query_mcp_audit(project_dir: Path, principal: str = "", tool_id: str = "", status: str = "", limit: int = 50) -> dict[str, Any]:
    summarize_call_audit(project_dir)
    rows = load_call_audit(project_dir)
    if principal:
        rows = [row for row in rows if row.get("principal", "") == principal]
    if tool_id:
        rows = [row for row in rows if row.get("tool_id", "") == tool_id]
    if status:
        rows = [row for row in rows if row.get("status", "") == status]
    decisions = load_policy_decisions(project_dir)
    return {
        "schema_version": "v4.mcp_audit_query/0.1",
        "project_id": project_dir.name,
        "filters": {"principal": principal, "tool_id": tool_id, "status": status, "limit": limit},
        "call_count": len(rows),
        "decision_count": len(decisions),
        "calls": rows[-max(1, limit) :],
        "latest_policy_decisions": decisions[-max(1, min(limit, 50)) :],
    }


def build_mcp_server_config(project_dir: Path, token_file: str = "", env_var: str = "TARGETCOMPASS_MCP_TOKEN") -> dict[str, Any]:
    policy = write_default_policy(project_dir)
    auth = build_external_auth_manifest(project_dir)
    return {
        "schema_version": "v4.mcp_server_config/0.1",
        "project_id": project_dir.name,
        "transport": "stdio",
        "token_sources": {
            "inline_json": True,
            "token_file": token_file,
            "environment_variable": env_var,
        },
        "policy": {
            "default_role": policy.get("default_role", ""),
            "require_token_for_external_clients": policy.get("require_token_for_external_clients", False),
            "roles": policy.get("roles", {}),
        },
        "session_registry": str(session_registry_path(project_dir).relative_to(project_dir)),
        "token_registry": str(token_registry_path(project_dir).relative_to(project_dir)),
        "audit_log": "v4/mcp_call_audit.jsonl",
        "policy_decisions": "v4/mcp_policy_decisions.jsonl",
        "external_auth_manifest": "v4/mcp_external_auth_manifest.json",
        "auth_mode": auth.get("active_auth_mode", "local_project_token"),
    }


def build_mcp_client_config(project_dir: Path, base_url: str = "", token_env: str = "TARGETCOMPASS_MCP_TOKEN") -> dict[str, Any]:
    base = base_url.rstrip("/") or f"http://127.0.0.1:8790/mcp/{project_dir.name}"
    return {
        "schema_version": "v4.mcp_client_config/0.1",
        "project_id": project_dir.name,
        "client_name": f"targetcompass-{project_dir.name}",
        "transports": {
            "http_jsonrpc": {
                "url": base,
                "method": "POST",
                "headers": {
                    "Authorization": f"Bearer ${{{token_env}}}",
                    "X-MCP-Client-ID": "external-agent",
                },
            },
            "sse_events": {
                "url": base + "/events",
                "method": "GET",
                "purpose": "Lightweight server/session readiness events. JSON-RPC calls still use http_jsonrpc.",
            },
            "stdio": {
                "command": "python",
                "args": ["tc_lite.py", "mcp-server", "--project", project_dir.name, "--token-env", token_env],
            },
        },
        "security": {
            "token_source": token_env,
            "project_bound_tokens": True,
            "session_header": "X-MCP-Session-ID",
            "client_id_header": "X-MCP-Client-ID",
            "recommended_role_for_read_only_agents": "agent_reader",
            "recommended_role_for_operators": "agent_operator",
        },
    }


def build_external_auth_manifest(project_dir: Path) -> dict[str, Any]:
    policy = write_default_policy(project_dir)
    sessions = load_sessions(project_dir)
    tokens = load_token_registry(project_dir)
    oidc_enabled = bool(os.environ.get("TARGETCOMPASS_OIDC_ISSUER") and os.environ.get("TARGETCOMPASS_OIDC_AUDIENCE"))
    vault_enabled = bool(os.environ.get("TARGETCOMPASS_VAULT_ADDR") and os.environ.get("TARGETCOMPASS_VAULT_TOKEN"))
    payload = {
        "schema_version": EXTERNAL_AUTH_SCHEMA,
        "project_id": project_dir.name,
        "active_auth_mode": "oidc" if oidc_enabled else "local_project_token",
        "project_isolation": {
            "project_bound_tokens": True,
            "token_project_claim_required": True,
            "cross_project_token_rejected": True,
            "artifact_paths_do_not_grant_authorization": True,
        },
        "multi_client_sessions": {
            "session_registry": "v4/mcp_sessions.json",
            "active_session_count": len([row for row in sessions.get("sessions", []) if row.get("status") == "active"]),
            "session_header": "X-MCP-Session-ID",
            "client_id_header": "X-MCP-Client-ID",
        },
        "token_auth": {
            "enabled": True,
            "require_token_for_external_clients": policy.get("require_token_for_external_clients", False),
            "token_registry": "v4/mcp_tokens.json",
            "registered_token_count": len(tokens.get("tokens", [])),
            "token_file_env": "TARGETCOMPASS_MCP_TOKEN_FILE",
            "token_json_env": "TARGETCOMPASS_MCP_TOKEN",
        },
        "oidc_contract": {
            "enabled": oidc_enabled,
            "issuer_env": "TARGETCOMPASS_OIDC_ISSUER",
            "audience_env": "TARGETCOMPASS_OIDC_AUDIENCE",
            "jwks_cache": "v4/oidc_jwks_cache.json",
            "status": "configured" if oidc_enabled else "not_configured",
        },
        "vault_contract": {
            "enabled": vault_enabled,
            "address_env": "TARGETCOMPASS_VAULT_ADDR",
            "token_env": "TARGETCOMPASS_VAULT_TOKEN",
            "secret_mount": f"targetcompass/{project_dir.name}/",
            "status": "configured" if vault_enabled else "not_configured",
        },
        "generated_at": _now(),
    }
    payload["auth_hash"] = content_hash(payload)
    _write_json(external_auth_manifest_path(project_dir), payload)
    return payload


def check_external_auth_readiness(project_dir: Path) -> dict[str, Any]:
    manifest = build_external_auth_manifest(project_dir)
    policy = write_default_policy(project_dir)
    tokens = load_token_registry(project_dir)
    sessions = load_sessions(project_dir)
    audit = summarize_call_audit(project_dir)
    decisions = load_policy_decisions(project_dir)
    checks = [
        _readiness_check(
            "project_bound_tokens",
            bool(manifest.get("project_isolation", {}).get("project_bound_tokens")),
            "Tokens include the project claim and are parsed against the current project.",
            "Keep project-bound token parsing enabled before exposing MCP to external clients.",
        ),
        _readiness_check(
            "cross_project_token_rejection",
            bool(manifest.get("project_isolation", {}).get("cross_project_token_rejected")),
            "Cross-project tokens are rejected by the local policy contract.",
            "Reject tokens whose project claim does not match the requested project.",
        ),
        _readiness_check(
            "external_token_required",
            bool(policy.get("require_token_for_external_clients")),
            "External clients must provide a token.",
            "Enable 'Require token for external clients' before multi-client or remote use.",
            severity="warning",
        ),
        _readiness_check(
            "registered_token_available",
            len(tokens.get("tokens", [])) > 0,
            "At least one project-scoped token descriptor is registered.",
            "Create a project token for each external client or service account.",
            severity="warning",
        ),
        _readiness_check(
            "session_headers_declared",
            bool(manifest.get("multi_client_sessions", {}).get("session_header"))
            and bool(manifest.get("multi_client_sessions", {}).get("client_id_header")),
            "Session and client-id headers are declared for HTTP/SSE clients.",
            "Declare X-MCP-Session-ID and X-MCP-Client-ID headers in client templates.",
        ),
        _readiness_check(
            "mcp_audit_available",
            audit.get("call_count", 0) > 0 or bool(decisions),
            "MCP calls or policy decisions have been audited.",
            "Exercise at least one tool/resource call so audit files prove the gateway path.",
            severity="warning",
        ),
    ]
    oidc_issuer = bool(os.environ.get("TARGETCOMPASS_OIDC_ISSUER"))
    oidc_audience = bool(os.environ.get("TARGETCOMPASS_OIDC_AUDIENCE"))
    checks.append(
        _readiness_check(
            "oidc_env_complete",
            (oidc_issuer and oidc_audience) or (not oidc_issuer and not oidc_audience),
            "OIDC env contract is either fully configured or intentionally disabled.",
            "Set both TARGETCOMPASS_OIDC_ISSUER and TARGETCOMPASS_OIDC_AUDIENCE, or unset both.",
            severity="warning",
        )
    )
    vault_addr = bool(os.environ.get("TARGETCOMPASS_VAULT_ADDR"))
    vault_token = bool(os.environ.get("TARGETCOMPASS_VAULT_TOKEN"))
    checks.append(
        _readiness_check(
            "vault_env_complete",
            (vault_addr and vault_token) or (not vault_addr and not vault_token),
            "Vault env contract is either fully configured or intentionally disabled.",
            "Set both TARGETCOMPASS_VAULT_ADDR and TARGETCOMPASS_VAULT_TOKEN, or unset both.",
            severity="warning",
        )
    )
    hard_failed = [row for row in checks if row["status"] == "FAIL" and row["severity"] == "error"]
    warnings = [row for row in checks if row["status"] == "WARN" or (row["status"] == "FAIL" and row["severity"] == "warning")]
    payload = {
        "schema_version": EXTERNAL_AUTH_READINESS_SCHEMA,
        "project_id": project_dir.name,
        "status": "BLOCKED" if hard_failed else ("READY_WITH_WARNINGS" if warnings else "READY"),
        "active_auth_mode": manifest.get("active_auth_mode", "local_project_token"),
        "summary": {
            "check_count": len(checks),
            "pass_count": len([row for row in checks if row["status"] == "PASS"]),
            "warning_count": len(warnings),
            "failure_count": len(hard_failed),
            "registered_token_count": len(tokens.get("tokens", [])),
            "active_session_count": len([row for row in sessions.get("sessions", []) if row.get("status") == "active"]),
            "audit_call_count": audit.get("call_count", 0),
            "policy_decision_count": len(decisions),
        },
        "checks": checks,
        "artifact_refs": {
            "external_auth_manifest": "v4/mcp_external_auth_manifest.json",
            "policy": "v4/mcp_policy.json",
            "token_registry": "v4/mcp_tokens.json",
            "session_registry": "v4/mcp_sessions.json",
            "call_audit": "v4/mcp_call_audit.jsonl",
            "policy_decisions": "v4/mcp_policy_decisions.jsonl",
        },
        "generated_at": _now(),
    }
    payload["readiness_hash"] = content_hash(payload)
    _write_json(external_auth_readiness_path(project_dir), payload)
    return payload


def _readiness_check(check_id: str, passed: bool, ok: str, remediation: str, severity: str = "error") -> dict[str, Any]:
    status = "PASS" if passed else ("WARN" if severity == "warning" else "FAIL")
    return {
        "check_id": check_id,
        "status": status,
        "severity": severity,
        "message": ok if passed else remediation,
        "remediation": "" if passed else remediation,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
