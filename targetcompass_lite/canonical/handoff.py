from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_protocol import HANDOFF_SCHEMA_VERSION
from .ids import hash_payload, make_stable_id
from .schemas import now_iso


def build_handoff(
    *,
    project_id: str,
    from_agent: str,
    to_agent: str,
    input_object_refs: list[dict[str, Any]] | None = None,
    output_object_refs: list[dict[str, Any]] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    assumptions: list[str] | None = None,
    open_questions: list[str] | None = None,
    blocking_issues: list[str] | None = None,
    max_allowed_claim: str = "association",
    claim_ceiling_reason: str = "",
    audit_notes: list[str] | None = None,
) -> dict[str, Any]:
    created_at = now_iso()
    base = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "project_id": project_id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "created_at": created_at,
        "input_object_refs": input_object_refs or [],
        "output_object_refs": output_object_refs or [],
        "evidence_refs": evidence_refs or [],
        "artifact_refs": artifact_refs or [],
        "assumptions": assumptions or [],
        "open_questions": open_questions or [],
        "blocking_issues": blocking_issues or [],
        "claim_ceiling": {
            "max_allowed_claim": max_allowed_claim,
            "reason": claim_ceiling_reason,
        },
        "audit_notes": audit_notes or [],
    }
    payload_hash = hash_payload(base)
    base["payload_hash"] = payload_hash
    base["handoff_id"] = make_stable_id(
        "handoff",
        {
            "project_id": project_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "created_at": created_at,
            "payload_hash": payload_hash,
        },
    )
    return base


def write_handoff(project_dir: str | Path, handoff: dict[str, Any]) -> dict[str, Any]:
    path = Path(project_dir) / "v5" / "handoffs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(handoff, ensure_ascii=False, sort_keys=True) + "\n")
    return handoff


def load_handoffs(project_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(project_dir) / "v5" / "handoffs.jsonl"
    if not path.exists():
        return []
    handoffs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            handoffs.append(json.loads(line))
    return handoffs
