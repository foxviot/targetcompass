import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .nextflow_plane import build_nextflow_execution_plane
from .v4 import content_hash, finish_work_order_attempt, load_v4_work_orders, start_work_order_attempt, v4_dir


TASKS_SCHEMA = "v4.nextflow_tasks/0.1"
RUN_SCHEMA = "v4.nextflow_run/0.1"


CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess]


def nextflow_tasks_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "tasks.json"


def nextflow_run_manifest_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "nextflow_run_manifest.json"


def build_nextflow_tasks(project_dir: Path, module_ids: list[str] | None = None) -> dict[str, Any]:
    orders = load_v4_work_orders(project_dir)
    selected = set(module_ids or [])
    tasks = []
    for order in orders:
        if selected and order.get("module_id") not in selected:
            continue
        module_id = _nextflow_module_id(order)
        if not module_id:
            continue
        task = {
            "task_id": "nft_" + content_hash({"work_order": order.get("work_order_id"), "module": module_id})[:16],
            "work_order_id": order.get("work_order_id", ""),
            "module_id": module_id,
            "source_module_id": order.get("module_id", ""),
            "dataset_id": order.get("dataset_id", ""),
            "inputs": order.get("inputs", {}),
            "parameters": order.get("parameters", {}),
            "expected_outputs": order.get("expected_artifacts", []),
            "resume_key": order.get("idempotency_key", ""),
        }
        task.update(_flatten_task_inputs(order.get("inputs", {})))
        tasks.append(task)
    payload = {
        "schema_version": TASKS_SCHEMA,
        "project_id": project_dir.name,
        "task_count": len(tasks),
        "tasks": tasks,
        "generated_at": _now(),
        "tasks_hash": content_hash(tasks),
    }
    path = nextflow_tasks_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def run_nextflow_local(
    project_dir: Path,
    profile: str = "local",
    module_ids: list[str] | None = None,
    nextflow_bin: str = "nextflow",
    resume: bool = False,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    plane = build_nextflow_execution_plane(project_dir)
    tasks = build_nextflow_tasks(project_dir, module_ids)
    attempt = start_work_order_attempt(project_dir, "nextflow_target_discovery", "nextflow")
    out_dir = project_dir / "workflows" / "target_discovery" / "runs" / attempt["attempt_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.html"
    timeline = out_dir / "timeline.html"
    trace = out_dir / "trace.txt"
    dag = out_dir / "dag.html"
    work_dir = out_dir / "work"
    command = [
        nextflow_bin,
        "run",
        str((project_dir / plane["entrypoint"]).resolve()),
        "-profile",
        profile,
        "-c",
        str((project_dir / plane["config"]).resolve()),
        "--project",
        project_dir.name,
        "--tasks_json",
        str(nextflow_tasks_path(project_dir).resolve()),
        "--outdir",
        str((out_dir / "results").resolve()),
        "-work-dir",
        str(work_dir.resolve()),
        "-with-report",
        str(report.resolve()),
        "-with-timeline",
        str(timeline.resolve()),
        "-with-trace",
        str(trace.resolve()),
        "-with-dag",
        str(dag.resolve()),
    ]
    if resume:
        command.append("-resume")
    if runner is None and shutil.which(nextflow_bin) is None:
        failure = f"Nextflow executable not found: {nextflow_bin}"
        manifest = _write_run_manifest(project_dir, attempt, command, profile, tasks, 127, "", failure, out_dir)
        finish_work_order_attempt(
            project_dir,
            attempt["attempt_id"],
            "failed",
            manifest["artifacts"],
            failure_reason=failure,
            metadata={"nextflow": manifest},
        )
        return manifest
    completed = (runner or _default_runner)(command, project_dir)
    nf_log = project_dir / ".nextflow.log"
    if nf_log.exists():
        copied = out_dir / ".nextflow.log"
        copied.write_text(nf_log.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    else:
        copied = out_dir / ".nextflow.log"
        copied.write_text(completed.stderr or completed.stdout or "", encoding="utf-8")
    status = "success" if completed.returncode == 0 else "failed"
    failure_reason = "" if status == "success" else (completed.stderr or completed.stdout or f"nextflow exited with {completed.returncode}")
    manifest = _write_run_manifest(project_dir, attempt, command, profile, tasks, completed.returncode, completed.stdout, failure_reason, out_dir)
    finish_work_order_attempt(
        project_dir,
        attempt["attempt_id"],
        status,
        manifest["artifacts"],
        failure_reason=failure_reason,
        metadata={"nextflow": manifest},
    )
    return manifest


def _default_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _write_run_manifest(
    project_dir: Path,
    attempt: dict[str, Any],
    command: list[str],
    profile: str,
    tasks: dict[str, Any],
    returncode: int,
    stdout: str,
    failure_reason: str,
    out_dir: Path,
) -> dict[str, Any]:
    artifacts = []
    for path in [out_dir / ".nextflow.log", out_dir / "report.html", out_dir / "timeline.html", out_dir / "trace.txt", out_dir / "dag.html"]:
        if path.exists():
            artifacts.append(_rel(project_dir, path))
    trace_failures = _parse_trace_failures(out_dir / "trace.txt")
    manifest = {
        "schema_version": RUN_SCHEMA,
        "project_id": project_dir.name,
        "attempt_id": attempt["attempt_id"],
        "work_order_id": attempt.get("work_order_id", ""),
        "profile": profile,
        "command": command,
        "returncode": returncode,
        "status": "success" if returncode == 0 else "failed",
        "failure_reason": failure_reason,
        "resume": "-resume" in command,
        "recovery": {
            "resume_command": command + ([] if "-resume" in command else ["-resume"]),
            "module_filter_supported": True,
            "failed_tasks": trace_failures,
            "recommendation": _recovery_recommendation(returncode, failure_reason, trace_failures),
        },
        "stdout_tail": stdout[-4000:],
        "tasks_json": _rel(project_dir, nextflow_tasks_path(project_dir)),
        "tasks_hash": tasks.get("tasks_hash", ""),
        "task_count": tasks.get("task_count", 0),
        "artifacts": artifacts,
        "run_dir": _rel(project_dir, out_dir),
        "finished_at": _now(),
    }
    path = nextflow_run_manifest_path(project_dir)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "nextflow_run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def _nextflow_module_id(order: dict[str, Any]) -> str:
    module = order.get("module", "")
    if module == "bulk_deg":
        return "bulk_deg_v1"
    if module in {"scrna_pseudobulk", "single_cell_pseudobulk"}:
        return "scrna_pseudobulk_v1"
    if module in {"genetic_coloc_mr", "gwas_qtl_coloc_mr"}:
        return "genetic_coloc_mr_v1"
    if module in {"enrichment", "meta_analysis"}:
        return {"enrichment": "enrichment_v2", "meta_analysis": "deg_meta_analysis_v1"}[module]
    return ""


def _flatten_task_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    flattened = {}
    for key, value in inputs.items():
        if key in {"expression_matrix", "metadata", "count_matrix", "gwas_summary", "qtl_summary", "ld_reference"}:
            flattened[key] = value
    return flattened


def _parse_trace_failures(trace_path: Path) -> list[dict[str, str]]:
    if not trace_path.exists():
        return []
    lines = [line for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    failures = []
    for line in lines[1:]:
        values = line.split("\t")
        row = {header[idx]: values[idx] for idx in range(min(len(header), len(values)))}
        status = row.get("status", "").upper()
        if status and status not in {"COMPLETED", "CACHED", "OK"}:
            failures.append(
                {
                    "task_id": row.get("task_id", row.get("task_id_hash", "")),
                    "process": row.get("process", ""),
                    "name": row.get("name", ""),
                    "status": row.get("status", ""),
                    "exit": row.get("exit", ""),
                }
            )
    return failures


def _recovery_recommendation(returncode: int, failure_reason: str, failed_tasks: list[dict[str, str]]) -> str:
    if returncode == 0:
        return "No recovery required."
    if returncode == 127 or "not found" in failure_reason.lower():
        return "Install Nextflow or configure --nextflow-bin, then rerun with --resume."
    if failed_tasks:
        modules = sorted({row.get("process", "") for row in failed_tasks if row.get("process")})
        return "Inspect failed process logs, then rerun with --resume" + (f"; failed processes: {', '.join(modules)}" if modules else ".")
    return "Inspect .nextflow.log and rerun with --resume after fixing the reported input or environment issue."


def _rel(project_dir: Path, path: Path) -> str:
    return path.relative_to(project_dir).as_posix()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
