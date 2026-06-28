import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..v4 import content_hash
from .agent_specs import build_agent_specs
from .backend_writer import write_json_artifact


MEMORY_SCHEMA_VERSION = "v5.memory_palace/0.1"
EVENT_SCHEMA_VERSION = "v5.memory_event/0.1"

DEFAULT_PILOTDECK_MEMORY = {
    "architecture_boundary": {
        "pilotdeck_role": "agent cockpit, workspace, conversation, tool scheduling, project context, memory, and skills",
        "bioinfo_agent_system_role": "domain state machine and scientific workflow control plane",
        "mcp_role": "auditable tool boundary for datasets, literature, Codex, Evidence DB, and scoring",
        "codex_role": "sandboxed small-task executor that reads inputs, writes code/scripts, runs tests, and returns artifacts/logs",
        "evidence_db_role": "scientific evidence, datasets, papers, methods, runs, audit records, candidate conclusions",
        "human_review_role": "final scientific approval and signoff",
    },
    "agent_topology": [
        "bioinfo_orchestrator",
        "scientific_question_normalizer",
        "scope_ontology_resolver",
        "evidence_plan_builder",
        "resource_discovery_agent",
        "method_adapter_workorder_compiler",
        "result_auditor_evidence_synthesizer",
    ],
    "state_model": [
        "INTAKE",
        "QUESTION_NORMALIZED",
        "SCOPE_RESOLVED",
        "EVIDENCE_PLAN_BUILT",
        "RESOURCES_DISCOVERED",
        "METHODS_SELECTED",
        "CODEX_PACKETS_READY",
        "EXECUTION_PENDING_APPROVAL",
        "RUN_COMPLETED",
        "AUDITED",
        "EVIDENCE_SYNTHESIZED",
        "HUMAN_REVIEW",
    ],
    "schema_bound_rules": [
        "Input must validate against previous schema.",
        "Output must validate against current schema.",
        "No free-form final answer as the primary protocol.",
        "No unsupported dataset IDs.",
        "No paper claim without PMID, DOI, or source.",
        "No method recommendation without required input data type.",
        "No Codex packet without expected input files and expected output files.",
    ],
    "mcp_tool_boundary": [
        "bioinfo.search_public_datasets",
        "bioinfo.get_dataset_metadata",
        "bioinfo.search_literature",
        "bioinfo.extract_paper_methods",
        "bioinfo.select_analysis_methods",
        "bioinfo.compile_codex_task_packet",
        "bioinfo.validate_codex_task_packet",
        "bioinfo.write_evidence_item",
        "bioinfo.score_candidate_targets",
    ],
    "codex_boundaries": {
        "responsible_for": ["read inputs", "write code/scripts", "run scripts/tests", "produce results", "save logs", "report failures"],
        "not_responsible_for": [
            "decide whether the research question is valid",
            "decide whether literature supports the final claim",
            "decide novelty",
            "approve final scientific conclusions",
        ],
    },
    "result_audit_checks": [
        "input files exist",
        "expected outputs are complete",
        "logs contain no blocking error/warning",
        "statistical method matches data type",
        "figures and tables agree",
        "conclusion does not exceed claim ceiling",
        "artifact can be written to Evidence DB",
    ],
    "memory_vs_evidence_db": {
        "pilotdeck_memory": "project context, conversation memory, user preferences, long-term task background, architecture decisions",
        "evidence_db": "scientific evidence chain, datasets, papers, methods, run records, audit records, candidate conclusions",
        "hard_rule": "scientific evidence must not exist only in general memory; it must be stored in Evidence DB or a v5 ArtifactManifest/EvidenceItemRef.",
    },
}


def install_pilotdeck_memory(project_dir: Path, source_doc: str = "", actor: str = "codex") -> dict[str, Any]:
    root = memory_palace_dir(project_dir)
    payload = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "memory_id": _stable_id("memory_palace", project_dir.name, DEFAULT_PILOTDECK_MEMORY),
        "installed_at": _now(),
        "installed_by": actor,
        "source_doc": source_doc,
        "status": "active",
        "scope": "pilotdeck_workspace_memory_not_scientific_evidence",
        "scientific_evidence_policy": "Do not store scientific conclusions only in memory; use Evidence DB and artifact/evidence refs.",
        "memory": DEFAULT_PILOTDECK_MEMORY,
    }
    version = _write_memory_version(project_dir, DEFAULT_PILOTDECK_MEMORY, actor=actor, reason="initial install", previous_hash="")
    payload["active_version_id"] = version["version_id"]
    payload["memory_hash"] = version["memory_hash"]
    _write_json(memory_manifest_path(project_dir), payload)
    event = append_memory_event(
        project_dir,
        "memory_palace_installed",
        actor=actor,
        message="Installed PilotDeck-compatible memory palace for v5 control-plane context.",
        payload={"memory_id": payload["memory_id"], "source_doc": source_doc},
    )
    payload["event_ref"] = f"v5/memory_palace/events.jsonl#{event['event_id']}"
    _write_json(memory_manifest_path(project_dir), payload)
    return payload


def load_memory_palace(project_dir: Path) -> dict[str, Any]:
    path = memory_manifest_path(project_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def update_memory_entry(project_dir: Path, key: str, value: Any, *, actor: str = "codex", reason: str = "") -> dict[str, Any]:
    current = load_memory_palace(project_dir) or install_pilotdeck_memory(project_dir, actor=actor)
    memory = dict(current.get("memory", {}))
    previous_hash = content_hash(memory)
    memory[key] = value
    version = _write_memory_version(project_dir, memory, actor=actor, reason=reason, previous_hash=previous_hash)
    current["memory"] = memory
    current["memory_id"] = _stable_id("memory_palace", project_dir.name, memory)
    current["active_version_id"] = version["version_id"]
    current["updated_at"] = _now()
    current["updated_by"] = actor
    current["memory_hash"] = version["memory_hash"]
    _write_json(memory_manifest_path(project_dir), current)
    append_memory_event(project_dir, "memory_entry_updated", actor=actor, message=f"Updated memory key: {key}", payload={"key": key, "version_id": version["version_id"], "reason": reason})
    return current


def rollback_memory(project_dir: Path, version_id: str, *, actor: str = "codex", reason: str = "") -> dict[str, Any]:
    version = _load_memory_version(project_dir, version_id)
    if not version:
        raise ValueError(f"unknown memory version: {version_id}")
    current = load_memory_palace(project_dir) or install_pilotdeck_memory(project_dir, actor=actor)
    current["memory"] = version["memory"]
    current["active_version_id"] = version_id
    current["memory_hash"] = version["memory_hash"]
    current["updated_at"] = _now()
    current["updated_by"] = actor
    _write_json(memory_manifest_path(project_dir), current)
    append_memory_event(project_dir, "memory_rollback", actor=actor, message=f"Rolled back memory to {version_id}.", payload={"version_id": version_id, "reason": reason})
    return current


def list_memory_versions(project_dir: Path) -> list[dict[str, Any]]:
    root = memory_palace_dir(project_dir) / "versions"
    versions = []
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        versions.append({k: data.get(k) for k in ["version_id", "created_at", "actor", "reason", "memory_hash", "previous_hash"]})
    return sorted(versions, key=lambda row: row.get("created_at", ""))


def build_agent_memory_context(project_dir: Path, agent_id: str) -> dict[str, Any]:
    specs = build_agent_specs()
    if agent_id not in specs:
        raise ValueError(f"unknown agent_id: {agent_id}")
    memory = _ensure_versioned_memory(project_dir, actor="memory_context")
    selected_keys = [
        "architecture_boundary",
        "schema_bound_rules",
        "memory_vs_evidence_db",
        "codex_boundaries",
        "result_audit_checks",
    ]
    context = {
        "schema_version": "v5.agent_memory_context/0.1",
        "project_id": project_dir.name,
        "agent_id": agent_id,
        "memory_ref": "v5/memory_palace/memory_palace.json",
        "active_version_id": memory.get("active_version_id", ""),
        "memory_hash": memory.get("memory_hash") or content_hash(memory.get("memory", {})),
        "agent_responsibility": specs[agent_id].get("responsibility", ""),
        "entries": {key: memory.get("memory", {}).get(key) for key in selected_keys if key in memory.get("memory", {})},
        "rules": [
            "Memory context is not scientific evidence.",
            "Scientific claims still require EvidenceItemRef and audited artifacts.",
            "Agent output must cite object/evidence/artifact refs, not memory-only claims.",
        ],
        "created_at": _now(),
    }
    out = memory_palace_dir(project_dir) / "agent_contexts" / f"{agent_id}.json"
    _write_json(out, context)
    append_memory_event(project_dir, "agent_memory_context_built", actor=agent_id, message="Built auditable agent memory context.", payload={"agent_id": agent_id, "memory_hash": context["memory_hash"]})
    return context


def diff_memory_versions(project_dir: Path, from_version_id: str, to_version_id: str, *, actor: str = "codex") -> dict[str, Any]:
    before = _load_memory_version(project_dir, from_version_id)
    after = _load_memory_version(project_dir, to_version_id)
    if not before:
        raise ValueError(f"unknown memory version: {from_version_id}")
    if not after:
        raise ValueError(f"unknown memory version: {to_version_id}")
    before_memory = before.get("memory", {})
    after_memory = after.get("memory", {})
    keys = sorted(set(before_memory) | set(after_memory))
    changes = []
    for key in keys:
        old = before_memory.get(key)
        new = after_memory.get(key)
        if old == new:
            continue
        if key not in before_memory:
            change_type = "added"
        elif key not in after_memory:
            change_type = "removed"
        else:
            change_type = "modified"
        changes.append(
            {
                "key": key,
                "change_type": change_type,
                "before_hash": content_hash(old),
                "after_hash": content_hash(new),
            }
        )
    payload = {
        "schema_version": "v5.memory_diff/0.1",
        "project_id": project_dir.name,
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "from_hash": before.get("memory_hash", ""),
        "to_hash": after.get("memory_hash", ""),
        "change_count": len(changes),
        "changes": changes,
        "created_at": _now(),
        "actor": actor,
    }
    _write_json(memory_palace_dir(project_dir) / "last_diff.json", payload)
    append_memory_event(project_dir, "memory_diff_built", actor=actor, message="Built memory version diff.", payload={"from_version_id": from_version_id, "to_version_id": to_version_id, "change_count": len(changes)})
    return payload


def run_memory_rollback_drill(project_dir: Path, *, actor: str = "codex") -> dict[str, Any]:
    memory = _ensure_versioned_memory(project_dir, actor=actor)
    versions = list_memory_versions(project_dir)
    if len(versions) < 2:
        update_memory_entry(project_dir, "rollback_drill_marker", {"created_at": _now(), "actor": actor}, actor=actor, reason="create rollback drill version")
        versions = list_memory_versions(project_dir)
    active_before = memory.get("active_version_id", "")
    target_version = versions[0]["version_id"]
    rolled = rollback_memory(project_dir, target_version, actor=actor, reason="rollback drill")
    restored = rollback_memory(project_dir, active_before or versions[-1]["version_id"], actor=actor, reason="restore after rollback drill")
    payload = {
        "schema_version": "v5.memory_rollback_drill/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if rolled.get("active_version_id") == target_version and restored.get("active_version_id") else "REVIEW",
        "rolled_back_to": target_version,
        "restored_to": restored.get("active_version_id", ""),
        "actor": actor,
        "created_at": _now(),
    }
    _write_json(memory_palace_dir(project_dir) / "rollback_drill.json", payload)
    append_memory_event(project_dir, "memory_rollback_drill", actor=actor, message="Executed memory rollback drill.", payload=payload)
    return payload


def run_memory_usage_scenarios(project_dir: Path, *, actor: str = "codex") -> dict[str, Any]:
    memory = _ensure_versioned_memory(project_dir, actor=actor)
    scenarios = [
        {
            "scenario_id": "agent_context_refresh",
            "purpose": "Build audited memory contexts for each canonical agent without treating memory as evidence.",
            "agent_ids": list(build_agent_specs().keys()),
        },
        {
            "scenario_id": "architecture_decision_update",
            "purpose": "Record a project architecture decision and verify it produces a new version and diff.",
            "memory_key": "latest_architecture_decision",
            "value": {
                "decision": "Use v5 canonical control plane and route real analysis through audited v4/v5 adapters.",
                "evidence_policy": "Scientific claims must remain in Evidence DB or ArtifactManifest refs.",
            },
        },
        {
            "scenario_id": "rollback_recovery",
            "purpose": "Exercise rollback and restoration to prove version reversibility.",
        },
        {
            "scenario_id": "scientific_boundary_check",
            "purpose": "Confirm memory carries only context/rules and not standalone scientific evidence.",
            "required_policy": memory.get("scientific_evidence_policy", ""),
        },
    ]
    context_refs = []
    for agent_id in scenarios[0]["agent_ids"]:
        context = build_agent_memory_context(project_dir, agent_id)
        context_refs.append(f"v5/memory_palace/agent_contexts/{agent_id}.json#{context.get('memory_hash', '')[:12]}")
    before_versions = list_memory_versions(project_dir)
    updated = update_memory_entry(project_dir, scenarios[1]["memory_key"], scenarios[1]["value"], actor=actor, reason="memory usage scenario")
    after_versions = list_memory_versions(project_dir)
    diff = {}
    if len(after_versions) >= 2:
        diff = diff_memory_versions(project_dir, after_versions[-2]["version_id"], after_versions[-1]["version_id"], actor=actor)
    rollback = run_memory_rollback_drill(project_dir, actor=actor)
    current = load_memory_palace(project_dir)
    policy_ok = "Evidence DB" in str(current.get("scientific_evidence_policy", "")) and "scientific" in str(current.get("scope", ""))
    payload = {
        "schema_version": "v5.memory_usage_scenarios/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if context_refs and updated.get("active_version_id") and rollback.get("status") == "PASS" and policy_ok else "REVIEW",
        "scenarios": scenarios,
        "agent_context_refs": context_refs,
        "version_count_before": len(before_versions),
        "version_count_after": len(after_versions),
        "updated_version_id": updated.get("active_version_id", ""),
        "diff_ref": "v5/memory_palace/last_diff.json" if diff else "",
        "rollback_drill_ref": "v5/memory_palace/rollback_drill.json",
        "scientific_boundary_ok": policy_ok,
        "created_at": _now(),
        "actor": actor,
    }
    _write_json(memory_palace_dir(project_dir) / "usage_scenarios.json", payload)
    append_memory_event(project_dir, "memory_usage_scenarios", actor=actor, message="Executed memory usage scenarios.", payload=payload)
    build_memory_audit_dashboard(project_dir, actor=actor)
    return payload


def build_memory_audit_dashboard(project_dir: Path, *, actor: str = "codex") -> dict[str, Any]:
    memory = _ensure_versioned_memory(project_dir, actor=actor)
    versions = list_memory_versions(project_dir)
    if len(versions) >= 2:
        diff = diff_memory_versions(project_dir, versions[-2]["version_id"], versions[-1]["version_id"], actor=actor)
    else:
        diff = {}
    events = []
    path = memory_events_path(project_dir)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    payload = {
        "schema_version": "v5.memory_audit_dashboard/0.1",
        "project_id": project_dir.name,
        "status": "READY" if memory.get("active_version_id") else "REVIEW",
        "memory_ref": "v5/memory_palace/memory_palace.json",
        "active_version_id": memory.get("active_version_id", ""),
        "memory_hash": memory.get("memory_hash", ""),
        "version_count": len(versions),
        "versions": versions[-20:],
        "event_count": len(events),
        "recent_events": events[-30:],
        "last_diff": diff,
        "rollback_drill": _read_json(memory_palace_dir(project_dir) / "rollback_drill.json", {}),
        "usage_scenarios": _read_json(memory_palace_dir(project_dir) / "usage_scenarios.json", {}),
        "scientific_evidence_policy": memory.get("scientific_evidence_policy", ""),
        "created_at": _now(),
    }
    _write_json(memory_palace_dir(project_dir) / "memory_audit_dashboard.json", payload)
    append_memory_event(project_dir, "memory_audit_dashboard_built", actor=actor, message="Built memory audit dashboard.", payload={"version_count": len(versions), "event_count": len(events)})
    return payload


def _ensure_versioned_memory(project_dir: Path, *, actor: str) -> dict[str, Any]:
    memory = load_memory_palace(project_dir) or install_pilotdeck_memory(project_dir, actor=actor)
    if memory.get("active_version_id") and memory.get("memory_hash"):
        return memory
    version = _write_memory_version(project_dir, memory.get("memory", {}), actor=actor, reason="migrate legacy memory manifest", previous_hash="")
    memory["active_version_id"] = version["version_id"]
    memory["memory_hash"] = version["memory_hash"]
    memory["updated_at"] = _now()
    memory["updated_by"] = actor
    _write_json(memory_manifest_path(project_dir), memory)
    append_memory_event(project_dir, "memory_manifest_versioned", actor=actor, message="Added active version metadata to legacy memory manifest.", payload={"version_id": version["version_id"]})
    return memory


def append_memory_event(project_dir: Path, event_type: str, actor: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    event_payload = payload or {}
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _stable_id("memory_event", project_dir.name, event_type, actor, message, event_payload, _now()),
        "project_id": project_dir.name,
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "payload": event_payload,
        "payload_hash": content_hash(event_payload),
        "created_at": _now(),
    }
    path = memory_events_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def memory_palace_dir(project_dir: Path) -> Path:
    path = project_dir / "v5" / "memory_palace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_manifest_path(project_dir: Path) -> Path:
    return memory_palace_dir(project_dir) / "memory_palace.json"


def memory_events_path(project_dir: Path) -> Path:
    return memory_palace_dir(project_dir) / "events.jsonl"


def _write_memory_version(project_dir: Path, memory: dict[str, Any], *, actor: str, reason: str, previous_hash: str) -> dict[str, Any]:
    memory_hash = content_hash(memory)
    payload = {
        "schema_version": "v5.memory_version/0.1",
        "project_id": project_dir.name,
        "version_id": _stable_id("memory_version", project_dir.name, memory_hash, _now()),
        "actor": actor,
        "reason": reason,
        "previous_hash": previous_hash,
        "memory_hash": memory_hash,
        "memory": memory,
        "created_at": _now(),
    }
    _write_json(memory_palace_dir(project_dir) / "versions" / f"{payload['version_id']}.json", payload)
    return payload


def _load_memory_version(project_dir: Path, version_id: str) -> dict[str, Any]:
    path = memory_palace_dir(project_dir) / "versions" / f"{version_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    project_dir = _project_dir_from_v5_path(path)
    write_json_artifact(project_dir, path.relative_to(project_dir), payload, producer="memory_palace", artifact_type="memory_json")


def _project_dir_from_v5_path(path: Path) -> Path:
    parts = path.parts
    if "v5" in parts:
        return Path(*parts[: parts.index("v5")])
    return path.parent


def _stable_id(prefix: str, *parts: Any) -> str:
    return prefix + "_" + content_hash(parts)[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
