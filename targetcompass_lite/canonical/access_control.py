from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .backend_writer import write_json_artifact


ACCESS_SCHEMA = "v5.access_control/0.1"
AUDIT_SCHEMA = "v5.access_audit/0.1"

ROLE_PERMISSIONS = {
    "owner": {"project:read", "project:write", "member:write", "permission:write", "token:write", "audit:read", "run:write", "review:write"},
    "admin": {"project:read", "project:write", "member:write", "permission:write", "token:write", "audit:read", "run:write", "review:write"},
    "operator": {"project:read", "run:write", "review:write", "audit:read"},
    "reviewer": {"project:read", "review:write", "audit:read"},
    "viewer": {"project:read", "audit:read"},
}


def initialize_access_control(project_dir: str | Path, *, owner_id: str = "local_owner", owner_name: str = "Local Owner") -> dict[str, Any]:
    project_dir = Path(project_dir)
    registry = load_access_registry(project_dir)
    if registry.get("users"):
        return registry
    now = _now()
    registry = {
        "schema_version": ACCESS_SCHEMA,
        "project_id": project_dir.name,
        "roles": {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
        "users": [{"user_id": owner_id, "display_name": owner_name, "status": "active", "created_at": now}],
        "members": [{"user_id": owner_id, "role": "owner", "status": "active", "created_at": now}],
        "tokens": [],
        "updated_at": now,
    }
    _write_json(_registry_path(project_dir), registry)
    _audit(project_dir, actor=owner_id, action="access.initialize", status="allowed", target=owner_id, reason="created local owner")
    return registry


def create_user(project_dir: str | Path, user_id: str, display_name: str = "", *, actor: str = "local_owner") -> dict[str, Any]:
    project_dir = Path(project_dir)
    authorize(project_dir, actor, "member:write")
    registry = initialize_access_control(project_dir)
    if any(row.get("user_id") == user_id for row in registry.get("users", [])):
        raise ValueError(f"user already exists: {user_id}")
    registry["users"].append({"user_id": user_id, "display_name": display_name or user_id, "status": "active", "created_at": _now()})
    registry["updated_at"] = _now()
    _write_json(_registry_path(project_dir), registry)
    _audit(project_dir, actor=actor, action="user.create", status="allowed", target=user_id, reason="")
    return registry


def set_project_member(project_dir: str | Path, user_id: str, role: str, *, actor: str = "local_owner", status: str = "active") -> dict[str, Any]:
    project_dir = Path(project_dir)
    authorize(project_dir, actor, "permission:write")
    registry = initialize_access_control(project_dir)
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    if not any(row.get("user_id") == user_id for row in registry.get("users", [])):
        registry["users"].append({"user_id": user_id, "display_name": user_id, "status": "active", "created_at": _now()})
    members = [row for row in registry.get("members", []) if row.get("user_id") != user_id]
    members.append({"user_id": user_id, "role": role, "status": status, "created_at": _now()})
    registry["members"] = members
    registry["updated_at"] = _now()
    _write_json(_registry_path(project_dir), registry)
    _audit(project_dir, actor=actor, action="member.set_role", status="allowed", target=user_id, reason=f"role={role}; status={status}")
    return registry


def issue_access_token(
    project_dir: str | Path,
    user_id: str,
    *,
    actor: str = "local_owner",
    ttl_minutes: int = 1440,
    scopes: list[str] | None = None,
    token_id: str = "",
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    authorize(project_dir, actor, "token:write")
    registry = initialize_access_control(project_dir)
    member = _member_for(registry, user_id)
    if not member:
        raise PermissionError(f"user is not a project member: {user_id}")
    allowed = ROLE_PERMISSIONS[member["role"]]
    selected = set(scopes or sorted(allowed))
    if not selected.issubset(allowed):
        raise PermissionError("requested token scopes exceed member role")
    now = datetime.now(timezone.utc)
    token_payload = {
        "schema_version": "v5.project_token/0.1",
        "token_id": token_id or "v5tok_" + _hash({"user_id": user_id, "time": now.isoformat()})[:16],
        "project_id": project_dir.name,
        "user_id": user_id,
        "role": member["role"],
        "scopes": sorted(selected),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=max(1, ttl_minutes))).isoformat(),
    }
    row = dict(token_payload)
    row["token_hash"] = _hash(token_payload)
    row["status"] = "active"
    registry["tokens"] = [item for item in registry.get("tokens", []) if item.get("token_id") != row["token_id"]] + [row]
    registry["updated_at"] = _now()
    _write_json(_registry_path(project_dir), registry)
    _audit(project_dir, actor=actor, action="token.issue", status="allowed", target=user_id, reason=f"token_id={row['token_id']}")
    return token_payload


def revoke_access_token(project_dir: str | Path, token_id: str, *, actor: str = "local_owner", reason: str = "") -> dict[str, Any]:
    project_dir = Path(project_dir)
    authorize(project_dir, actor, "token:write")
    registry = initialize_access_control(project_dir)
    found = False
    for row in registry.get("tokens", []):
        if row.get("token_id") == token_id:
            row["status"] = "revoked"
            row["revoked_at"] = _now()
            row["revoke_reason"] = reason
            found = True
    if not found:
        raise ValueError(f"unknown token_id: {token_id}")
    registry["updated_at"] = _now()
    _write_json(_registry_path(project_dir), registry)
    _audit(project_dir, actor=actor, action="token.revoke", status="allowed", target=token_id, reason=reason)
    return registry


def authorize(project_dir: str | Path, principal: str, permission: str) -> dict[str, Any]:
    project_dir = Path(project_dir)
    registry = initialize_access_control(project_dir)
    member = _member_for(registry, principal)
    allowed = False
    reason = ""
    if member and member.get("status") == "active":
        allowed = permission in ROLE_PERMISSIONS.get(member.get("role", ""), set())
        reason = "allowed" if allowed else f"missing permission: {permission}"
    else:
        reason = "principal is not an active project member"
    decision = _audit(project_dir, actor=principal, action=f"authorize:{permission}", status="allowed" if allowed else "denied", target=project_dir.name, reason=reason)
    if not allowed:
        raise PermissionError(reason)
    return decision


def query_access_audit(project_dir: str | Path, *, actor: str = "", action: str = "", status: str = "", limit: int = 50) -> dict[str, Any]:
    project_dir = Path(project_dir)
    rows = _load_audit(project_dir)
    if actor:
        rows = [row for row in rows if row.get("actor") == actor]
    if action:
        rows = [row for row in rows if row.get("action") == action]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return {
        "schema_version": "v5.access_audit_query/0.1",
        "project_id": project_dir.name,
        "filters": {"actor": actor, "action": action, "status": status, "limit": limit},
        "match_count": len(rows),
        "events": rows[-max(1, limit) :],
    }


def access_readiness(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    registry = initialize_access_control(project_dir)
    active_members = [row for row in registry.get("members", []) if row.get("status") == "active"]
    active_tokens = [row for row in registry.get("tokens", []) if row.get("status") == "active" and not _is_expired(row.get("expires_at", ""))]
    audit_rows = _load_audit(project_dir)
    checks = [
        _check("owner_exists", any(row.get("role") == "owner" for row in active_members), "Owner member exists.", "Create an owner member."),
        _check("roles_declared", set(ROLE_PERMISSIONS).issubset(set(registry.get("roles", {}))), "Roles declared.", "Rebuild v5 access registry."),
        _check("active_member_exists", bool(active_members), f"{len(active_members)} active member(s).", "Add at least one project member."),
        _check("active_token_exists", bool(active_tokens), f"{len(active_tokens)} active token(s).", "Issue at least one token before external/multi-user use.", severity="warn"),
        _check("audit_available", bool(audit_rows), f"{len(audit_rows)} audit event(s).", "Exercise at least one permission decision.", severity="warn"),
    ]
    failed = [row for row in checks if row["status"] == "FAIL"]
    warnings = [row for row in checks if row["status"] == "WARN"]
    payload = {
        "schema_version": "v5.access_readiness/0.1",
        "project_id": project_dir.name,
        "status": "BLOCKED" if failed else ("READY_WITH_WARNINGS" if warnings else "READY"),
        "summary": {
            "user_count": len(registry.get("users", [])),
            "active_member_count": len(active_members),
            "active_token_count": len(active_tokens),
            "audit_event_count": len(audit_rows),
        },
        "checks": checks,
        "registry_ref": "v5/access/access_registry.json",
        "audit_ref": "v5/access/access_audit.jsonl",
        "generated_at": _now(),
    }
    _write_json(project_dir / "v5" / "access" / "access_readiness.json", payload)
    return payload


def load_access_registry(project_dir: str | Path) -> dict[str, Any]:
    path = _registry_path(Path(project_dir))
    if not path.exists():
        return {"schema_version": ACCESS_SCHEMA, "project_id": Path(project_dir).name, "roles": {}, "users": [], "members": [], "tokens": [], "updated_at": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def _member_for(registry: dict[str, Any], user_id: str) -> dict[str, Any] | None:
    for row in registry.get("members", []):
        if row.get("user_id") == user_id:
            return row
    return None


def _registry_path(project_dir: Path) -> Path:
    return project_dir / "v5" / "access" / "access_registry.json"


def _audit_path(project_dir: Path) -> Path:
    return project_dir / "v5" / "access" / "access_audit.jsonl"


def _audit(project_dir: Path, *, actor: str, action: str, status: str, target: str, reason: str) -> dict[str, Any]:
    event = {
        "schema_version": AUDIT_SCHEMA,
        "event_id": "access_" + _hash({"actor": actor, "action": action, "target": target, "time": _now()})[:16],
        "project_id": project_dir.name,
        "actor": actor,
        "action": action,
        "target": target,
        "status": status,
        "reason": reason,
        "created_at": _now(),
    }
    path = _audit_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def _load_audit(project_dir: Path) -> list[dict[str, Any]]:
    path = _audit_path(project_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _check(check_id: str, ok: bool, message: str, remediation: str, *, severity: str = "fail") -> dict[str, Any]:
    status = "PASS" if ok else ("WARN" if severity == "warn" else "FAIL")
    return {"check_id": check_id, "status": status, "message": message if ok else remediation, "remediation": "" if ok else remediation}


def _is_expired(value: str) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    project_dir = _project_dir_from_v5_path(path)
    write_json_artifact(project_dir, path.relative_to(project_dir), payload, producer="access_control", artifact_type="access_control_json")


def _project_dir_from_v5_path(path: Path) -> Path:
    parts = path.parts
    if "v5" in parts:
        return Path(*parts[: parts.index("v5")])
    return path.parent


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
