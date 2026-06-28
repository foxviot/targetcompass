from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agent_protocol import enforce_claim_ceiling, validate_agent_handoff
from .handoff import build_handoff
from .ids import make_stable_id
from .schemas import EvidencePlan, ResourceCandidate, ResearchSpec, ScopeBundle, SubQuestion
from .store import init_project_state, load_events, load_project_state, transition_state
from .task_packets import validate_task_packets
from .workflow_compiler import compile_mock_workflow


def run_mock_canonical_pipeline(project_dir: str | Path, user_question: str) -> dict[str, Any]:
    project_dir = Path(project_dir)
    project_id = project_dir.name
    init_project_state(project_dir, user_question)

    research_spec, subquestions = _run_question_normalizer(project_id, user_question)
    research_spec_ref = _write_object(project_dir, research_spec["research_spec_id"], research_spec)
    subquestion_refs = [_write_object(project_dir, item["subquestion_id"], item) for item in subquestions]
    _transition_and_handoff(
        project_dir,
        "QUESTION_RESOLVED",
        "QUESTION_NORMALIZED",
        "question_normalizer",
        "scope_resolver",
        input_refs=[{"object_type": "UserQuestion", "object_id": "user_question"}],
        output_refs=[research_spec_ref, *subquestion_refs],
        previous_ceiling="association",
        next_ceiling="descriptive",
        reason="Natural language question only; no empirical evidence yet.",
    )

    scope_bundle = _run_scope_resolver(research_spec, subquestions)
    scope_ref = _write_object(project_dir, scope_bundle["scope_bundle_id"], scope_bundle)
    _transition_and_handoff(
        project_dir,
        "SCOPE_RESOLVED",
        "SCOPE_RESOLVED",
        "scope_resolver",
        "evidence_plan_builder",
        input_refs=[research_spec_ref, *subquestion_refs],
        output_refs=[scope_ref],
        previous_ceiling="descriptive",
        next_ceiling="descriptive",
        reason="Scope resolution does not add empirical evidence.",
        open_questions=["Species, tissue, and disease labels are deterministic mock assumptions until real normalization is enabled."],
    )

    evidence_plan = _run_evidence_plan_builder(research_spec, scope_bundle)
    evidence_ref = _write_object(project_dir, evidence_plan["evidence_plan_id"], evidence_plan)
    _transition_and_handoff(
        project_dir,
        "EVIDENCE_PLANNED",
        "EVIDENCE_PLANNED",
        "evidence_plan_builder",
        "resource_discovery_agent",
        input_refs=[research_spec_ref, scope_ref],
        output_refs=[evidence_ref],
        previous_ceiling="descriptive",
        next_ceiling="descriptive",
        reason="Evidence planning defines required axes but does not create evidence.",
    )

    resource_candidates = _run_resource_discovery_agent(project_id, evidence_plan)
    resource_bundle = {
        "schema_version": "v5.canonical/0.1",
        "resource_candidates_bundle_id": make_stable_id("resource_candidates", {"project_id": project_id, "evidence_plan_id": evidence_plan["evidence_plan_id"]}),
        "project_id": project_id,
        "resource_candidates": resource_candidates,
        "verified_dataset_count": 0,
        "status": "mock_placeholder_only",
    }
    resource_ref = _write_object(project_dir, resource_bundle["resource_candidates_bundle_id"], resource_bundle)
    _transition_and_handoff(
        project_dir,
        "RESOURCES_DISCOVERED",
        "RESOURCES_DISCOVERED",
        "resource_discovery_agent",
        "method_adapter_workorder_compiler",
        input_refs=[evidence_ref],
        output_refs=[resource_ref],
        previous_ceiling="descriptive",
        next_ceiling="descriptive",
        reason="Mock resource discovery cannot verify datasets or raise claim level.",
        assumptions=["All resource candidates are placeholders and must not be locked."],
        open_questions=["Real metadata discovery and dataset verification are required before DATASETS_LOCKED."],
    )

    workflow_plan, task_packets = compile_mock_workflow(
        project_id=project_id,
        subquestions=subquestions,
        evidence_plan=evidence_plan,
        resource_candidates=resource_candidates,
    )
    task_errors = validate_task_packets(task_packets)
    if task_errors:
        raise ValueError("; ".join(task_errors))
    workflow_ref = _write_object(project_dir, workflow_plan["workflow_plan_id"], workflow_plan)
    task_bundle = {
        "schema_version": "v5.canonical/0.1",
        "task_packets_bundle_id": make_stable_id("task_packets", {"project_id": project_id, "task_ids": [item["task_id"] for item in task_packets]}),
        "project_id": project_id,
        "task_packets": task_packets,
        "engineering_task_packet_count": len([item for item in task_packets if item.get("packet_type") == "EngineeringTaskPacket"]),
        "analysis_executed": False,
        "status": "ready_for_human_review",
    }
    task_ref = _write_object(project_dir, task_bundle["task_packets_bundle_id"], task_bundle)
    _transition_and_handoff(
        project_dir,
        "WORKFLOW_COMPILED",
        "WORKFLOW_COMPILED",
        "method_adapter_workorder_compiler",
        "result_auditor",
        input_refs=[evidence_ref, resource_ref],
        output_refs=[workflow_ref, task_ref],
        previous_ceiling="descriptive",
        next_ceiling="descriptive",
        reason="Workflow compilation produces task packets only; no biological result is generated.",
    )
    final_state = transition_state(
        project_dir,
        "TASKS_READY",
        "TASKS_READY",
        "method_adapter_workorder_compiler",
        [workflow_ref, task_ref],
        "Canonical mock pipeline stopped at task packets; human approval and real data are required before execution.",
    )

    return {
        "project_id": project_id,
        "project_state": final_state,
        "research_spec": research_spec,
        "subquestions": subquestions,
        "scope_bundle": scope_bundle,
        "evidence_plan": evidence_plan,
        "resource_candidates": resource_candidates,
        "workflow_plan": workflow_plan,
        "task_packets": task_packets,
        "events": load_events(project_dir),
        "handoffs": _load_handoff_files(project_dir),
    }


def _run_question_normalizer(project_id: str, user_question: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    research_spec = ResearchSpec(project_id=project_id, research_question=user_question, status="resolved", max_claim_level="association").to_dict()
    subquestion = SubQuestion(
        research_spec_id=research_spec["research_spec_id"],
        question="What datasets and methods would be needed to answer the research question without exceeding association-level claims?",
        status="resolved",
    ).to_dict()
    return research_spec, [subquestion]


def _run_scope_resolver(research_spec: dict[str, Any], subquestions: list[dict[str, Any]]) -> dict[str, Any]:
    scope = ScopeBundle(
        research_spec_id=research_spec["research_spec_id"],
        species=["human"],
        tissues=["unspecified_tissue_from_user_question"],
        conditions=["condition_from_user_question"],
        status="resolved",
    )
    data = asdict(scope)
    data["scope_bundle_id"] = make_stable_id("scope_bundle", {"research_spec_id": research_spec["research_spec_id"], "subquestion_ids": [item["subquestion_id"] for item in subquestions]})
    data["modality_preferences"] = ["bulk_rna", "scrna", "literature_metadata"]
    return data


def _run_evidence_plan_builder(research_spec: dict[str, Any], scope_bundle: dict[str, Any]) -> dict[str, Any]:
    plan = EvidencePlan(
        research_spec_id=research_spec["research_spec_id"],
        evidence_axes=["dataset_feasibility", "method_compatibility", "qc_readiness", "claim_ceiling"],
        max_claim_level="association",
        status="planned",
    )
    data = asdict(plan)
    data["evidence_plan_id"] = make_stable_id("evidence_plan", {"research_spec_id": research_spec["research_spec_id"], "scope_bundle_id": scope_bundle["scope_bundle_id"]})
    data["requires_human_review_before_execution"] = True
    return data


def _run_resource_discovery_agent(project_id: str, evidence_plan: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for name in ["AUTO_GEO_METADATA_SEARCH", "AUTO_LITERATURE_SEARCH"]:
        candidate = ResourceCandidate(
            resource_name=name,
            resource_type="dataset_candidate" if "GEO" in name else "literature_candidate",
            verified=False,
            source_status="mock_placeholder",
            status="candidate",
            accession=name,
        )
        data = asdict(candidate)
        data["project_id"] = project_id
        data["evidence_plan_id"] = evidence_plan["evidence_plan_id"]
        data["resource_candidate_id"] = make_stable_id("resource_candidate", {"project_id": project_id, "resource_name": name, "source_status": "mock_placeholder"})
        candidates.append(data)
    return candidates


def _transition_and_handoff(
    project_dir: Path,
    next_stage: str,
    event_type: str,
    from_agent: str,
    to_agent: str,
    input_refs: list[dict[str, Any]],
    output_refs: list[dict[str, Any]],
    previous_ceiling: str,
    next_ceiling: str,
    reason: str,
    assumptions: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> None:
    ceiling_errors = enforce_claim_ceiling(previous_ceiling, next_ceiling)
    if ceiling_errors:
        raise ValueError("; ".join(ceiling_errors))
    handoff = build_handoff(
        project_id=project_dir.name,
        from_agent=from_agent,
        to_agent=to_agent,
        input_object_refs=input_refs,
        output_object_refs=output_refs,
        assumptions=assumptions or [],
        open_questions=open_questions or [],
        max_allowed_claim=next_ceiling,
        claim_ceiling_reason=reason,
    )
    validation = validate_agent_handoff(handoff, from_agent, to_agent)
    if validation["status"] == "invalid":
        raise ValueError("; ".join(validation["errors"]))
    _write_handoff_file(project_dir, handoff)
    transition_state(project_dir, next_stage, event_type, from_agent, output_refs, f"{from_agent} completed {event_type}.")


def _write_object(project_dir: Path, object_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = project_dir / "v5" / "objects" / f"{object_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"object_type": _object_type_from_id(object_id), "object_id": object_id, "path": str(path.relative_to(project_dir))}


def _write_handoff_file(project_dir: Path, handoff: dict[str, Any]) -> dict[str, Any]:
    path = project_dir / "v5" / "handoffs" / f"{handoff['handoff_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"handoff_id": handoff["handoff_id"], "path": str(path.relative_to(project_dir))}


def _load_handoff_files(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / "v5" / "handoffs"
    if not path.exists():
        return []
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]


def _object_type_from_id(object_id: str) -> str:
    return "".join(part.capitalize() for part in object_id.split("_")[:-1]) or "CanonicalObject"
