from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_specs import build_agent_specs
from .alignment_auditor import audit_question_alignment
from .artifacts import load_artifact_registry, register_artifact
from .backend_writer import write_json_artifact
from .backend_access import backend_status_summary, load_artifact_registry_preferred
from .ids import make_stable_id
from .nextflow_execution import load_qc_reports, load_task_runs
from .schemas import CANONICAL_SCHEMA_VERSION, now_iso


REPORT_MANIFEST_SCHEMA_VERSION = "v5.canonical_report_manifest/0.1"


def build_canonical_report_manifest(project_dir: str | Path, *, register: bool = True) -> dict[str, Any]:
    project_dir = Path(project_dir)
    state = _read_json(project_dir / "v5" / "project_state.json", {})
    research_spec_ref, research_spec = _latest_object(project_dir, "research_spec_*.json")
    subquestion_refs = _object_refs(project_dir, "subquestion_*.json")
    subquestions = [_read_json(project_dir / ref["path"], {}) for ref in subquestion_refs]
    scope_ref, scope_bundle = _latest_object(project_dir, "scope_bundle_*.json")
    evidence_plan_ref, evidence_plan = _latest_object(project_dir, "evidence_plan_*.json")
    workflow_ref, workflow_plan = _latest_object(project_dir, "workflow_plan_*.json")
    task_packet_refs = _object_refs(project_dir, "task_packets_*.json")
    task_packet_refs.extend(_existing_refs(project_dir, ["v5/task_packets/registered_analysis_task_packets.json"]))
    artifact_query = load_artifact_registry_preferred(project_dir)
    artifact_registry = artifact_query["artifacts"]
    qc_reports = load_qc_reports(project_dir)
    task_runs = load_task_runs(project_dir)
    handoffs = _load_all_handoffs(project_dir)
    alignment_ref, alignment_report = _ensure_alignment_report(
        project_dir,
        research_spec,
        subquestions,
        scope_bundle,
        artifact_registry,
        qc_reports,
        evidence_plan,
    )
    report_paths = _existing_refs(
        project_dir,
        [
            "reports/target_report.html",
            "reports/target_report.docx",
            "reports/target_report_structured.json",
        ],
    )
    failed_qc = [row for row in qc_reports if str(row.get("overall_status", "")).lower() not in {"pass", "passed"}]
    missing_reports = [row["path"] for row in report_paths if not row.get("exists")]
    review_required = (
        bool(failed_qc)
        or alignment_report.get("final_decision") != "approve"
        or not report_paths
        or bool(missing_reports)
    )
    manifest = {
        "schema_version": REPORT_MANIFEST_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "report_manifest_id": make_stable_id(
            "canonical_report_manifest",
            {
                "project_id": project_dir.name,
                "state": state.get("current_stage", ""),
                "reports": [row["path"] for row in report_paths],
                "artifact_count": len(artifact_registry),
                "qc_count": len(qc_reports),
                "alignment": alignment_report.get("report_id", ""),
            },
        ),
        "created_at": now_iso(),
        "project_state_ref": _ref(project_dir, "v5/project_state.json", "ProjectState"),
        "research_spec_ref": research_spec_ref,
        "scope_bundle_ref": scope_ref,
        "evidence_plan_ref": evidence_plan_ref,
        "workflow_plan_ref": workflow_ref,
        "task_packet_refs": task_packet_refs,
        "task_run_refs": _task_run_refs(project_dir, task_runs),
        "qc_report_refs": _qc_refs(project_dir, qc_reports),
        "artifact_manifest_refs": _artifact_refs(artifact_registry),
        "backend_preference": {
            "source": artifact_query.get("source", "local_filesystem"),
            "backend_status": artifact_query.get("backend_status", "FALLBACK"),
            "active_backends": artifact_query.get("active_backends", {}),
            "ref": artifact_query.get("backend_preference_ref", ""),
        },
        "question_alignment_report_ref": alignment_ref,
        "handoff_refs": _handoff_refs(project_dir, handoffs),
        "report_outputs": report_paths,
        "claim_ceiling": {
            "max_allowed_claim": evidence_plan.get("max_claim_level") or research_spec.get("max_claim_level") or "association",
            "source": evidence_plan_ref.get("path") or research_spec_ref.get("path") or "default",
        },
        "human_review_gate": {
            "required": review_required,
            "reason": _review_reason(review_required, failed_qc, alignment_report, report_paths),
        },
        "consistency_checks": _consistency_checks(report_paths, artifact_registry, qc_reports, alignment_report),
        "status": "review_required" if review_required else "ready_for_signoff",
    }
    out = project_dir / "v5" / "reports" / "canonical_report_manifest.json"
    _write_json(out, manifest)
    if register:
        artifact = register_artifact(
            project_dir,
            out.relative_to(project_dir),
            producer="evidence_synthesizer_reporter",
            artifact_type="canonical_report_manifest",
            expected_by_task_ids=["v5_report_manifest"],
            supports_subquestion_ids=[row.get("object_id", "") for row in subquestion_refs],
            producer_run_id=manifest["report_manifest_id"],
            qc_status="pass",
            limitations=["Manifest links report outputs and evidence controls; it does not by itself approve scientific claims."],
        )
        manifest["artifact_id"] = artifact["artifact_id"]
        _write_json(out, manifest)
    return manifest


def build_canonical_flow_view(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    state = _read_json(project_dir / "v5" / "project_state.json", {})
    handoffs = _load_all_handoffs(project_dir)
    task_runs = load_task_runs(project_dir)
    qc_reports = load_qc_reports(project_dir)
    artifact_query = load_artifact_registry_preferred(project_dir)
    artifact_registry = artifact_query["artifacts"]
    report_manifest = _read_json(project_dir / "v5" / "reports" / "canonical_report_manifest.json", {})
    handoff_by_from = {row.get("from_agent", ""): row for row in handoffs}
    specs = build_agent_specs()
    flow = []
    for agent_id, spec in specs.items():
        handoff = handoff_by_from.get(agent_id, {})
        flow.append(
            {
                "agent_id": agent_id,
                "display_name": spec.get("display_name", agent_id),
                "responsibility": spec.get("responsibility", ""),
                "input_refs": spec.get("required_input_refs", []),
                "output_refs": spec.get("required_output_refs", []),
                "handoff_id": handoff.get("handoff_id", ""),
                "to_agent": handoff.get("to_agent", spec.get("handoff_contract", {}).get("to_agent")),
                "claim_ceiling": (handoff.get("claim_ceiling") or {}).get("max_allowed_claim") or spec.get("max_claim_level", ""),
                "blocking_issues": handoff.get("blocking_issues", []),
                "human_gate": _agent_gate(agent_id, task_runs, qc_reports, artifact_registry, report_manifest),
            }
        )
    return {
        "schema_version": "v5.canonical_flow_view/0.1",
        "project_id": project_dir.name,
        "current_stage": state.get("current_stage", "not_initialized"),
        "flow": flow,
        "task_run_count": len(task_runs),
        "qc_report_count": len(qc_reports),
        "artifact_count": len(artifact_registry),
        "backend_preference": backend_status_summary(project_dir),
        "report_manifest_ref": "v5/reports/canonical_report_manifest.json" if report_manifest else "",
        "human_review_required": (report_manifest.get("human_review_gate") or {}).get("required", True),
    }


def _ensure_alignment_report(
    project_dir: Path,
    research_spec: dict[str, Any],
    subquestions: list[dict[str, Any]],
    scope_bundle: dict[str, Any],
    artifact_registry: list[dict[str, Any]],
    qc_reports: list[dict[str, Any]],
    evidence_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = _read_json(project_dir / "v5" / "reports" / "question_alignment_report.json", {})
    if existing:
        return _ref(project_dir, "v5/reports/question_alignment_report.json", "QuestionAlignmentReport", existing.get("report_id", "")), existing
    if not research_spec:
        research_spec = {"project_id": project_dir.name, "research_spec_id": "", "max_claim_level": "association"}
    if not scope_bundle:
        scope_bundle = {"species": [], "tissues": [], "conditions": []}
    if not subquestions:
        subquestions = [{"subquestion_id": "sq_unresolved", "question": "No canonical subquestion found.", "unresolved_reason": "Canonical question_normalizer output is missing."}]
    for subquestion in subquestions:
        subquestion.setdefault("unresolved_reason", "No audited v5 claim has been synthesized yet.")
    report = audit_question_alignment(
        research_spec=research_spec,
        subquestions=subquestions,
        scope_bundle=scope_bundle,
        evidence_item_refs=[],
        claims=[],
        artifact_manifests=artifact_registry,
        qc_reports=qc_reports,
        max_claim_level=evidence_plan.get("max_claim_level") if evidence_plan else None,
    )
    path = project_dir / "v5" / "reports" / "question_alignment_report.json"
    _write_json(path, report)
    return _ref(project_dir, "v5/reports/question_alignment_report.json", "QuestionAlignmentReport", report.get("report_id", "")), report


def _latest_object(project_dir: Path, pattern: str) -> tuple[dict[str, Any], dict[str, Any]]:
    refs = _object_refs(project_dir, pattern)
    if not refs:
        return {}, {}
    ref = refs[-1]
    return ref, _read_json(project_dir / ref["path"], {})


def _object_refs(project_dir: Path, pattern: str) -> list[dict[str, Any]]:
    out = []
    for path in sorted((project_dir / "v5" / "objects").glob(pattern)):
        data = _read_json(path, {})
        out.append(_ref(project_dir, path.relative_to(project_dir), _object_type_from_name(path.name), _object_id(data)))
    return out


def _existing_refs(project_dir: Path, paths: list[str]) -> list[dict[str, Any]]:
    return [_ref(project_dir, path, _object_type_from_path(path), "", exists=(project_dir / path).exists()) for path in paths if (project_dir / path).exists()]


def _task_run_refs(project_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_ref(project_dir, f"v5/task_runs/{row.get('task_run_id', '')}.json", "TaskRun", row.get("task_run_id", "")) for row in rows]


def _qc_refs(project_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_ref(project_dir, f"v5/qc_reports/{row.get('qc_report_id', '')}.json", "QCReport", row.get("qc_report_id", "")) for row in rows]


def _artifact_refs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"object_type": "ArtifactManifest", "object_id": row.get("artifact_id", ""), "path": row.get("path", ""), "qc_status": row.get("qc_status", ""), "exists": row.get("exists", False)} for row in rows]


def _handoff_refs(project_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for row in rows:
        path = row.get("_path") or f"v5/handoffs/{row.get('handoff_id', '')}.json"
        refs.append(_ref(project_dir, path, "Handoff", row.get("handoff_id", "")))
    return refs


def _load_all_handoffs(project_dir: Path) -> list[dict[str, Any]]:
    out = []
    jsonl = project_dir / "v5" / "handoffs.jsonl"
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_path"] = "v5/handoffs.jsonl"
                out.append(row)
    for path in sorted((project_dir / "v5" / "handoffs").glob("*.json")):
        row = _read_json(path, {})
        if row:
            row["_path"] = str(path.relative_to(project_dir)).replace("\\", "/")
            out.append(row)
    return out


def _ref(project_dir: Path, path: str | Path, object_type: str, object_id: str = "", *, exists: bool | None = None) -> dict[str, Any]:
    rel = str(path).replace("\\", "/")
    actual_exists = (project_dir / rel).exists() if exists is None else exists
    return {"object_type": object_type, "object_id": object_id, "path": rel, "exists": actual_exists}


def _object_id(data: dict[str, Any]) -> str:
    for key in ["subquestion_id", "scope_bundle_id", "evidence_plan_id", "workflow_plan_id", "research_spec_id"]:
        if data.get(key):
            return data[key]
    return data.get("id", "")


def _object_type_from_name(name: str) -> str:
    if name.startswith("research_spec_"):
        return "ResearchSpec"
    if name.startswith("subquestion_"):
        return "SubQuestion"
    if name.startswith("scope_bundle_"):
        return "ScopeBundle"
    if name.startswith("evidence_plan_"):
        return "EvidencePlan"
    if name.startswith("workflow_plan_"):
        return "WorkflowPlan"
    if name.startswith("task_packets_"):
        return "TaskPacketBundle"
    return "CanonicalObject"


def _object_type_from_path(path: str) -> str:
    if "task_packets" in path:
        return "TaskPacketBundle"
    if "report" in path:
        return "Report"
    return "Artifact"


def _review_reason(required: bool, failed_qc: list[dict[str, Any]], alignment_report: dict[str, Any], report_paths: list[dict[str, Any]]) -> str:
    if not required:
        return "All report outputs exist, QC reports pass, and question alignment is approved."
    reasons = []
    if failed_qc:
        reasons.append(f"{len(failed_qc)} QC report(s) are not pass")
    if alignment_report.get("final_decision") != "approve":
        reasons.append(f"alignment decision is {alignment_report.get('final_decision', 'missing')}")
    if not report_paths:
        reasons.append("no report output found")
    return "; ".join(reasons) or "human review required by policy"


def _consistency_checks(report_paths: list[dict[str, Any]], artifacts: list[dict[str, Any]], qc_reports: list[dict[str, Any]], alignment_report: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"check_id": "report_outputs_exist", "status": "pass" if report_paths and all(row.get("exists") for row in report_paths) else "fail"},
        {"check_id": "artifact_registry_nonempty", "status": "pass" if artifacts else "fail"},
        {"check_id": "qc_reports_nonempty", "status": "pass" if qc_reports else "fail"},
        {"check_id": "question_alignment_present", "status": "pass" if alignment_report else "fail"},
    ]


def _agent_gate(agent_id: str, task_runs: list[dict[str, Any]], qc_reports: list[dict[str, Any]], artifacts: list[dict[str, Any]], report_manifest: dict[str, Any]) -> dict[str, Any]:
    if agent_id == "result_auditor":
        failed = [row for row in qc_reports if str(row.get("overall_status", "")).lower() not in {"pass", "passed"}]
        return {"required": bool(failed), "status": "review_required" if failed else "clear", "reason": f"{len(failed)} failed QC report(s)"}
    if agent_id == "evidence_synthesizer_reporter":
        gate = report_manifest.get("human_review_gate") or {}
        return {"required": bool(gate.get("required", True)), "status": "review_required" if gate.get("required", True) else "clear", "reason": gate.get("reason", "report manifest missing")}
    if agent_id == "method_adapter_workorder_compiler":
        return {"required": not bool(task_runs), "status": "clear" if task_runs else "waiting", "reason": f"{len(task_runs)} TaskRun record(s)"}
    if agent_id == "resource_discovery_agent":
        return {"required": False, "status": "clear" if artifacts else "waiting", "reason": f"{len(artifacts)} artifact manifest(s)"}
    return {"required": False, "status": "clear", "reason": "canonical handoff gate"}


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    project_dir = _project_dir_from_v5_path(path)
    write_json_artifact(project_dir, path.relative_to(project_dir), payload, producer="canonical_report_manifest", artifact_type="canonical_report_json")


def _project_dir_from_v5_path(path: Path) -> Path:
    parts = path.parts
    if "v5" in parts:
        return Path(*parts[: parts.index("v5")])
    return path.parent
