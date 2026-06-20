import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def artifact_id(project_dir: Path, relative_path: str) -> str:
    path = project_dir / relative_path
    payload = relative_path
    if path.exists():
        payload += "|" + hashlib.sha256(path.read_bytes()).hexdigest()
    return "artifact_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def resume_key(module_id: str, inputs: dict[str, str], parameters: dict[str, Any]) -> str:
    payload = json.dumps({"module_id": module_id, "inputs": inputs, "parameters": parameters}, sort_keys=True, ensure_ascii=False)
    return "resume_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_executor_contract(
    project_dir: Path,
    module_id: str,
    runner: str,
    inputs: dict[str, str],
    parameters: dict[str, Any],
    expected_outputs: list[str],
    backend: str = "local_python_r",
    nextflow_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_hashes = {}
    for name, rel in inputs.items():
        path = project_dir / rel
        input_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    return {
        "schema_version": "executor_contract_v1",
        "module_id": module_id,
        "backend": backend,
        "runner": runner,
        "inputs": inputs,
        "input_hashes": input_hashes,
        "parameters": parameters,
        "expected_outputs": expected_outputs,
        "resume_key": resume_key(module_id, inputs, parameters),
        "nextflow_compatible": {
            "module_contract": f"nextflow/{module_id}.contract.yaml",
            "profile": "local",
            "container_digest": "",
            **(nextflow_hint or {}),
        },
    }


def run_local_executor(
    project_dir: Path,
    out_dir: Path,
    contract: dict[str, Any],
    operation: Callable[[], Any],
) -> tuple[Any, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "executor_artifact_manifest_v1",
        "module_id": contract["module_id"],
        "backend": contract["backend"],
        "runner": contract["runner"],
        "status": "running",
        "started_at": started,
        "finished_at": "",
        "failure_reason": "",
        "resume_key": contract["resume_key"],
        "contract": contract,
        "artifacts": [],
    }
    _write_manifest(out_dir, manifest)
    try:
        result = operation()
        artifacts = []
        for rel in contract.get("expected_outputs", []):
            path = project_dir / rel
            if path.exists():
                artifacts.append(
                    {
                        "path": rel,
                        "artifact_id": artifact_id(project_dir, rel),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        manifest.update(
            {
                "status": "success",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "artifacts": artifacts,
            }
        )
        _write_manifest(out_dir, manifest)
        return result, manifest
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "failure_reason": str(exc),
            }
        )
        _write_manifest(out_dir, manifest)
        raise


def _write_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    (out_dir / "executor_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
