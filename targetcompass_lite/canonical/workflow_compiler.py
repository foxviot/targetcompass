from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .ids import make_stable_id
from .schemas import WorkflowPlan
from .task_packets import build_analysis_task_packet, build_review_task_packet


def compile_mock_workflow(
    *,
    project_id: str,
    subquestions: list[dict[str, Any]],
    evidence_plan: dict[str, Any],
    resource_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task_packets: list[dict[str, Any]] = []
    for subquestion in subquestions:
        subquestion_id = subquestion["subquestion_id"]
        task_packets.append(
            build_analysis_task_packet(
                subquestion_id=subquestion_id,
                method_name="registered_mock_metadata_screen",
                expected_inputs=["research_spec", "scope_bundle", "evidence_plan", "resource_candidates"],
                expected_outputs=["dataset_feasibility_report", "method_compatibility_decision"],
                qc_requirements=["no_verified_dataset_without_real_accession", "no_biological_claim_from_mock_data"],
                failure_conditions=["all_resource_candidates_unverified", "missing_scope_bundle", "missing_evidence_plan"],
            )
        )
        task_packets.append(
            build_review_task_packet(
                subquestion_id=subquestion_id,
                audit_scope=["resource_candidates", "workflow_plan", "analysis_task_packets"],
                claim_ceiling=evidence_plan.get("max_claim_level", "association"),
                required_checks=["claim_ceiling_not_loosened", "no_mock_dataset_locked", "analysis_not_executed"],
            )
        )
    workflow_id = make_stable_id(
        "workflow_plan",
        {
            "project_id": project_id,
            "subquestion_ids": [item["subquestion_id"] for item in subquestions],
            "resource_candidate_ids": [item["resource_candidate_id"] for item in resource_candidates],
            "task_ids": [item["task_id"] for item in task_packets],
        },
    )
    workflow = WorkflowPlan(
        workflow_name="canonical_mock_task_packet_workflow",
        task_ids=[item["task_id"] for item in task_packets],
        workflow_plan_id=workflow_id,
        status="compiled",
    )
    data = asdict(workflow)
    data["project_id"] = project_id
    data["execution_mode"] = "mock_control_plane_only"
    data["analysis_executed"] = False
    data["resource_candidate_ids"] = [item["resource_candidate_id"] for item in resource_candidates]
    return data, task_packets
