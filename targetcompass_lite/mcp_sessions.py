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


def session_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_sessions.json"


def token_registry_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_tokens.json"


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
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
