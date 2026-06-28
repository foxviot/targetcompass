from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import build_event
from .state import STAGES, TERMINAL_STAGES, allowed_next_stages, build_initial_state, with_stage


def _v5_dir(project_dir: str | Path) -> Path:
    path = Path(project_dir) / "v5"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(project_dir: str | Path) -> Path:
    return _v5_dir(project_dir) / "project_state.json"


def _events_path(project_dir: str | Path) -> Path:
    return _v5_dir(project_dir) / "events.jsonl"


def init_project_state(project_dir: str | Path, user_question: str) -> dict[str, Any]:
    project_id = Path(project_dir).name
    state = build_initial_state(project_id=project_id, user_question=user_question)
    _write_state(project_dir, state)
    event = build_event(
        project_id=project_id,
        event_type="PROJECT_STATE_INITIALIZED",
        actor="system",
        previous_stage="",
        next_stage="INTAKE",
        object_refs=[{"object_type": "ProjectState", "object_id": state["project_state_id"]}],
        message="Initialized canonical v5 project state.",
        payload={"user_question": user_question},
    )
    append_event(project_dir, event)
    return state


def append_event(project_dir: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    required = ["event_id", "project_id", "event_type", "actor", "created_at", "previous_stage", "next_stage", "object_refs", "message", "payload_hash"]
    missing = [field for field in required if field not in event]
    if missing:
        raise ValueError(f"event missing required fields: {', '.join(missing)}")
    path = _events_path(project_dir)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def transition_state(
    project_dir: str | Path,
    next_stage: str,
    event_type: str,
    actor: str,
    object_refs: list[dict[str, Any]] | None,
    message: str,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    state = load_project_state(project_dir)
    previous_stage = state["current_stage"]
    validate_transition(previous_stage, next_stage, resume=resume)
    if previous_stage == "TASKS_RUNNING" and next_stage == "EVIDENCE_SYNTHESIZED":
        events = load_events(project_dir)
        if not any(event.get("next_stage") == "QC_COMPLETED" or event.get("event_type") == "QC_COMPLETED" for event in events):
            raise ValueError("TASKS_RUNNING cannot transition to EVIDENCE_SYNTHESIZED before QC_COMPLETED event")
    updated = with_stage(state, next_stage)
    _write_state(project_dir, updated)
    event = build_event(
        project_id=updated["project_id"],
        event_type=event_type,
        actor=actor,
        previous_stage=previous_stage,
        next_stage=next_stage,
        object_refs=object_refs or [],
        message=message,
        payload={"project_state_id": updated["project_state_id"]},
    )
    append_event(project_dir, event)
    return updated


def load_project_state(project_dir: str | Path) -> dict[str, Any]:
    path = _state_path(project_dir)
    if not path.exists():
        raise FileNotFoundError(f"project_state.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(project_dir: str | Path) -> list[dict[str, Any]]:
    path = _events_path(project_dir)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def validate_transition(previous: str, next: str, *, resume: bool = False) -> None:
    if previous not in STAGES:
        raise ValueError(f"unknown previous stage: {previous}")
    if next not in STAGES:
        raise ValueError(f"unknown next stage: {next}")
    if previous in TERMINAL_STAGES and not resume:
        raise ValueError(f"{previous} is terminal; explicit resume is required")
    if next not in allowed_next_stages(previous):
        raise ValueError(f"illegal transition: {previous} -> {next}")


def _write_state(project_dir: str | Path, state: dict[str, Any]) -> None:
    path = _state_path(project_dir)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
