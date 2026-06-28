from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .artifacts import register_artifact, validate_artifact_for_evidence
from .backend_writer import write_json_artifact
from .ids import make_stable_id
from .schemas import CANONICAL_SCHEMA_VERSION, now_iso
from .task_packets import build_analysis_task_packet, validate_task_packets


LOCAL_EXECUTION_SCHEMA_VERSION = "v5.local_execution/0.1"


def compile_registered_analysis_task_packets(project_dir: str | Path, *, subquestion_id: str = "") -> dict[str, Any]:
    """Compile v4 registered modules into canonical v5 AnalysisTaskPacket objects."""
    project_dir = Path(project_dir)
    if not (project_dir / "research_spec.json").exists() or not (project_dir / "dataset_cards").exists():
        return _empty_compile(project_dir, "missing research_spec.json or dataset_cards")
    try:
        from targetcompass_lite.evidence_planning import build_evidence_planning_bundle
        from targetcompass_lite.planning import build_plan

        build_evidence_planning_bundle(project_dir)
        plan = build_plan(project_dir)
    except Exception as exc:
        return _empty_compile(project_dir, str(exc))

    packets = []
    for module in plan.get("modules", []):
        packet = build_analysis_task_packet(
            subquestion_id=subquestion_id or _default_subquestion_id(project_dir),
            method_name=module.get("method_id") or module.get("module", ""),
            expected_inputs=_module_expected_inputs(module),
            expected_outputs=list(module.get("expected_outputs", []) or ["registered module output"]),
            qc_requirements=list(module.get("qc_checks", []) or ["executor records artifact manifest"]),
            failure_conditions=list(module.get("compatibility", {}).get("unmet_requirements", []) or ["registered module raises execution error"]),
        )
        packet.update(
            {
                "module": module.get("module", ""),
                "module_id": module.get("module_id", ""),
                "dataset_id": module.get("dataset_id", ""),
                "method_id": module.get("method_id", ""),
                "execution_backend": "local_registered_module",
                "v4_module_ref": module,
            }
        )
        packets.append(packet)

    payload = {
        "schema_version": LOCAL_EXECUTION_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "analysis_plan_ref": "analysis_plan.json",
        "packet_count": len(packets),
        "packets": packets,
        "created_at": now_iso(),
        "status": "compiled" if packets else "empty",
    }
    _write_json(project_dir / "v5" / "task_packets" / "registered_analysis_task_packets.json", payload)
    return payload


def execute_registered_analysis_task_packets(
    project_dir: str | Path,
    packets: list[dict[str, Any]],
    *,
    max_packets: int | None = None,
    runner_overrides: dict[str, Callable[[Path, dict[str, Any]], list[str]]] | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    selected = packets[:max_packets] if max_packets is not None else list(packets)
    task_results = []
    seen_global_modules: set[str] = set()
    for packet in selected:
        module = packet.get("module", "")
        if module in {"sasp_score", "annotation", "cell_type_evidence", "enrichment", "meta_analysis"}:
            if module in seen_global_modules:
                task_results.append(_record_skipped_task(project_dir, packet, f"{module} already executed in this run"))
                continue
            seen_global_modules.add(module)
        task_results.append(execute_analysis_task_packet(project_dir, packet, runner_overrides=runner_overrides))
    post = _run_post_analysis_outputs(project_dir, selected)
    bundle = {
        "schema_version": LOCAL_EXECUTION_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "status": "completed" if all(item.get("task_run", {}).get("result_status") in {"completed", "skipped"} for item in task_results) else "review_required",
        "task_count": len(selected),
        "completed_count": sum(1 for item in task_results if item.get("task_run", {}).get("result_status") == "completed"),
        "skipped_count": sum(1 for item in task_results if item.get("task_run", {}).get("result_status") == "skipped"),
        "failed_count": sum(1 for item in task_results if item.get("task_run", {}).get("result_status") == "failed"),
        "task_results": task_results,
        "post_analysis": post,
        "created_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "local_execution" / "local_execution_bundle.json", bundle)
    return bundle


def execute_analysis_task_packet(
    project_dir: str | Path,
    task_packet: dict[str, Any],
    *,
    runner_overrides: dict[str, Callable[[Path, dict[str, Any]], list[str]]] | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    errors = validate_task_packets([task_packet])
    if errors:
        raise ValueError("; ".join(errors))
    if task_packet.get("packet_type") != "AnalysisTaskPacket":
        raise ValueError("local analysis execution requires AnalysisTaskPacket")

    task_id = task_packet["task_id"]
    module = task_packet.get("module", "")
    task_run_id = make_stable_id("local_task_run", {"task_id": task_id, "module": module, "dataset_id": task_packet.get("dataset_id", "")})
    try:
        output_paths = _execute_module(project_dir, task_packet, runner_overrides or {})
        artifacts = _register_outputs(project_dir, task_packet, task_run_id, output_paths, "pass")
        artifact_errors = [error for artifact in artifacts for error in validate_artifact_for_evidence(artifact)]
        status = "completed" if not artifact_errors else "failed"
        failure_reason = "; ".join(artifact_errors)
    except Exception as exc:
        output_paths = []
        artifacts = []
        status = "failed"
        failure_reason = str(exc)

    task_run = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "task_run_id": task_run_id,
        "project_id": project_dir.name,
        "task_id": task_id,
        "executor": "local_registered_module",
        "module": module,
        "module_id": task_packet.get("module_id", ""),
        "dataset_id": task_packet.get("dataset_id", ""),
        "result_status": status,
        "artifact_refs": [artifact["artifact_id"] for artifact in artifacts],
        "output_paths": output_paths,
        "failure_reason": failure_reason,
        "created_at": now_iso(),
        "status": "recorded",
    }
    qc_report = _build_local_qc_report(project_dir, task_packet, task_run, artifacts, failure_reason)
    task_run["qc_report_ref"] = qc_report["qc_report_id"]
    task_run_ref = f"v5/task_runs/{task_run_id}.json"
    qc_report_ref = f"v5/qc_reports/{qc_report['qc_report_id']}.json"
    _write_json(project_dir / task_run_ref, task_run)
    _write_json(project_dir / qc_report_ref, qc_report)
    return {
        "task_run": task_run,
        "qc_report": qc_report,
        "task_run_ref": task_run_ref,
        "qc_report_ref": qc_report_ref,
        "artifacts": artifacts,
    }


def _execute_module(project_dir: Path, packet: dict[str, Any], runner_overrides: dict[str, Callable[[Path, dict[str, Any]], list[str]]]) -> list[str]:
    module = packet.get("module", "")
    if module in runner_overrides:
        return runner_overrides[module](project_dir, packet)
    if module == "bulk_deg":
        from targetcompass_lite.deg import run_deg

        result = run_deg(project_dir, packet.get("dataset_id", ""))
        out_dir = result.parent
        return _existing(project_dir, [result, out_dir / "qc_summary.json", out_dir / "run_manifest.json", out_dir / "executor_manifest.json"])
    if module == "scrna_pseudobulk":
        from targetcompass_lite.scrna import run_scrna_pseudobulk

        inputs = packet.get("v4_module_ref", {}).get("inputs", {})
        params = packet.get("v4_module_ref", {}).get("parameters", {})
        result = run_scrna_pseudobulk(
            project_dir,
            packet.get("dataset_id", "scrna"),
            inputs.get("expression_matrix", ""),
            inputs.get("metadata", ""),
            donor_column=params.get("donor_column", "donor_id"),
            group_column=params.get("group_column", "group"),
            cell_type_column=params.get("cell_type_column", "cell_type"),
        )
        out_dir = result.parent
        return _existing(project_dir, [result, out_dir / "pseudobulk_metadata.tsv", out_dir / "qc_summary.json", out_dir / "run_manifest.json"])
    if module == "sasp_score":
        from targetcompass_lite.sasp_score import run_sasp_score

        run_sasp_score(project_dir)
        return _existing(project_dir, ["results/sasp_score/sasp_gene_scores.tsv", "results/sasp_score/sasp_dataset_scores.tsv", "results/sasp_score/run_manifest.json"])
    if module == "annotation":
        from targetcompass_lite.annotation import annotate_project

        return _existing(project_dir, list(annotate_project(project_dir)))
    if module == "cell_type_evidence":
        from targetcompass_lite.cell_type_evidence import build_cell_type_evidence

        build_cell_type_evidence(project_dir)
        return _existing(project_dir, ["results/cell_type_evidence/cell_type_evidence.tsv", "results/cell_type_evidence/cell_type_summary.json"])
    if module == "enrichment":
        from targetcompass_lite.enrichment import run_enrichment

        result = run_enrichment(project_dir)
        out_dir = result.parent
        return _existing(project_dir, [result, out_dir / "gsea_preranked_results.tsv", out_dir / "gene_set_snapshot.json", out_dir / "run_manifest.json", out_dir / "qc_summary.json"])
    if module == "meta_analysis":
        from targetcompass_lite.meta_analysis import run_meta_analysis

        result = run_meta_analysis(project_dir)
        out_dir = result.parent
        return _existing(project_dir, [result, out_dir / "qc_summary.json", out_dir / "run_manifest.json"])
    raise ValueError(f"unsupported local registered module: {module or 'unknown'}")


def _run_post_analysis_outputs(project_dir: Path, packets: list[dict[str, Any]]) -> dict[str, Any]:
    refs: list[str] = []
    errors: list[str] = []
    if not packets:
        return {"status": "skipped", "reason": "no registered analysis packets", "artifacts": []}
    try:
        from targetcompass_lite.annotation import annotate_project
        from targetcompass_lite.cell_type_evidence import build_cell_type_evidence
        from targetcompass_lite.evidence_db import import_evidence
        from targetcompass_lite.sasp_score import run_sasp_score
        from targetcompass_lite.scoring import score_project
        from targetcompass_lite.reporting import build_report

        if not (project_dir / "results" / "sasp_score" / "sasp_gene_scores.tsv").exists():
            run_sasp_score(project_dir)
        if not (project_dir / "results" / "annotation" / "accessibility_annotation.tsv").exists():
            annotate_project(project_dir)
        if not (project_dir / "results" / "cell_type_evidence" / "cell_type_evidence.tsv").exists():
            build_cell_type_evidence(project_dir)
        refs.append(_rel(import_evidence(project_dir), project_dir))
        refs.append(_rel(score_project(project_dir), project_dir))
        for path in build_report(project_dir):
            refs.append(_rel(path, project_dir))
    except Exception as exc:
        errors.append(str(exc))
    artifacts = _register_outputs(
        project_dir,
        {"task_id": "v5_post_analysis", "subquestion_id": packets[0].get("subquestion_id", "") if packets else ""},
        "v5_post_analysis",
        refs,
        "pass" if not errors else "failed",
        artifact_type="v5_post_analysis_artifact",
    )
    try:
        from .report_manifest import build_canonical_report_manifest

        manifest = build_canonical_report_manifest(project_dir)
        refs.append("v5/reports/canonical_report_manifest.json")
        if manifest.get("artifact_id"):
            artifacts.append({"artifact_id": manifest["artifact_id"]})
    except Exception as exc:
        errors.append(f"canonical report manifest failed: {exc}")
    return {
        "status": "completed" if not errors else "failed",
        "artifacts": [artifact["artifact_id"] for artifact in artifacts],
        "artifact_paths": refs,
        "errors": errors,
    }


def _record_skipped_task(project_dir: Path, packet: dict[str, Any], reason: str) -> dict[str, Any]:
    task_run_id = make_stable_id("local_task_run_skipped", {"task_id": packet["task_id"], "reason": reason})
    task_run = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "task_run_id": task_run_id,
        "project_id": project_dir.name,
        "task_id": packet["task_id"],
        "executor": "local_registered_module",
        "module": packet.get("module", ""),
        "module_id": packet.get("module_id", ""),
        "dataset_id": packet.get("dataset_id", ""),
        "result_status": "skipped",
        "artifact_refs": [],
        "failure_reason": "",
        "skip_reason": reason,
        "created_at": now_iso(),
        "status": "recorded",
    }
    qc_report = _build_local_qc_report(project_dir, packet, task_run, [], "")
    task_run["qc_report_ref"] = qc_report["qc_report_id"]
    _write_json(project_dir / "v5" / "task_runs" / f"{task_run_id}.json", task_run)
    _write_json(project_dir / "v5" / "qc_reports" / f"{qc_report['qc_report_id']}.json", qc_report)
    return {"task_run": task_run, "qc_report": qc_report, "artifacts": []}


def _build_local_qc_report(project_dir: Path, packet: dict[str, Any], task_run: dict[str, Any], artifacts: list[dict[str, Any]], failure_reason: str) -> dict[str, Any]:
    status = task_run["result_status"]
    checks = [
        {"check_id": "packet_schema", "status": "pass", "message": "AnalysisTaskPacket validated."},
        {"check_id": "executor_dispatch", "status": "pass" if status in {"completed", "skipped"} else "fail", "message": failure_reason or status},
        {"check_id": "artifact_registry", "status": "pass" if status == "skipped" or artifacts else "fail", "message": f"{len(artifacts)} artifact(s) registered"},
    ]
    overall = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "qc_report_id": make_stable_id("qc_report", {"task_run_id": task_run["task_run_id"], "checks": checks}),
        "project_id": project_dir.name,
        "task_id": packet["task_id"],
        "task_run_id": task_run["task_run_id"],
        "overall_status": overall,
        "checks": checks,
        "created_at": now_iso(),
        "status": "recorded",
    }


def _register_outputs(
    project_dir: Path,
    packet: dict[str, Any],
    producer_run_id: str,
    output_paths: list[str],
    qc_status: str,
    *,
    artifact_type: str = "local_analysis_artifact",
) -> list[dict[str, Any]]:
    artifacts = []
    for output in output_paths:
        artifacts.append(
            register_artifact(
                project_dir,
                output,
                producer=packet["task_id"],
                artifact_type=artifact_type if artifact_type != "local_analysis_artifact" else _artifact_type(output),
                expected_by_task_ids=[packet["task_id"]],
                supports_subquestion_ids=[packet.get("subquestion_id", "")],
                producer_run_id=producer_run_id,
                qc_status=qc_status,
                limitations=[] if qc_status == "pass" else ["Local registered module failed or produced invalid artifacts."],
            )
        )
    return artifacts


def _module_expected_inputs(module: dict[str, Any]) -> list[str]:
    inputs = module.get("inputs", {})
    if isinstance(inputs, dict):
        return [str(value) for value in inputs.values() if value]
    return list(inputs or [])


def _default_subquestion_id(project_dir: Path) -> str:
    data = _read_json(project_dir / "v5" / "objects" / "research_spec_latest.json", {})
    return data.get("subquestion_id") or "sq_v5_local_registered_execution"


def _empty_compile(project_dir: Path, reason: str) -> dict[str, Any]:
    payload = {
        "schema_version": LOCAL_EXECUTION_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "packet_count": 0,
        "packets": [],
        "status": "empty",
        "reason": reason,
        "created_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "task_packets" / "registered_analysis_task_packets.json", payload)
    return payload


def _existing(project_dir: Path, paths: list[str | Path]) -> list[str]:
    out = []
    for path in paths:
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else project_dir / candidate
        if absolute.exists() and absolute.is_file():
            out.append(_rel(absolute, project_dir))
    return out


def _rel(path: str | Path, project_dir: Path) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def _artifact_type(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".html") or lowered.endswith(".docx") or "report" in lowered:
        return "report_artifact"
    if lowered.endswith(".json"):
        return "analysis_manifest"
    if lowered.endswith(".tsv") or lowered.endswith(".csv"):
        return "analysis_table"
    return "analysis_artifact"


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    project_dir = _project_dir_from_v5_path(path)
    write_json_artifact(project_dir, path.relative_to(project_dir), payload, producer="local_execution", artifact_type="local_execution_json")


def _project_dir_from_v5_path(path: Path) -> Path:
    parts = path.parts
    if "v5" in parts:
        return Path(*parts[: parts.index("v5")])
    return path.parent
