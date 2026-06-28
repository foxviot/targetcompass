from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .ids import make_stable_id
from .schemas import CANONICAL_SCHEMA_VERSION, ProjectState, now_iso


STAGES = [
    "INTAKE",
    "QUESTION_RESOLVED",
    "SCOPE_RESOLVED",
    "EVIDENCE_PLANNED",
    "RESOURCES_DISCOVERED",
    "DATASETS_LOCKED",
    "WORKFLOW_COMPILED",
    "TASKS_READY",
    "TASKS_RUNNING",
    "QC_COMPLETED",
    "EVIDENCE_SYNTHESIZED",
    "ALIGNMENT_AUDITED",
    "REPORT_READY",
    "HUMAN_REVIEW_REQUIRED",
    "FAILED",
    "CANCELLED",
]

TERMINAL_STAGES = {"FAILED", "CANCELLED"}

LINEAR_NEXT = {
    "INTAKE": ["QUESTION_RESOLVED", "FAILED", "CANCELLED"],
    "QUESTION_RESOLVED": ["SCOPE_RESOLVED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "SCOPE_RESOLVED": ["EVIDENCE_PLANNED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "EVIDENCE_PLANNED": ["RESOURCES_DISCOVERED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "RESOURCES_DISCOVERED": ["DATASETS_LOCKED", "WORKFLOW_COMPILED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "DATASETS_LOCKED": ["WORKFLOW_COMPILED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "WORKFLOW_COMPILED": ["TASKS_READY", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "TASKS_READY": ["TASKS_RUNNING", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "TASKS_RUNNING": ["QC_COMPLETED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "QC_COMPLETED": ["EVIDENCE_SYNTHESIZED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "EVIDENCE_SYNTHESIZED": ["ALIGNMENT_AUDITED", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "ALIGNMENT_AUDITED": ["REPORT_READY", "HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "REPORT_READY": ["HUMAN_REVIEW_REQUIRED", "FAILED", "CANCELLED"],
    "HUMAN_REVIEW_REQUIRED": ["QUESTION_RESOLVED", "SCOPE_RESOLVED", "EVIDENCE_PLANNED", "RESOURCES_DISCOVERED", "DATASETS_LOCKED", "WORKFLOW_COMPILED", "TASKS_READY", "TASKS_RUNNING", "QC_COMPLETED", "EVIDENCE_SYNTHESIZED", "ALIGNMENT_AUDITED", "REPORT_READY", "FAILED", "CANCELLED"],
    "FAILED": [],
    "CANCELLED": [],
}


def allowed_next_stages(stage: str) -> list[str]:
    return list(LINEAR_NEXT.get(stage, []))


def build_initial_state(project_id: str, user_question: str) -> dict[str, Any]:
    state = ProjectState(project_id=project_id, current_stage="INTAKE", allowed_next_stages=allowed_next_stages("INTAKE"))
    data = asdict(state)
    data["project_state_id"] = make_stable_id("project_state", {"project_id": project_id})
    data["schema_version"] = CANONICAL_SCHEMA_VERSION
    data["updated_at"] = now_iso()
    data["user_question"] = user_question
    data["stage_history"] = ["INTAKE"]
    return data


def with_stage(state: dict[str, Any], next_stage: str) -> dict[str, Any]:
    updated = dict(state)
    updated["current_stage"] = next_stage
    updated["allowed_next_stages"] = allowed_next_stages(next_stage)
    updated["updated_at"] = now_iso()
    history = list(updated.get("stage_history") or [])
    history.append(next_stage)
    updated["stage_history"] = history
    return updated
