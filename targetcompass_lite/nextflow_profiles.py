import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .container_plane import container_build_result_path
from .nextflow_plane import MODULE_CONTRACTS
from .v4 import content_hash


PROFILE_MATRIX_SCHEMA = "v4.nextflow_profile_matrix/0.1"
RESOURCE_VALIDATION_SCHEMA = "v4.nextflow_resource_policy_validation/0.1"


def profile_matrix_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "execution_profile_matrix.json"


def resource_validation_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "resource_policy_validation.json"


def build_nextflow_profile_matrix(project_dir: Path) -> dict[str, Any]:
    build = _read_json(container_build_result_path(project_dir), {})
    digest = build.get("digest", "")
    immutable_ref = build.get("immutable_ref", "")
    image = immutable_ref or build.get("image", "targetcompass-lite:local")
    matrix = {
        "local": {"executor": "local", "container_engine": "none", "requires_digest": False, "queue": "", "image": ""},
        "docker": {"executor": "local", "container_engine": "docker", "requires_digest": True, "queue": "", "image": image},
        "apptainer": {"executor": "local", "container_engine": "apptainer", "requires_digest": True, "queue": "", "image": image},
        "slurm": {"executor": "slurm", "container_engine": "apptainer", "requires_digest": True, "queue": "default", "image": image},
    }
    payload = {
        "schema_version": PROFILE_MATRIX_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "container_digest": digest,
        "immutable_image": immutable_ref,
        "profiles": matrix,
        "policy": {
            "shared_or_hpc_profiles_require_digest": True,
            "local_profile_may_run_without_container": True,
            "slurm_requires_queue_and_time_limits": True,
        },
        "matrix_hash": content_hash({"profiles": matrix, "digest": digest}),
    }
    path = profile_matrix_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def validate_nextflow_resource_policy(project_dir: Path, tasks: dict[str, Any] | None = None) -> dict[str, Any]:
    from .nextflow_runner import build_nextflow_tasks

    matrix = build_nextflow_profile_matrix(project_dir)
    tasks = tasks or build_nextflow_tasks(project_dir)
    issues = []
    for profile, row in matrix.get("profiles", {}).items():
        if row.get("requires_digest") and not matrix.get("container_digest"):
            issues.append({"profile": profile, "severity": "review", "reason": "container digest is required before shared/HPC execution"})
        if row.get("executor") == "slurm" and not row.get("queue"):
            issues.append({"profile": profile, "severity": "failed", "reason": "slurm queue is required"})
    for task in tasks.get("tasks", []):
        resources = task.get("resources", {})
        if int(resources.get("cpus", 0) or 0) < 1:
            issues.append({"task_id": task.get("task_id", ""), "severity": "failed", "reason": "cpus must be >= 1"})
        if not str(resources.get("memory", "")).strip():
            issues.append({"task_id": task.get("task_id", ""), "severity": "failed", "reason": "memory is required"})
        if not str(resources.get("time", "")).strip():
            issues.append({"task_id": task.get("task_id", ""), "severity": "failed", "reason": "time is required"})
    failed = [row for row in issues if row.get("severity") == "failed"]
    status = "failed" if failed else ("review" if issues else "pass")
    payload = {
        "schema_version": RESOURCE_VALIDATION_SCHEMA,
        "project_id": project_dir.name,
        "status": status,
        "task_count": tasks.get("task_count", 0),
        "issues": issues,
        "profile_matrix": "workflows/target_discovery/execution_profile_matrix.json",
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = resource_validation_path(project_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
