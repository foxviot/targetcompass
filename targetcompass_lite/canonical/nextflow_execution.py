from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from targetcompass_lite.artifact_store import put_artifact

from .artifacts import register_artifact, validate_artifact_for_evidence
from .backend_writer import write_json_artifact
from .ids import make_stable_id
from .schemas import CANONICAL_SCHEMA_VERSION, now_iso
from .task_packets import validate_task_packets


NEXTFLOW_EXECUTION_SCHEMA_VERSION = "v5.nextflow_execution/0.1"


def run_nextflow_task_packet(
    project_dir: str | Path,
    task_packet: dict[str, Any],
    *,
    profile: str = "local",
    module_ids: list[str] | None = None,
    nextflow_bin: str = "nextflow",
    resume: bool = False,
    runner: Callable | None = None,
) -> dict[str, Any]:
    """Run the existing v4 Nextflow runner and record canonical v5 run/QC/artifacts."""
    project_dir = Path(project_dir)
    _validate_analysis_task_packet(task_packet)

    from targetcompass_lite.nextflow_runner import run_nextflow_local

    manifest = run_nextflow_local(
        project_dir,
        profile=profile,
        module_ids=module_ids or task_packet.get("module_ids") or _module_ids_from_packet(task_packet),
        nextflow_bin=nextflow_bin,
        resume=resume,
        runner=runner,
    )
    return record_nextflow_execution(project_dir, task_packet, manifest)


def record_nextflow_execution(project_dir: str | Path, task_packet: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    project_dir = Path(project_dir)
    task_id = task_packet["task_id"]
    task_run_id = make_stable_id(
        "task_run",
        {
            "task_id": task_id,
            "attempt_id": manifest.get("attempt_id", ""),
            "status": manifest.get("status", ""),
            "tasks_hash": manifest.get("tasks_hash", ""),
        },
    )
    artifact_manifests = []
    for artifact_path in manifest.get("artifacts", []):
        artifact_manifests.append(
            register_artifact(
                project_dir,
                artifact_path,
                producer=task_id,
                artifact_type=_artifact_type_for_path(artifact_path),
                expected_by_task_ids=[task_id],
                supports_subquestion_ids=[task_packet.get("subquestion_id", "")],
                producer_run_id=task_run_id,
                qc_status="pass" if manifest.get("status") == "success" else "failed",
                limitations=[] if manifest.get("status") == "success" else [manifest.get("failure_reason", "Nextflow run failed.")],
            )
        )

    artifact_errors = []
    for artifact in artifact_manifests:
        artifact_errors.extend(validate_artifact_for_evidence(artifact))
    object_store_records = _register_nextflow_run_outputs(project_dir, task_id, manifest)
    status = "completed" if manifest.get("status") == "success" and not artifact_errors else "failed"
    if manifest.get("status") != "success":
        status = "failed"

    task_run = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "task_run_id": task_run_id,
        "project_id": project_dir.name,
        "task_id": task_id,
        "executor": "nextflow",
        "result_status": status,
        "artifact_refs": [artifact["artifact_id"] for artifact in artifact_manifests],
        "manifest_ref": _write_nextflow_manifest_copy(project_dir, task_run_id, manifest),
        "nextflow_attempt_id": manifest.get("attempt_id", ""),
        "returncode": manifest.get("returncode"),
        "failure_reason": manifest.get("failure_reason", ""),
        "recovery": manifest.get("recovery", {}),
        "created_at": now_iso(),
        "status": "recorded",
    }
    qc_report = build_nextflow_qc_report(project_dir, task_packet, task_run, manifest, artifact_manifests, artifact_errors)
    task_run["qc_report_ref"] = qc_report["qc_report_id"]
    _write_json(project_dir / "v5" / "task_runs" / f"{task_run_id}.json", task_run)
    _write_json(project_dir / "v5" / "qc_reports" / f"{qc_report['qc_report_id']}.json", qc_report)
    bundle = {
        "schema_version": NEXTFLOW_EXECUTION_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "task_run": task_run,
        "qc_report": qc_report,
        "artifacts": artifact_manifests,
        "object_store_records": object_store_records,
        "recorded_at": now_iso(),
    }
    _write_json(project_dir / "v5" / "nextflow" / f"nextflow_execution_{task_run_id}.json", bundle)
    return bundle


def build_nextflow_qc_report(
    project_dir: str | Path,
    task_packet: dict[str, Any],
    task_run: dict[str, Any],
    manifest: dict[str, Any],
    artifacts: list[dict[str, Any]],
    artifact_errors: list[str],
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    checks = [
        {
            "check_id": "nextflow_returncode",
            "status": "pass" if manifest.get("returncode") == 0 else "fail",
            "message": f"returncode={manifest.get('returncode')}",
        },
        {
            "check_id": "nextflow_manifest_status",
            "status": "pass" if manifest.get("status") == "success" else "fail",
            "message": manifest.get("failure_reason") or manifest.get("status", ""),
        },
        {
            "check_id": "artifact_presence",
            "status": "pass" if artifacts and not artifact_errors else "fail",
            "message": "; ".join(artifact_errors) if artifact_errors else f"{len(artifacts)} artifact(s) registered",
        },
    ]
    failed_trace = manifest.get("recovery", {}).get("failed_tasks", [])
    if failed_trace:
        checks.append({"check_id": "nextflow_trace_failures", "status": "fail", "message": json.dumps(failed_trace, ensure_ascii=False)})
    overall_status = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "qc_report_id": make_stable_id("qc_report", {"task_run_id": task_run["task_run_id"], "checks": checks}),
        "project_id": project_dir.name,
        "task_id": task_packet["task_id"],
        "task_run_id": task_run["task_run_id"],
        "overall_status": overall_status,
        "checks": checks,
        "created_at": now_iso(),
        "status": "recorded",
    }


def load_task_runs(project_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(project_dir) / "v5" / "task_runs"
    if not path.exists():
        return []
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]


def load_qc_reports(project_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(project_dir) / "v5" / "qc_reports"
    if not path.exists():
        return []
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]


def _validate_analysis_task_packet(task_packet: dict[str, Any]) -> None:
    errors = validate_task_packets([task_packet])
    if errors:
        raise ValueError("; ".join(errors))
    if task_packet.get("packet_type") != "AnalysisTaskPacket":
        raise ValueError("Nextflow execution requires AnalysisTaskPacket")


def _module_ids_from_packet(task_packet: dict[str, Any]) -> list[str] | None:
    module_id = task_packet.get("module_id") or task_packet.get("nextflow_module_id")
    if module_id:
        return [module_id]
    method = str(task_packet.get("method_name", "")).lower()
    mapping = {
        "bulk_deg": "bulk_deg_v1",
        "scrna_pseudobulk": "scrna_pseudobulk_v1",
        "enrichment": "enrichment_v2",
        "meta_analysis": "deg_meta_analysis_v1",
        "genetic_coloc_mr": "genetic_coloc_mr_v1",
    }
    for key, value in mapping.items():
        if key in method:
            return [value]
    return None


def _artifact_type_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".html", ".htm"}:
        return "nextflow_report"
    if suffix in {".txt", ".log"}:
        return "nextflow_log"
    if suffix in {".csv", ".tsv"}:
        return "nextflow_table"
    return "nextflow_artifact"


def _write_nextflow_manifest_copy(project_dir: Path, task_run_id: str, manifest: dict[str, Any]) -> str:
    relative = Path("v5") / "nextflow" / f"nextflow_manifest_{task_run_id}.json"
    _write_json(project_dir / relative, manifest)
    return relative.as_posix()


def _register_nextflow_run_outputs(project_dir: Path, task_id: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Register concrete Nextflow run outputs in the primary ArtifactStore.

    The canonical artifact registry records selected execution metadata, while
    the storage production gate checks all mature files under results/reports.
    Registering the run output directory here prevents each real Nextflow run
    from reintroducing untracked legacy artifacts.
    """
    run_dir = manifest.get("run_dir", "")
    if not run_dir:
        return []
    results_dir = project_dir / run_dir / "results"
    if not results_dir.exists():
        return []
    records = []
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir).as_posix()
        records.append(put_artifact(project_dir, rel, producer=task_id, artifact_type=_artifact_type_for_path(rel)))
    return records


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    project_dir = _project_dir_from_v5_path(path)
    write_json_artifact(project_dir, path.relative_to(project_dir), payload, producer="nextflow_execution", artifact_type="nextflow_execution_json")


def _project_dir_from_v5_path(path: Path) -> Path:
    parts = path.parts
    if "v5" in parts:
        return Path(*parts[: parts.index("v5")])
    return path.parent
