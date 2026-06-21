import json
import shutil
import os
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


def write_apptainer_recipe(project_dir: Path, base_image: str = "python:3.11-slim") -> Path:
    build_nextflow_execution_plane(project_dir, base_image=base_image)
    path = apptainer_recipe_path(project_dir)
    path.write_text(
        f"""Bootstrap: docker
From: {base_image}

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
    base_image: str = "python:3.11-slim",
    build_args: dict[str, str] | None = None,
    network: str = "",
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    build_nextflow_execution_plane(project_dir, base_image=base_image)
    build_container_mount_policy(project_dir)
    write_apptainer_recipe(project_dir, base_image=base_image)
    dockerfile = project_dir / "workflows" / "target_discovery" / "Dockerfile.targetcompass-lite"
    resolved = resolve_docker_bin(docker_bin)
    command = [resolved or docker_bin, "build"]
    if network:
        command.extend(["--network", network])
    command.extend(["--build-arg", f"TARGETCOMPASS_BASE_IMAGE={base_image}"])
    for key, value in sorted((build_args or {}).items()):
        command.extend(["--build-arg", f"{key}={value}"])
    command.extend(["-f", str(dockerfile), "-t", image_tag, "."])
    if runner is None and not resolved:
        return _write_build_result(project_dir, image_tag, base_image, command, 127, "", f"Docker executable not found: {docker_bin}", "")
    completed = (runner or _default_runner)(command, project_dir)
    digest = ""
    if completed.returncode == 0:
        digest = inspect_image_digest(project_dir, image_tag, docker_bin=docker_bin, runner=runner).get("digest", "")
    return _write_build_result(project_dir, image_tag, base_image, command, completed.returncode, completed.stdout, completed.stderr, digest)


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
        resolved = shutil.which(docker_bin)
        if resolved:
            return resolved
        if Path(docker_bin).exists():
            return docker_bin
        if docker_bin.lower() in {"docker", "docker.exe"}:
            common = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
            return str(common) if common.exists() else ""
        return ""
    found = shutil.which("docker")
    if found:
        return found
    common = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
    return str(common) if common.exists() else ""


def _write_build_result(
    project_dir: Path,
    image_tag: str,
    base_image: str,
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
        "base_image": base_image,
        "digest": digest,
        "immutable_ref": f"{image_tag.split(':', 1)[0]}@{digest}" if digest else "",
        "status": "success" if returncode == 0 else "failed",
        "returncode": returncode,
        "command": command,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "failure_reason": "" if returncode == 0 else (stderr or stdout or f"docker build exited with {returncode}"),
        "recovery": _docker_recovery(returncode, stdout, stderr),
        "mount_policy": str(container_policy_path(project_dir).relative_to(project_dir)).replace("\\", "/"),
        "apptainer_recipe": str(apptainer_recipe_path(project_dir).relative_to(project_dir)).replace("\\", "/"),
        "build_hash": content_hash({"image": image_tag, "base_image": base_image, "digest": digest, "command": command, "returncode": returncode}),
        "finished_at": _now(),
    }
    path = container_build_result_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _default_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    docker_bin_dir = Path("C:/Program Files/Docker/Docker/resources/bin")
    if docker_bin_dir.exists():
        env["PATH"] = f"{docker_bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def _docker_recovery(returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
    if returncode == 0:
        return {"recoverable": False, "category": "", "actions": []}
    text = f"{stderr}\n{stdout}".lower()
    actions: list[str] = []
    category = "docker_build_failed"
    if "docker executable not found" in text:
        category = "missing_docker"
        actions.append("Install Docker Desktop or pass --docker-bin with an absolute docker.exe path.")
    if "docker-credential" in text:
        category = "docker_cli_path"
        actions.append("Add Docker Desktop resources/bin to PATH or use the bundled resolver.")
    if "no https proxy" in text or "connectex" in text or "method not allowed" in text:
        category = "registry_network"
        actions.append("Configure Docker Desktop with a real HTTP proxy that supports HTTPS CONNECT, not the FlClash service/control port.")
        actions.append("Verify with: curl -I -x http://127.0.0.1:<proxy_port> https://registry-1.docker.io/v2/")
        actions.append("Retry with --base-image set to an already mirrored or locally available Python image if Docker Hub is blocked.")
    if "failed to resolve source metadata" in text or "failed to resolve reference" in text:
        category = "base_image_unavailable"
        actions.append("Pull or mirror the base image first, then rerun container-build.")
    if not actions:
        actions.append("Open workflows/target_discovery/container_build_result.json and inspect stdout_tail/stderr_tail.")
    return {"recoverable": True, "category": category, "actions": actions}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
