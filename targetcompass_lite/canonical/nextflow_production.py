from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .nextflow_execution import run_nextflow_task_packet
from .schemas import now_iso


NEXTFLOW_PRODUCTION_SCHEMA = "v5.nextflow_production_run/0.1"
MODULE_PROFILES = {
    "bulk_deg": {"module_id": "bulk_deg_v1", "profiles": ["local", "docker", "apptainer", "slurm"], "default_cpus": 2, "default_memory": "4 GB", "default_time": "2h"},
    "scrna_pseudobulk": {"module_id": "scrna_pseudobulk_v1", "profiles": ["local", "docker", "apptainer", "slurm"], "default_cpus": 4, "default_memory": "12 GB", "default_time": "6h"},
    "enrichment": {"module_id": "enrichment_v2", "profiles": ["local", "docker", "apptainer", "slurm"], "default_cpus": 2, "default_memory": "6 GB", "default_time": "2h"},
}


def build_nextflow_module_profiles(project_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    from targetcompass_lite.nextflow_profiles import build_nextflow_profile_matrix, validate_nextflow_resource_policy
    from targetcompass_lite.nextflow_runner import build_nextflow_tasks

    tasks = build_nextflow_tasks(project_dir)
    matrix = build_nextflow_profile_matrix(project_dir)
    validation = validate_nextflow_resource_policy(project_dir, tasks)
    payload = {
        "schema_version": "v5.nextflow_module_profiles/0.1",
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "module_profiles": MODULE_PROFILES,
        "execution_profile_matrix_ref": "workflows/target_discovery/execution_profile_matrix.json",
        "resource_policy_validation_ref": "workflows/target_discovery/resource_policy_validation.json",
        "profile_matrix_status": "ready",
        "resource_policy_status": validation.get("status", ""),
        "task_count": tasks.get("task_count", 0),
        "available_profiles": sorted((matrix.get("profiles") or {}).keys()),
        "hpc_container_policy": {
            "docker": "requires immutable digest before shared execution",
            "apptainer": "requires immutable digest and recipe for HPC",
            "slurm": "requires queue/time/memory policy plus apptainer image",
        },
    }
    out = project_dir / "v5" / "nextflow" / "module_profiles.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def run_nextflow_production_validation(
    project_dir: str | Path,
    task_packet: dict[str, Any],
    *,
    profile: str = "local",
    nextflow_bin: str = "nextflow",
    runner: Callable | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    module_profiles = build_nextflow_module_profiles(project_dir)
    first = run_nextflow_task_packet(project_dir, task_packet, profile=profile, nextflow_bin=nextflow_bin, resume=False, runner=runner)
    retry = None
    if first.get("task_run", {}).get("result_status") == "failed":
        retry = run_nextflow_task_packet(project_dir, task_packet, profile=profile, nextflow_bin=nextflow_bin, resume=True, runner=runner)
    payload = {
        "schema_version": NEXTFLOW_PRODUCTION_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "module_profiles_ref": "v5/nextflow/module_profiles.json",
        "profile": profile,
        "module_profiles_status": module_profiles.get("resource_policy_status", ""),
        "first_run_ref": first.get("task_run", {}).get("manifest_ref", ""),
        "first_run_status": first.get("task_run", {}).get("result_status", ""),
        "resume_run_ref": (retry or {}).get("task_run", {}).get("manifest_ref", ""),
        "resume_run_status": (retry or {}).get("task_run", {}).get("result_status", ""),
        "resume_validated": bool(retry),
        "recovery": first.get("task_run", {}).get("recovery", {}),
        "status": "completed" if (retry or first).get("task_run", {}).get("result_status") == "completed" else "review_required",
    }
    out = project_dir / "v5" / "nextflow" / "production_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload
