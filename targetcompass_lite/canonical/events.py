from __future__ import annotations

from typing import Any

from .ids import hash_payload, make_stable_id
from .schemas import CANONICAL_SCHEMA_VERSION, now_iso


def build_event(
    *,
    project_id: str,
    event_type: str,
    actor: str,
    previous_stage: str,
    next_stage: str,
    object_refs: list[dict[str, Any]] | None = None,
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_data = payload or {}
    created_at = now_iso()
    payload_hash = hash_payload(
        {
            "project_id": project_id,
            "event_type": event_type,
            "actor": actor,
            "previous_stage": previous_stage,
            "next_stage": next_stage,
            "object_refs": object_refs or [],
            "message": message,
            "payload": payload_data,
        }
    )
    event_id = make_stable_id(
        "event",
        {
            "project_id": project_id,
            "event_type": event_type,
            "created_at": created_at,
            "payload_hash": payload_hash,
        },
    )
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "event_id": event_id,
        "project_id": project_id,
        "event_type": event_type,
        "actor": actor,
        "created_at": created_at,
        "previous_stage": previous_stage,
        "next_stage": next_stage,
        "object_refs": object_refs or [],
        "message": message,
        "payload_hash": payload_hash,
        "payload": payload_data,
    }
