from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGING_PROFILE_SCHEMA = "v5.packaging_profile/0.1"
DEPENDENCY_CACHE_SCHEMA = "v5.dependency_cache_manifest/0.1"
REPAIR_PLAN_SCHEMA = "v5.runtime_repair_plan/0.1"


PROFILES: dict[str, dict[str, Any]] = {
    "professor_demo": {
        "display_name": "Professor demo package",
        "description": "Small guided demo package with one curated project and no historical run bulk.",
        "include_tests": False,
        "include_docs": True,
        "include_demo_outputs": True,
        "include_dev_artifacts": False,
        "include_external_agent_runs": False,
        "include_historical_projects": False,
    },
    "developer": {
        "display_name": "Developer package",
        "description": "Full local development package with tests, docs, schemas, and default demo project.",
        "include_tests": True,
        "include_docs": True,
        "include_demo_outputs": True,
        "include_dev_artifacts": True,
        "include_external_agent_runs": False,
        "include_historical_projects": False,
    },
}


def build_packaging_profile(profile: str = "professor_demo") -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown packaging profile: {profile}")
    payload = {
        "schema_version": PACKAGING_PROFILE_SCHEMA,
        "profile": profile,
        "created_at": _now(),
        **PROFILES[profile],
        "always_excluded": [
            ".git",
            "__pycache__",
            ".pytest_cache",
            "node_modules",
            "dist",
            "configs/secrets.local.json",
            "external_agent_runs/*/mock_run",
        ],
        "demo_project": "projects/vascular_aging_demo",
    }
    return payload


def build_dependency_cache_manifest(root: Path) -> dict[str, Any]:
    runtime_cache = root / "packaging" / "windows_v5" / "runtime_cache"
    wheelhouse = root / "packaging" / "windows_v5" / "wheelhouse"
    docker_cache = runtime_cache / "docker_images"
    r_cache = runtime_cache / "r_packages"
    nextflow_cache = runtime_cache / "nextflow"
    payload = {
        "schema_version": DEPENDENCY_CACHE_SCHEMA,
        "created_at": _now(),
        "python": {
            "embedded_zip_present": bool(list(runtime_cache.glob("python-*-embed-amd64.zip"))) if runtime_cache.exists() else False,
            "get_pip_present": (runtime_cache / "get-pip.py").exists(),
            "wheel_count": len(list(wheelhouse.glob("*.whl"))) if wheelhouse.exists() else 0,
            "wheelhouse": _display(wheelhouse, root),
        },
        "r": {
            "cache_dir": _display(r_cache, root),
            "package_count": len(list(r_cache.glob("*"))) if r_cache.exists() else 0,
            "status": "CACHE_PRESENT" if r_cache.exists() and any(r_cache.iterdir()) else "NOT_CACHED",
        },
        "nextflow": {
            "cache_dir": _display(nextflow_cache, root),
            "nextflow_binary_present": any((nextflow_cache / name).exists() for name in ["nextflow", "nextflow.bat"]) if nextflow_cache.exists() else False,
            "jre_archive_present": bool(list(nextflow_cache.glob("*jre*.*"))) if nextflow_cache.exists() else False,
        },
        "docker": {
            "cache_dir": _display(docker_cache, root),
            "image_archive_count": len(list(docker_cache.glob("*.tar"))) + len(list(docker_cache.glob("*.tar.gz"))) if docker_cache.exists() else 0,
            "status": "CACHE_PRESENT" if docker_cache.exists() and any(docker_cache.iterdir()) else "NOT_CACHED",
        },
        "policy": {
            "offline_python_install_supported": True,
            "offline_r_install_supported": False,
            "offline_nextflow_install_supported": "partial_when_cache_populated",
            "offline_docker_image_load_supported": "partial_when_cache_populated",
        },
    }
    return payload


def build_runtime_repair_plan(root: Path, install_dir: Path | None = None) -> dict[str, Any]:
    install_dir = install_dir or Path("%LOCALAPPDATA%") / "TargetCompassV5"
    payload = {
        "schema_version": REPAIR_PLAN_SCHEMA,
        "created_at": _now(),
        "install_dir": str(install_dir).replace("\\", "/"),
        "repairs": [
            {
                "repair_id": "repair_python_dependencies",
                "label": "Repair Python dependencies",
                "risk": "low",
                "command": "python -m pip install -r requirements.txt or use wheelhouse when populated",
                "automated_by_installer": True,
            },
            {
                "repair_id": "repair_nextflow",
                "label": "Install/check Nextflow",
                "risk": "medium",
                "command": "python tc_lite.py nextflow-bootstrap --project vascular_aging_demo --download --install-runtime",
                "automated_by_installer": False,
            },
            {
                "repair_id": "repair_docker_backends",
                "label": "Start Docker and activate PostgreSQL/MinIO",
                "risk": "medium",
                "command": "python tc_lite.py local-backends-prepare --project vascular_aging_demo; start Docker Desktop; python tc_lite.py v5-backends-activate --project vascular_aging_demo",
                "automated_by_installer": False,
            },
            {
                "repair_id": "repair_rscript",
                "label": "Install/configure Rscript",
                "risk": "medium",
                "command": "Install R 4.x, then set Rscript path in v5 Setup Wizard.",
                "automated_by_installer": False,
            },
        ],
        "notes": [
            "Repair buttons may open commands or scripts; they should not silently install system software without user approval.",
            "Code signing requires a real certificate and is represented as installer metadata until a certificate is supplied.",
        ],
    }
    return payload


def write_packaging_manifests(root: Path, out_dir: Path, profile: str) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_manifest = build_packaging_profile(profile)
    dependency_manifest = build_dependency_cache_manifest(root)
    repair_plan = build_runtime_repair_plan(root)
    (out_dir / "packaging_profile.json").write_text(json.dumps(profile_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "dependency_cache_manifest.json").write_text(json.dumps(dependency_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "runtime_repair_plan.json").write_text(json.dumps(repair_plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"profile": profile_manifest, "dependencies": dependency_manifest, "repair_plan": repair_plan}


def copy_optional_asset(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
