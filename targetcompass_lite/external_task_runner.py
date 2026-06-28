import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sasp_score import run_sasp_score
from .v4 import content_hash


RUN_SCHEMA = "v4.external_codex_task_packet_run/0.1"


def run_external_codex_task_packets(project_dir: Path, packet_file: Path) -> dict[str, Any]:
    packet_path = packet_file if packet_file.is_absolute() else project_dir / packet_file
    payload = _read_json(packet_path, {})
    packets = payload.get("packets", [])
    if not isinstance(packets, list) or not packets:
        raise ValueError(f"packet file has no packets: {packet_path}")

    run_id = "external_packet_run_" + content_hash({"packet_file": str(packet_path), "packets": packets, "time": _now()})[:16]
    out_dir = project_dir / "external_agent_runs" / "bioinfo_agent_system" / "packet_executions" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, str] = {}
    task_results: list[dict[str, Any]] = []
    status_by_task: dict[str, str] = {}

    for packet in packets:
        task_id = str(packet.get("task_id", ""))
        blocked_deps = [dep for dep in packet.get("dependencies", []) if status_by_task.get(dep) != "success"]
        if blocked_deps:
            result = _task_result(
                project_dir,
                out_dir,
                packet,
                "blocked",
                f"dependency not successful: {', '.join(blocked_deps)}",
                [],
                _recovery_for_packet(packet, dependency_blocked=True),
            )
        else:
            result = _execute_packet(project_dir, out_dir, packet, produced)
            if result["status"] == "success":
                for output in packet.get("output_artifacts", []):
                    if output:
                        produced[str(output)] = result.get("primary_output", "")
        task_results.append(result)
        status_by_task[task_id] = result["status"]

    run_status = "success" if all(row["status"] == "success" for row in task_results) else "failed"
    if any(row["status"] == "blocked" for row in task_results) and not any(row["status"] == "failed" for row in task_results):
        run_status = "blocked"
    summary = {
        "schema_version": RUN_SCHEMA,
        "project_id": project_dir.name,
        "run_id": run_id,
        "status": run_status,
        "packet_file": _rel(packet_path, project_dir),
        "started_at": task_results[0]["started_at"] if task_results else _now(),
        "finished_at": _now(),
        "task_count": len(task_results),
        "success_count": sum(1 for row in task_results if row["status"] == "success"),
        "failed_count": sum(1 for row in task_results if row["status"] == "failed"),
        "blocked_count": sum(1 for row in task_results if row["status"] == "blocked"),
        "produced_artifacts": produced,
        "tasks": task_results,
        "recovery_summary": _recovery_summary(task_results),
    }
    summary_path = out_dir / "run_manifest.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = packet_path.parent / "latest_packet_execution.json"
    latest.write_text(json.dumps({**summary, "manifest": _rel(summary_path, project_dir)}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**summary, "manifest": _rel(summary_path, project_dir)}


def _execute_packet(project_dir: Path, out_dir: Path, packet: dict[str, Any], produced: dict[str, str]) -> dict[str, Any]:
    method = str(packet.get("method_contract_id", ""))
    inputs = _resolve_inputs(project_dir, packet, produced)
    missing = [row for row in inputs if row["status"] == "missing"]
    if missing and method != "method_contract_SASP_score_computation":
        return _task_result(
            project_dir,
            out_dir,
            packet,
            "failed",
            "missing required input artifact(s): " + ", ".join(row["artifact"] for row in missing),
            inputs,
            _recovery_for_packet(packet),
        )

    if method == "method_contract_SASP_score_computation":
        # This external packet asks for single-cell SASP scores. If per-cell inputs are
        # absent, run the existing project-level DEG SASP score as a degraded artifact
        # only when real DEG files exist, and mark the limitation explicitly.
        deg_files = sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))
        if not deg_files:
            return _task_result(project_dir, out_dir, packet, "failed", "no DEG or scRNA input available for SASP scoring", inputs, _recovery_for_packet(packet))
        result = run_sasp_score(project_dir)
        limitation = "Executed degraded project-level DEG SASP score; this is not per-cell scRNA SASP scoring."
        return _task_result(
            project_dir,
            out_dir,
            packet,
            "success",
            limitation,
            inputs,
            [{"type": "limitation", "message": limitation}],
            primary_output=result["manifest"]["outputs"]["gene_scores"],
            executor="targetcompass_lite.sasp_score.run_sasp_score",
        )

    return _task_result(
        project_dir,
        out_dir,
        packet,
        "failed",
        f"no executor registered for method_contract_id={method}",
        inputs,
        _recovery_for_packet(packet),
    )


def _resolve_inputs(project_dir: Path, packet: dict[str, Any], produced: dict[str, str]) -> list[dict[str, str]]:
    rows = []
    for artifact in packet.get("input_artifacts", []):
        artifact = str(artifact)
        if artifact in produced and produced[artifact]:
            rows.append({"artifact": artifact, "status": "available", "path": produced[artifact], "source": "previous_task"})
            continue
        resolved = _resolve_artifact_path(project_dir, artifact)
        if resolved:
            rows.append({"artifact": artifact, "status": "available", "path": _rel(resolved, project_dir), "source": "project_file"})
        else:
            rows.append({"artifact": artifact, "status": "missing", "path": "", "source": ""})
    return rows


def _resolve_artifact_path(project_dir: Path, artifact: str) -> Path | None:
    direct = project_dir / artifact
    if direct.exists():
        return direct
    logical = artifact.lower()
    if logical == "raw_scrnaseq_data":
        for metadata in sorted((project_dir / "data").glob("*/metadata.tsv")):
            try:
                first = metadata.read_text(encoding="utf-8").splitlines()[0].split("\t")
            except Exception:
                continue
            matrix = metadata.parent / "expression_matrix.tsv"
            if matrix.exists() and "cell_id" in first:
                return metadata.parent
    if logical == "sasp_scores":
        path = project_dir / "results" / "sasp_score" / "sasp_gene_scores.tsv"
        return path if path.exists() else None
    return None


def _task_result(
    project_dir: Path,
    out_dir: Path,
    packet: dict[str, Any],
    status: str,
    reason: str,
    inputs: list[dict[str, str]],
    recovery: list[dict[str, str]],
    primary_output: str = "",
    executor: str = "external_task_runner",
) -> dict[str, Any]:
    task_id = str(packet.get("task_id", "unknown"))
    result = {
        "task_id": task_id,
        "name": packet.get("name", ""),
        "method_contract_id": packet.get("method_contract_id", ""),
        "status": status,
        "executor": executor,
        "started_at": _now(),
        "finished_at": _now(),
        "input_resolution": inputs,
        "primary_output": primary_output,
        "failure_reason": "" if status == "success" else reason,
        "message": reason if status == "success" else "",
        "recovery": recovery,
        "acceptance_criteria": packet.get("acceptance_criteria", []),
        "failure_condition": packet.get("failure_condition", ""),
    }
    path = out_dir / f"{task_id}_result.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["result_artifact"] = _rel(path, project_dir)
    return result


def _recovery_for_packet(packet: dict[str, Any], dependency_blocked: bool = False) -> list[dict[str, str]]:
    method = str(packet.get("method_contract_id", ""))
    if dependency_blocked:
        return [{"type": "rerun", "message": "Fix or rerun upstream failed dependency before this task."}]
    if method == "method_contract_scRNAseq_QC":
        return [
            {"type": "provide_input", "message": "Provide a real scRNA/snRNA count matrix and metadata with cell_id, donor_id, group, and cell_type columns."},
            {"type": "alternative", "message": "Run GEO raw download/import first, then map raw_scRNAseq_data to the generated matrix/metadata."},
        ]
    if method == "method_contract_cell_type_annotation":
        return [{"type": "provide_input", "message": "Provide QC-filtered scRNA data or metadata with reliable cell_type annotations."}]
    if method == "method_contract_SASP_score_computation":
        return [{"type": "provide_input", "message": "Provide per-cell/pseudobulk expression with a SASP gene set, or accept degraded DEG-level SASP scoring only as non-cell-specific evidence."}]
    if method == "method_contract_surface_marker_enrichment":
        return [{"type": "provide_input", "message": "Provide SASP high/low groups plus surface annotation database such as HPA/UniProt/CellMarker."}]
    return [{"type": "register_executor", "message": f"Register an executor for {method} before rerun."}]


def _recovery_summary(tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for task in tasks:
        if task["status"] == "success":
            continue
        for item in task.get("recovery", [])[:2]:
            rows.append({"task_id": task["task_id"], "type": item.get("type", ""), "message": item.get("message", "")})
    return rows


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
