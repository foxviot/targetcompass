import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .nextflow_plane import build_nextflow_execution_plane
from .v4 import content_hash


BUILD_SCHEMA = "v4.container_build_result/0.1"
POLICY_SCHEMA = "v4.container_mount_policy/0.1"

CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess]


def container_policy_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "container_mount_policy.json"


def container_build_result_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "container_build_result.json"


def apptainer_recipe_path(project_dir: Path) -> Path:
    return project_dir / "workflows" / "target_discovery" / "targetcompass-lite.def"


def build_container_mount_policy(project_dir: Path) -> dict[str, Any]:
    payload = {
        "schema_version": POLICY_SCHEMA,
        "project_id": project_dir.name,
        "mounts": [
            {"host": ".", "container": "/app", "mode": "ro", "purpose": "application code and project metadata"},
            {"host": f"projects/{project_dir.name}/data", "container": f"/data/{project_dir.name}", "mode": "ro", "purpose": "input matrices and metadata"},
            {"host": f"projects/{project_dir.name}/results", "container": f"/results/{project_dir.name}", "mode": "rw", "purpose": "analysis outputs"},
            {"host": f"projects/{project_dir.name}/workflows", "container": f"/workflows/{project_dir.name}", "mode": "rw", "purpose": "Nextflow work, reports, and trace files"},
        ],
        "policy": {
            "no_host_root_mount": True,
            "raw_private_data_must_be_under_project_data": True,
            "outputs_must_be_under_project_results_or_workflows": True,
            "network_disabled_by_default": True,
        },
        "generated_at": _now(),
    }
    path = container_policy_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def write_apptainer_recipe(project_dir: Path) -> Path:
    build_nextflow_execution_plane(project_dir)
    path = apptainer_recipe_path(project_dir)
    path.write_text(
        """Bootstrap: docker
From: python:3.11-slim

%post
    python -m pip install --no-cache-dir -U pip

%environment
    export PYTHONUNBUFFERED=1

%runscript
    cd /app
    exec python tc_lite.py "$@"
""",
        encoding="utf-8",
    )
    return path


def build_docker_image(
    project_dir: Path,
    image_tag: str = "targetcompass-lite:local",
    docker_bin: str = "auto",
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    build_nextflow_execution_plane(project_dir)
    build_container_mount_policy(project_dir)
    write_apptainer_recipe(project_dir)
    dockerfile = project_dir / "workflows" / "target_discovery" / "Dockerfile.targetcompass-lite"
    resolved = resolve_docker_bin(docker_bin)
    command = [resolved or docker_bin, "build", "-f", str(dockerfile), "-t", image_tag, "."]
    if runner is None and not resolved:
        return _write_build_result(project_dir, image_tag, command, 127, "", f"Docker executable not found: {docker_bin}", "")
    completed = (runner or _default_runner)(command, project_dir)
    digest = ""
    if completed.returncode == 0:
        digest = inspect_image_digest(project_dir, image_tag, docker_bin=docker_bin, runner=runner).get("digest", "")
    return _write_build_result(project_dir, image_tag, command, completed.returncode, completed.stdout, completed.stderr, digest)


def inspect_image_digest(
    project_dir: Path,
    image_tag: str = "targetcompass-lite:local",
    docker_bin: str = "auto",
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    resolved = resolve_docker_bin(docker_bin)
    command = [resolved or docker_bin, "image", "inspect", image_tag, "--format", "{{json .RepoDigests}}"]
    if runner is None and not resolved:
        return {"image": image_tag, "digest": "", "status": "missing_docker", "command": command}
    completed = (runner or _default_runner)(command, project_dir)
    digest = ""
    if completed.returncode == 0:
        try:
            values = json.loads(completed.stdout.strip() or "[]")
            digest = values[0].split("@", 1)[1] if values and "@" in values[0] else ""
        except Exception:
            digest = ""
    return {
        "image": image_tag,
        "digest": digest,
        "status": "success" if digest else "digest_unavailable",
        "command": command,
        "stdout_tail": completed.stdout[-1000:] if completed.returncode == 0 else "",
    }


def resolve_docker_bin(docker_bin: str = "auto") -> str:
    if docker_bin and docker_bin != "auto":
        return docker_bin if Path(docker_bin).exists() or shutil.which(docker_bin) else ""
    found = shutil.which("docker")
    if found:
        return found
    common = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
    return str(common) if common.exists() else ""


def _write_build_result(
    project_dir: Path,
    image_tag: str,
    command: list[str],
    returncode: int,
    stdout: str,
    stderr: str,
    digest: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": BUILD_SCHEMA,
        "project_id": project_dir.name,
        "image": image_tag,
        "digest": digest,
        "immutable_ref": f"{image_tag.split(':', 1)[0]}@{digest}" if digest else "",
        "status": "success" if returncode == 0 else "failed",
        "returncode": returncode,
        "command": command,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "failure_reason": "" if returncode == 0 else (stderr or stdout or f"docker build exited with {returncode}"),
        "mount_policy": str(container_policy_path(project_dir).relative_to(project_dir)).replace("\\", "/"),
        "apptainer_recipe": str(apptainer_recipe_path(project_dir).relative_to(project_dir)).replace("\\", "/"),
        "build_hash": content_hash({"image": image_tag, "digest": digest, "command": command, "returncode": returncode}),
        "finished_at": _now(),
    }
    path = container_build_result_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _default_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
