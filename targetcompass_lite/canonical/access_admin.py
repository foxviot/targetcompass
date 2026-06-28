from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .access_control import ROLE_PERMISSIONS, access_readiness, load_access_registry, query_access_audit
from .backend_writer import write_json_artifact


ACCESS_ADMIN_SCHEMA = "v5.access_admin_dashboard/0.1"


def build_access_admin_dashboard(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    registry = load_access_registry(project_dir)
    readiness = access_readiness(project_dir)
    audit = query_access_audit(project_dir, limit=100)
    users = {row.get("user_id", ""): row for row in registry.get("users", [])}
    members = _members(registry, users)
    tokens = _tokens(registry)
    dashboard = {
        "schema_version": ACCESS_ADMIN_SCHEMA,
        "project_id": project_dir.name,
        "readiness_status": readiness.get("status", ""),
        "summary": {
            "user_count": len(registry.get("users", [])),
            "member_count": len(registry.get("members", [])),
            "active_member_count": len([row for row in members if row.get("status") == "active"]),
            "active_token_count": len([row for row in tokens if row.get("lifecycle_status") == "active"]),
            "expired_token_count": len([row for row in tokens if row.get("lifecycle_status") == "expired"]),
            "revoked_token_count": len([row for row in tokens if row.get("lifecycle_status") == "revoked"]),
            "audit_event_count": audit.get("match_count", 0),
        },
        "members": members,
        "tokens": tokens,
        "role_coverage": _role_coverage(members),
        "token_lifecycle_summary": _token_lifecycle_summary(tokens),
        "permission_matrix": _permission_matrix(),
        "audit_summary": _audit_summary(audit.get("events", [])),
        "actions_required": _actions_required(readiness, members, tokens),
        "productization_gaps": _productization_gaps(readiness, members, tokens, audit.get("events", [])),
        "admin_capabilities": _admin_capabilities(),
        "refs": {
            "registry": "v5/access/access_registry.json",
            "audit": "v5/access/access_audit.jsonl",
            "readiness": "v5/access/access_readiness.json",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_artifact(project_dir, "v5/access/access_admin_dashboard.json", dashboard, producer="access_admin", artifact_type="access_admin_dashboard")
    return dashboard


def _members(registry: dict[str, Any], users: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in registry.get("members", []):
        user = users.get(row.get("user_id", ""), {})
        rows.append(
            {
                "user_id": row.get("user_id", ""),
                "display_name": user.get("display_name", row.get("user_id", "")),
                "role": row.get("role", ""),
                "status": row.get("status", ""),
                "permissions": sorted(ROLE_PERMISSIONS.get(row.get("role", ""), set())),
                "created_at": row.get("created_at", ""),
            }
        )
    return rows


def _tokens(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    now = datetime.now(timezone.utc)
    for row in registry.get("tokens", []):
        status = row.get("status", "active")
        lifecycle = status
        expires_at = row.get("expires_at", "")
        if status == "active":
            try:
                if datetime.fromisoformat(expires_at) <= now:
                    lifecycle = "expired"
            except ValueError:
                lifecycle = "invalid_expiry"
        rows.append(
            {
                "token_id": row.get("token_id", ""),
                "user_id": row.get("user_id", ""),
                "role": row.get("role", ""),
                "scopes": row.get("scopes", []),
                "status": status,
                "lifecycle_status": lifecycle,
                "issued_at": row.get("issued_at", ""),
                "expires_at": expires_at,
                "revoked_at": row.get("revoked_at", ""),
                "token_hash_prefix": str(row.get("token_hash", ""))[:12],
            }
        )
    return rows


def _permission_matrix() -> list[dict[str, Any]]:
    return [{"role": role, "permissions": sorted(perms)} for role, perms in sorted(ROLE_PERMISSIONS.items())]


def _role_coverage(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_roles = {row.get("role", "") for row in members if row.get("status") == "active"}
    return [
        {
            "role": role,
            "active_member_count": len([row for row in members if row.get("role") == role and row.get("status") == "active"]),
            "covered": role in active_roles,
            "permissions": sorted(ROLE_PERMISSIONS.get(role, set())),
        }
        for role in sorted(ROLE_PERMISSIONS)
    ]


def _token_lifecycle_summary(tokens: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    scoped_tokens = 0
    for row in tokens:
        by_status[row.get("lifecycle_status", "unknown")] = by_status.get(row.get("lifecycle_status", "unknown"), 0) + 1
        if row.get("scopes"):
            scoped_tokens += 1
    return {
        "by_lifecycle_status": by_status,
        "scoped_token_count": scoped_tokens,
        "rotation_required": bool(by_status.get("expired") or by_status.get("invalid_expiry")),
        "active_token_ids": [row.get("token_id", "") for row in tokens if row.get("lifecycle_status") == "active"],
    }


def _audit_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for row in events:
        by_status[row.get("status", "unknown")] = by_status.get(row.get("status", "unknown"), 0) + 1
        by_action[row.get("action", "unknown")] = by_action.get(row.get("action", "unknown"), 0) + 1
    return {"by_status": by_status, "by_action": by_action, "recent_events": events[-20:]}


def _actions_required(readiness: dict[str, Any], members: list[dict[str, Any]], tokens: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions = []
    if readiness.get("status") == "BLOCKED":
        actions.append({"priority": "P0", "action": "Fix access readiness failures before external users operate this project."})
    if not any(row.get("role") == "owner" and row.get("status") == "active" for row in members):
        actions.append({"priority": "P0", "action": "Create at least one active owner member."})
    if not any(row.get("lifecycle_status") == "active" for row in tokens):
        actions.append({"priority": "P1", "action": "Issue a scoped token for external agents or multi-user validation."})
    if any(row.get("lifecycle_status") == "expired" for row in tokens):
        actions.append({"priority": "P2", "action": "Rotate expired tokens and revoke unused credentials."})
    return actions


def _productization_gaps(
    readiness: dict[str, Any], members: list[dict[str, Any]], tokens: list[dict[str, Any]], audit_events: list[dict[str, Any]]
) -> list[dict[str, str]]:
    gaps = []
    if readiness.get("status") != "READY":
        gaps.append({"priority": "P0", "gap": "access_readiness_not_clean", "next_step": "Resolve failed/warn checks before inviting external operators."})
    if not any(row.get("role") == "admin" and row.get("status") == "active" for row in members):
        gaps.append({"priority": "P1", "gap": "no_active_admin", "next_step": "Add an admin member separate from the owner for operational handoff."})
    if not any(row.get("role") == "reviewer" and row.get("status") == "active" for row in members):
        gaps.append({"priority": "P1", "gap": "no_active_reviewer", "next_step": "Add a reviewer member so scientific approval is not performed by the runner identity."})
    if tokens and not any(row.get("lifecycle_status") == "revoked" for row in tokens):
        gaps.append({"priority": "P2", "gap": "token_revocation_not_exercised", "next_step": "Run one token rotation/revocation drill and verify audit events."})
    if not any(row.get("status") == "denied" for row in audit_events):
        gaps.append({"priority": "P2", "gap": "denied_permission_path_not_tested", "next_step": "Exercise a denied permission request to prove RBAC enforcement is visible in audit."})
    return gaps


def _admin_capabilities() -> list[dict[str, str]]:
    return [
        {"capability": "user_management", "status": "implemented", "entrypoint": "UI /v5/access and CLI v5-access create-user"},
        {"capability": "project_membership", "status": "implemented", "entrypoint": "UI /v5/access and CLI v5-access set-member"},
        {"capability": "role_permission_matrix", "status": "implemented", "entrypoint": "v5/access/access_admin_dashboard.json"},
        {"capability": "token_lifecycle", "status": "implemented", "entrypoint": "UI issue/revoke token and audit trail"},
        {"capability": "audit_search", "status": "implemented", "entrypoint": "UI /v5/audit"},
        {"capability": "login_sessions", "status": "local_scaffold", "entrypoint": "planned for packaged multi-user runtime"},
    ]
