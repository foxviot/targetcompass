from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .artifacts import register_artifact
from .ids import make_stable_id
from .local_execution import compile_registered_analysis_task_packets, execute_registered_analysis_task_packets
from .mock_runner import run_mock_canonical_pipeline
from .resource_discovery import FetchJson, discover_real_resources
from .run_workspace import snapshot_v5_run_workspace
from .schemas import CANONICAL_SCHEMA_VERSION, now_iso
from .store import transition_state


LOCAL_DEMO_SCHEMA_VERSION = "v5.local_demo_run/0.1"


def run_v5_local_demo(
    project_dir: str | Path,
    question: str,
    *,
    sources: list[str] | tuple[str, ...] = ("geo", "sra", "pubmed", "europe_pmc"),
    limit: int = 3,
    fetch_json: FetchJson | None = None,
    execute_registered_modules: bool = True,
    max_analysis_packets: int | None = None,
) -> dict[str, Any]:
    """Run a local v5 validation slice from question to TaskRun/QC/Artifact.

    This validates orchestration and real resource discovery. It does not lock datasets
    or generate biological conclusions unless later reviewed analysis modules do so.
    """
    project_dir = Path(project_dir)
    pipeline = run_mock_canonical_pipeline(project_dir, question)
    evidence_plan = pipeline["evidence_plan"]
    scope_bundle = _scope_from_question(pipeline["scope_bundle"], question)
    resource_bundle = discover_real_resources(
        project_dir,
        evidence_plan,
        scope_bundle,
        sources=sources,
        limit=limit,
        fetch_json=fetch_json,
        write=True,
    )
    compile_result = compile_registered_analysis_task_packets(project_dir, subquestion_id=_first_subquestion_id(pipeline))
    registered_packets = compile_result.get("packets", []) if execute_registered_modules else []
    analysis_packet = registered_packets[0] if registered_packets else _first_analysis_packet(pipeline["task_packets"])
    transition_state(
        project_dir,
        "TASKS_RUNNING",
        "LOCAL_DEMO_TASKS_RUNNING",
        "v5_local_demo_runner",
        [{"object_type": "AnalysisTaskPacket", "object_id": analysis_packet["task_id"]}],
        "Started local v5 validation task packet execution.",
    )
    if registered_packets:
        execution = execute_registered_analysis_task_packets(project_dir, registered_packets, max_packets=max_analysis_packets)
        object_refs = _execution_object_refs(execution)
        artifact_refs = _execution_artifact_refs(execution)
        qc_status = "pass" if execution.get("status") == "completed" else "review"
    else:
        execution = _execute_local_validation_task(project_dir, analysis_packet, pipeline, resource_bundle)
        object_refs = [
            {"object_type": "TaskRun", "object_id": execution["task_run"]["task_run_id"], "path": execution["task_run_ref"]},
            {"object_type": "QCReport", "object_id": execution["qc_report"]["qc_report_id"], "path": execution["qc_report_ref"]},
        ]
        artifact_refs = [artifact["artifact_id"] for artifact in execution["artifacts"]]
        qc_status = execution["qc_report"]["overall_status"]
    transition_state(
        project_dir,
        "QC_COMPLETED",
        "LOCAL_DEMO_QC_COMPLETED",
        "v5_local_demo_runner",
        object_refs,
        "Completed local v5 validation task packet execution and QC.",
    )
    if registered_packets:
        if execution.get("status") == "completed" and execution.get("post_analysis", {}).get("status") == "completed":
            _advance_report_stages(project_dir, execution)
        else:
            transition_state(
                project_dir,
                "HUMAN_REVIEW_REQUIRED",
                "LOCAL_DEMO_REVIEW_REQUIRED",
                "v5_local_demo_runner",
                [{"object_type": "LocalExecution", "object_id": execution.get("status", ""), "path": "v5/local_execution/local_execution_bundle.json"}],
                "Local registered-module execution requires review before report-ready signoff.",
            )
    result = {
        "schema_version": LOCAL_DEMO_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "question": question,
        "status": "completed" if qc_status == "pass" else "review_required",
        "claim_scope": "registered_local_analysis" if registered_packets else "control_plane_validation_only",
        "resource_discovery_ref": "v5/resource_discovery/resource_discovery_bundle.json",
        "resource_gate_ref": resource_bundle.get("resource_gate_ref", "v5/resource_discovery/resource_gate_report.json"),
        "task_packet_compile_ref": "v5/task_packets/registered_analysis_task_packets.json",
        "execution_ref": "v5/local_execution/local_execution_bundle.json" if registered_packets else "",
        "task_run_ref": execution.get("task_run_ref", ""),
        "qc_report_ref": execution.get("qc_report_ref", ""),
        "task_run_refs": _execution_task_run_refs(execution),
        "qc_report_refs": _execution_qc_report_refs(execution),
        "artifact_refs": artifact_refs,
        "analysis_task_count": len(registered_packets),
        "analysis_execution_status": execution.get("status", "control_plane_validation"),
        "verified_candidate_count": resource_bundle.get("verified_candidate_count", 0),
        "locked_dataset_count": resource_bundle.get("locked_dataset_count", 0),
        "resource_manual_review_count": resource_bundle.get("manual_review_count", 0),
        "limitations": [
            "Local runner validates v5 control-plane execution and resource discovery.",
            "No dataset is DATASETS_LOCKED automatically; biological claims require reviewed analysis outputs.",
        ],
        "created_at": now_iso(),
    }
    result["run_id"] = make_stable_id("v5_local_demo", {"question": question, "execution": execution.get("status", ""), "artifacts": artifact_refs})
    snapshot_refs = [
        "v5/resource_discovery/resource_discovery_bundle.json",
        result.get("resource_gate_ref", "v5/resource_discovery/resource_gate_report.json"),
        result.get("task_packet_compile_ref", "v5/task_packets/registered_analysis_task_packets.json"),
        result.get("execution_ref", "v5/local_execution/local_execution_bundle.json"),
        "v5/local_demo/local_demo_run.json",
        "v5/reports/canonical_report_manifest.json",
        "reports/target_report.html",
        "candidate_scores.csv",
    ]
    snapshot_refs.extend(result.get("task_run_refs", []))
    snapshot_refs.extend(result.get("qc_report_refs", []))
    result["run_workspace_ref"] = f"v5/runs/{result['run_id']}/run_workspace_manifest.json"
    _write_json(project_dir / "v5" / "local_demo" / "local_demo_run.json", result)
    workspace = snapshot_v5_run_workspace(project_dir, result["run_id"], snapshot_refs, question=question)
    result["run_workspace_copied_count"] = workspace.get("copied_count", 0)
    _write_json(project_dir / "v5" / "local_demo" / "local_demo_run.json", result)
    return result


def _execute_local_validation_task(
    project_dir: Path,
    analysis_packet: dict[str, Any],
    pipeline: dict[str, Any],
    resource_bundle: dict[str, Any],
) -> dict[str, Any]:
    run_id = make_stable_id(
        "local_task_run",
        {
            "task_id": analysis_packet["task_id"],
            "resource_candidates": [row.get("resource_candidate_id") for row in resource_bundle.get("resource_candidates", [])],
        },
    )
    out_dir = project_dir / "v5" / "local_runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "resource_task_summary.tsv"
    summary_path.write_text(_resource_summary_tsv(resource_bundle), encoding="utf-8")
    manifest_path = out_dir / "local_execution_manifest.json"
    local_manifest = {
        "schema_version": "v5.local_task_execution/0.1",
        "project_id": project_dir.name,
        "task_id": analysis_packet["task_id"],
        "run_id": run_id,
        "executor": "v5_local_demo_runner",
        "resource_candidate_count": len(resource_bundle.get("resource_candidates", [])),
        "dataset_profile_count": len(resource_bundle.get("dataset_profiles", [])),
        "verified_candidate_count": resource_bundle.get("verified_candidate_count", 0),
        "locked_dataset_count": resource_bundle.get("locked_dataset_count", 0),
        "analysis_claim": "none",
        "outputs": ["v5/local_runs/" + run_id + "/resource_task_summary.tsv"],
        "created_at": now_iso(),
    }
    _write_json(manifest_path, local_manifest)
    artifact_summary = register_artifact(
        project_dir,
        summary_path.relative_to(project_dir),
        producer=analysis_packet["task_id"],
        artifact_type="v5_local_resource_summary",
        expected_by_task_ids=[analysis_packet["task_id"]],
        supports_subquestion_ids=[analysis_packet.get("subquestion_id", "")],
        producer_run_id=run_id,
        qc_status="pass",
        limitations=["Resource summary is a control-plane validation artifact, not a biological result."],
    )
    artifact_manifest = register_artifact(
        project_dir,
        manifest_path.relative_to(project_dir),
        producer=analysis_packet["task_id"],
        artifact_type="v5_local_execution_manifest",
        expected_by_task_ids=[analysis_packet["task_id"]],
        supports_subquestion_ids=[analysis_packet.get("subquestion_id", "")],
        producer_run_id=run_id,
        qc_status="pass",
        limitations=["Execution manifest records local validation only."],
    )
    checks = [
        {
            "check_id": "resource_discovery_completed",
            "status": "pass" if resource_bundle.get("query_attempts") else "fail",
            "message": f"{len(resource_bundle.get('query_attempts', []))} query attempt(s)",
        },
        {
            "check_id": "no_auto_dataset_lock",
            "status": "pass" if resource_bundle.get("locked_dataset_count", 0) == 0 else "fail",
            "message": "Datasets require human review before DATASETS_LOCKED.",
        },
        {
            "check_id": "artifact_registry_written",
            "status": "pass" if artifact_summary.get("exists") and artifact_manifest.get("exists") else "fail",
            "message": "Local execution artifacts registered with checksums.",
        },
        {
            "check_id": "claim_scope_control_plane_only",
            "status": "pass",
            "message": "No biological claim was generated by local validation task.",
        },
    ]
    overall = "pass" if all(row["status"] == "pass" for row in checks) else "review"
    task_run = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "task_run_id": run_id,
        "project_id": project_dir.name,
        "task_id": analysis_packet["task_id"],
        "executor": "v5_local_demo_runner",
        "result_status": "completed" if overall == "pass" else "review_required",
        "artifact_refs": [artifact_summary["artifact_id"], artifact_manifest["artifact_id"]],
        "manifest_ref": str(manifest_path.relative_to(project_dir)).replace("\\", "/"),
        "failure_reason": "" if overall == "pass" else "Local validation requires review.",
        "created_at": now_iso(),
        "status": "recorded",
    }
    qc_report = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "qc_report_id": make_stable_id("qc_report", {"task_run_id": run_id, "checks": checks}),
        "project_id": project_dir.name,
        "task_id": analysis_packet["task_id"],
        "task_run_id": run_id,
        "overall_status": overall,
        "checks": checks,
        "created_at": now_iso(),
        "status": "recorded",
    }
    task_run["qc_report_ref"] = qc_report["qc_report_id"]
    task_run_ref = f"v5/task_runs/{run_id}.json"
    qc_report_ref = f"v5/qc_reports/{qc_report['qc_report_id']}.json"
    _write_json(project_dir / task_run_ref, task_run)
    _write_json(project_dir / qc_report_ref, qc_report)
    return {
        "task_run": task_run,
        "qc_report": qc_report,
        "task_run_ref": task_run_ref,
        "qc_report_ref": qc_report_ref,
        "artifacts": [artifact_summary, artifact_manifest],
    }


def _resource_summary_tsv(resource_bundle: dict[str, Any]) -> str:
    rows = ["source\taccession\tverified\tsource_status\tresource_type\ttitle"]
    for item in resource_bundle.get("resource_candidates", []):
        rows.append(
            "\t".join(
                [
                    str(item.get("source", "")),
                    str(item.get("accession", "")),
                    str(item.get("verified", False)),
                    str(item.get("source_status", "")),
                    str(item.get("resource_type", "")),
                    str(item.get("title", "")).replace("\t", " ").replace("\n", " ")[:240],
                ]
            )
        )
    return "\n".join(rows) + "\n"


def _first_analysis_packet(task_packets: list[dict[str, Any]]) -> dict[str, Any]:
    for packet in task_packets:
        if packet.get("packet_type") == "AnalysisTaskPacket":
            return packet
    raise ValueError("No AnalysisTaskPacket found")


def _first_subquestion_id(pipeline: dict[str, Any]) -> str:
    subquestions = pipeline.get("subquestions", [])
    if subquestions:
        return subquestions[0].get("subquestion_id", "")
    for packet in pipeline.get("task_packets", []):
        if packet.get("subquestion_id"):
            return packet["subquestion_id"]
    return "sq_v5_local_registered_execution"


def _execution_object_refs(execution: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for item in execution.get("task_results", []):
        task_run = item.get("task_run", {})
        qc_report = item.get("qc_report", {})
        if task_run:
            refs.append({"object_type": "TaskRun", "object_id": task_run.get("task_run_id", ""), "path": f"v5/task_runs/{task_run.get('task_run_id', '')}.json"})
        if qc_report:
            refs.append({"object_type": "QCReport", "object_id": qc_report.get("qc_report_id", ""), "path": f"v5/qc_reports/{qc_report.get('qc_report_id', '')}.json"})
    return refs


def _execution_artifact_refs(execution: dict[str, Any]) -> list[str]:
    refs = []
    for item in execution.get("task_results", []):
        refs.extend(artifact.get("artifact_id", "") for artifact in item.get("artifacts", []))
    refs.extend(execution.get("post_analysis", {}).get("artifacts", []))
    return [ref for ref in refs if ref]


def _execution_task_run_refs(execution: dict[str, Any]) -> list[str]:
    if execution.get("task_run_ref"):
        return [execution["task_run_ref"]]
    refs = []
    for item in execution.get("task_results", []):
        ref = item.get("task_run_ref")
        if ref:
            refs.append(ref)
        elif item.get("task_run", {}).get("task_run_id"):
            refs.append(f"v5/task_runs/{item['task_run']['task_run_id']}.json")
    return refs


def _execution_qc_report_refs(execution: dict[str, Any]) -> list[str]:
    if execution.get("qc_report_ref"):
        return [execution["qc_report_ref"]]
    refs = []
    for item in execution.get("task_results", []):
        ref = item.get("qc_report_ref")
        if ref:
            refs.append(ref)
        elif item.get("qc_report", {}).get("qc_report_id"):
            refs.append(f"v5/qc_reports/{item['qc_report']['qc_report_id']}.json")
    return refs


def _advance_report_stages(project_dir: Path, execution: dict[str, Any]) -> None:
    post = execution.get("post_analysis", {})
    transition_state(
        project_dir,
        "EVIDENCE_SYNTHESIZED",
        "LOCAL_DEMO_EVIDENCE_SYNTHESIZED",
        "v5_local_demo_runner",
        [{"object_type": "PostAnalysis", "object_id": post.get("status", ""), "path": "v5/local_execution/local_execution_bundle.json"}],
        "Imported evidence and refreshed scores through local registered modules.",
    )
    transition_state(
        project_dir,
        "ALIGNMENT_AUDITED",
        "LOCAL_DEMO_ALIGNMENT_AUDITED",
        "v5_local_demo_runner",
        [{"object_type": "PostAnalysis", "object_id": post.get("status", ""), "path": "v5/local_execution/local_execution_bundle.json"}],
        "Recorded local alignment checkpoint for registered-module outputs.",
    )
    transition_state(
        project_dir,
        "REPORT_READY",
        "LOCAL_DEMO_REPORT_READY",
        "v5_local_demo_runner",
        [{"object_type": "Report", "object_id": "target_report", "path": "reports/target_report.html"}],
        "Built report from local registered-module outputs.",
    )


def _scope_from_question(scope_bundle: dict[str, Any], question: str) -> dict[str, Any]:
    text = question.lower()
    scope = dict(scope_bundle)
    if any(term in text for term in ["肌少", "sarcopenia"]):
        scope["conditions"] = ["sarcopenia"]
    if any(term in text for term in ["肌肉", "muscle"]):
        scope["tissues"] = ["skeletal muscle"]
    if any(term in text for term in ["人", "patient", "患者", "human"]):
        scope["species"] = ["human"]
    axes = set(scope.get("evidence_axes", []))
    if "sasp" in text.lower():
        axes.add("SASP_annotation")
    if any(term in text for term in ["表面", "surface"]):
        axes.add("surface_marker")
    if any(term in text for term in ["细胞", "cell"]):
        axes.add("cell_type_specificity")
    if axes:
        scope["evidence_axes"] = sorted(axes)
    return scope


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
