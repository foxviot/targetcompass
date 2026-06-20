import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, v4_dir


POLICY_SCHEMA = "v4.mcp_policy/0.1"
DECISION_SCHEMA = "v4.mcp_policy_decision/0.1"


ROLE_SCOPES = {
    "local_admin": {"resource:read", "tool:read", "tool:write", "review:write", "registry:write", "knowledge:write"},
    "reviewer": {"resource:read", "tool:read", "review:write"},
    "agent_reader": {"resource:read", "tool:read"},
    "agent_operator": {"resource:read", "tool:read", "tool:write"},
}


TOOL_SCOPES = {
    "resource.read": "resource:read",
    "v4.build_manifest": "tool:write",
    "review.queue.build": "review:write",
    "evidence.index.build": "tool:write",
    "evidence.trace.query": "tool:read",
    "knowledge.adapt_resources": "knowledge:write",
    "codex.task_packet.inspect": "tool:read",
    "method.registry.list": "tool:read",
    "method.config.read": "tool:read",
    "method.config.update": "registry:write",
    "role.runs.list": "tool:read",
    "role.run.inspect": "tool:read",
}


@dataclass(frozen=True)
class Principal:
    principal_id: str
    role: str
    project: str
    scopes: set[str]
    token_id: str
    authenticated: bool


def default_policy(project_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA,
        "project_id": project_dir.name,
        "policy_id": "local_mcp_rbac_v1",
        "version": "0.1.0",
        "default_role": "local_admin",
        "require_token_for_external_clients": False,
        "roles": {role: sorted(scopes) for role, scopes in ROLE_SCOPES.items()},
        "tool_scopes": TOOL_SCOPES,
    }


def write_default_policy(project_dir: Path) -> dict[str, Any]:
    path = policy_path(project_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    payload = default_policy(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def policy_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_policy.json"


def policy_decisions_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_policy_decisions.jsonl"


def parse_token(project_dir: Path, token: str | None, actor: str = "local_gateway") -> Principal:
    policy = write_default_policy(project_dir)
    if not token:
        role = policy.get("default_role", "local_admin")
        scopes = set(policy.get("roles", {}).get(role, []))
        return Principal(actor, role, project_dir.name, scopes, "local_dev", False)
    try:
        payload = json.loads(token)
    except json.JSONDecodeError as exc:
        raise PermissionError(f"invalid MCP token JSON: {exc}")
    project = payload.get("project", "")
    if project != project_dir.name:
        raise PermissionError("token project scope does not match this project")
    role = payload.get("role", "agent_reader")
    role_scopes = set(policy.get("roles", {}).get(role, []))
    requested = set(payload.get("scopes", []))
    scopes = requested if requested else role_scopes
    if not scopes.issubset(role_scopes):
        raise PermissionError("token scopes exceed role grants")
    return Principal(
        principal_id=payload.get("principal", "external_client"),
        role=role,
        project=project,
        scopes=scopes,
        token_id=payload.get("token_id", "inline_token"),
        authenticated=True,
    )


def authorize_tool(project_dir: Path, principal: Principal, tool_id: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = write_default_policy(project_dir)
    required = policy.get("tool_scopes", {}).get(tool_id, "tool:read")
    allow = required in principal.scopes
    decision = {
        "schema_version": DECISION_SCHEMA,
        "decision_id": "pd_" + content_hash({"principal": principal.principal_id, "tool": tool_id, "args": arguments or {}, "time": _now()})[:16],
        "policy_id": policy.get("policy_id", "local_mcp_rbac_v1"),
        "policy_version": policy.get("version", "0.1.0"),
        "principal": principal.principal_id,
        "role": principal.role,
        "project_id": project_dir.name,
        "action": f"tool:{tool_id}",
        "required_scope": required,
        "granted_scopes": sorted(principal.scopes),
        "allow": allow,
        "reason": "allowed" if allow else f"missing required scope: {required}",
        "arguments_hash": content_hash(arguments or {}),
        "created_at": _now(),
    }
    record_policy_decision(project_dir, decision)
    if not allow:
        raise PermissionError(decision["reason"])
    return decision


def authorize_resource(project_dir: Path, principal: Principal, uri: str) -> dict[str, Any]:
    allow = "resource:read" in principal.scopes
    decision = {
        "schema_version": DECISION_SCHEMA,
        "decision_id": "pd_" + content_hash({"principal": principal.principal_id, "resource": uri, "time": _now()})[:16],
        "policy_id": "local_mcp_rbac_v1",
        "policy_version": "0.1.0",
        "principal": principal.principal_id,
        "role": principal.role,
        "project_id": project_dir.name,
        "action": f"resource:{uri}",
        "required_scope": "resource:read",
        "granted_scopes": sorted(principal.scopes),
        "allow": allow,
        "reason": "allowed" if allow else "missing required scope: resource:read",
        "arguments_hash": content_hash({"uri": uri}),
        "created_at": _now(),
    }
    record_policy_decision(project_dir, decision)
    if not allow:
        raise PermissionError(decision["reason"])
    return decision


def record_policy_decision(project_dir: Path, decision: dict[str, Any]) -> None:
    path = policy_decisions_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(decision, ensure_ascii=False) + "\n")


def load_policy_decisions(project_dir: Path) -> list[dict[str, Any]]:
    path = policy_decisions_path(project_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
