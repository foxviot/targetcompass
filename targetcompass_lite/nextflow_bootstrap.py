import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash


BOOTSTRAP_SCHEMA = "v4.nextflow_bootstrap/0.1"


JRE17_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jre/hotspot/normal/eclipse?project=jdk"


def bootstrap_nextflow(
    project_dir: Path,
    download: bool = False,
    nextflow_url: str = "https://get.nextflow.io",
    install_runtime: bool = False,
) -> dict[str, Any]:
    tools = project_dir / "tools" / "nextflow"
    tools.mkdir(parents=True, exist_ok=True)
    install_error = ""
    if install_runtime:
        try:
            _install_project_jre17(project_dir)
        except Exception as exc:
            install_error = str(exc)
    java = _java_status(project_dir)
    existing = shutil.which("nextflow") or _local_nextflow_path(project_dir)
    downloaded = ""
    download_error = ""
    if download and not existing:
        try:
            downloaded = _download_nextflow(tools, nextflow_url)
            existing = downloaded
        except Exception as exc:
            download_error = str(exc)
    executable = existing or ""
    nf_version = _nextflow_version(project_dir, executable, java.get("java_bin", "")) if executable else {"status": "missing", "detail": "nextflow executable not found"}
    status = "ready" if executable and java.get("usable_for_nextflow") and nf_version.get("status") == "ready" else "blocked"
    payload = {
        "schema_version": BOOTSTRAP_SCHEMA,
        "project_id": project_dir.name,
        "status": status,
        "generated_at": _now(),
        "java": java,
        "nextflow": {
            "executable": executable,
            "version_status": nf_version,
            "downloaded": downloaded,
            "download_error": download_error,
            "local_tools_dir": str(tools.relative_to(project_dir)).replace("\\", "/"),
        },
        "runtime_install_error": install_error,
        "recovery": _recovery(java, executable, download_error),
    }
    payload["bootstrap_hash"] = content_hash(payload)
    out = project_dir / "workflows" / "target_discovery" / "nextflow_bootstrap.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def resolve_nextflow_bin(project_dir: Path, prefer_wsl: bool = True) -> str:
    if prefer_wsl and _local_nextflow_path(project_dir):
        return "wsl"
    return shutil.which("nextflow") or _local_nextflow_path(project_dir) or "nextflow"


def _java_status(project_dir: Path) -> dict[str, Any]:
    candidates = []
    project_java = _project_java_bin(project_dir)
    if project_java:
        candidates.append(project_java)
    if shutil.which("java"):
        candidates.append(shutil.which("java"))
    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        candidates.append(str(Path(java_home) / "bin" / "java.exe"))
        candidates.append(str(Path(java_home) / "bin" / "java"))
    seen = []
    for candidate in candidates:
        if candidate and candidate not in seen and Path(candidate).exists():
            seen.append(candidate)
    for candidate in seen:
        command, env = _java_command(project_dir, candidate)
        result = subprocess.run(command + ["-version"], text=True, capture_output=True, check=False, env=env)
        text = (result.stderr or result.stdout or "").strip()
        major = _java_major(text)
        return {
            "status": "found" if result.returncode == 0 else "failed",
            "java_bin": candidate,
            "version_text": text.splitlines()[0] if text else "",
            "major_version": major,
            "usable_for_nextflow": bool(major and major >= 11),
            "minimum_recommended_major": 17,
        }
    return {
        "status": "missing",
        "java_bin": "",
        "version_text": "",
        "major_version": 0,
        "usable_for_nextflow": False,
        "minimum_recommended_major": 17,
    }


def _java_major(text: str) -> int:
    marker = 'version "'
    if marker not in text:
        return 0
    version = text.split(marker, 1)[1].split('"', 1)[0]
    if version.startswith("1."):
        try:
            return int(version.split(".")[1])
        except (IndexError, ValueError):
            return 0
    try:
        return int(version.split(".")[0])
    except ValueError:
        return 0


def _download_nextflow(tools: Path, url: str) -> str:
    target = tools / "nextflow"
    raw = _download_bytes(url, timeout=60)
    target.write_bytes(raw)
    target.chmod(0o755)
    return str(target)


def _download_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.4 local nextflow bootstrap"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _local_nextflow_path(project_dir: Path) -> str:
    for name in ["nextflow", "nextflow.bat"]:
        path = project_dir / "tools" / "nextflow" / name
        if path.exists():
            return str(path)
    return ""


def _nextflow_version(project_dir: Path, executable: str, java_bin: str) -> dict[str, Any]:
    if not executable:
        return {"status": "missing", "detail": "nextflow executable not found"}
    try:
        command, env = _nextflow_command(project_dir, executable, java_bin)
        result = subprocess.run(command + ["-version"], text=True, capture_output=True, check=False, timeout=120, env=env)
        text = (result.stdout or result.stderr or "").strip()
        return {
            "status": "ready" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "version_text": text[-1000:],
        }
    except Exception as exc:
        return {"status": "failed", "detail": str(exc)}


def _install_project_jre17(project_dir: Path) -> Path:
    java_root = project_dir / "tools" / "java17"
    java_bin = _project_java_bin(project_dir)
    if java_bin:
        return Path(java_bin)
    archive = project_dir / "tools" / "java17-linux-x64-jre.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        archive.write_bytes(_download_bytes(JRE17_URL, timeout=300))
    extract_dir = project_dir / "tools" / "java17_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(extract_dir)
    candidates = list(extract_dir.glob("*/bin/java"))
    if not candidates:
        raise RuntimeError("Downloaded JRE archive did not contain bin/java")
    source_root = candidates[0].parents[1]
    if java_root.exists():
        shutil.rmtree(java_root)
    shutil.move(str(source_root), str(java_root))
    shutil.rmtree(extract_dir, ignore_errors=True)
    return java_root / "bin" / "java"


def _project_java_bin(project_dir: Path) -> str:
    path = project_dir / "tools" / "java17" / "bin" / "java"
    return str(path) if path.exists() else ""


def _wsl_path(path: Path | str) -> str:
    raw = str(path).replace("\\", "/")
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower()
        return f"/mnt/{drive}{raw[2:]}"
    return raw


def _java_command(project_dir: Path, java_bin: str) -> tuple[list[str], dict[str, str] | None]:
    if _is_windows_path(java_bin) or str(java_bin).startswith(str(project_dir)):
        return ["wsl.exe", "-d", "Ubuntu", "--", _wsl_path(java_bin)], None
    return [java_bin], None


def _nextflow_command(project_dir: Path, executable: str, java_bin: str) -> tuple[list[str], dict[str, str] | None]:
    if _is_windows_path(executable) or str(executable).startswith(str(project_dir)):
        java_home = str(Path(java_bin).parents[1]) if java_bin else str(project_dir / "tools" / "java17")
        script = f"export JAVA_HOME='{_wsl_path(java_home)}'; export PATH=\"$JAVA_HOME/bin:$PATH\"; chmod +x '{_wsl_path(executable)}'; exec '{_wsl_path(executable)}' \"$@\""
        return ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc", script, "nextflow"], None
    env = os.environ.copy()
    if java_bin:
        env["PATH"] = str(Path(java_bin).parent) + os.pathsep + env.get("PATH", "")
    return [executable], env


def _is_windows_path(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":"


def _recovery(java: dict[str, Any], executable: str, download_error: str) -> list[str]:
    actions = []
    if not java.get("java_bin"):
        actions.append("Install Java 17+ and ensure java is on PATH or JAVA_HOME points to it.")
    elif not java.get("usable_for_nextflow"):
        actions.append(f"Current Java is {java.get('version_text', 'unknown')}; install Java 17+ for modern Nextflow.")
    if not executable:
        actions.append("Install Nextflow or run python tc_lite.py nextflow-bootstrap --project <project> --download after Java 17+ is available.")
    if download_error:
        actions.append("Nextflow download failed; check network/proxy and retry.")
    if not actions:
        actions.append("Nextflow is ready for local profile execution.")
    return actions


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
