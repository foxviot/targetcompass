from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def publish_output_artifacts(
    project_dir: Path,
    paths: list[str | Path],
    *,
    producer: str,
    artifact_type: str,
    task_id: str = "",
    subquestion_id: str = "",
    qc_status: str = "pass",
) -> dict[str, Any]:
    """Publish mature v4/local outputs through the v5 backend abstraction.

    This keeps the existing local file contract intact while making the object
    store and Artifact Registry a first-class write path for generated outputs.
    """
    published = []
    failures = []
    try:
        from targetcompass_lite.canonical.artifacts import register_artifact
    except Exception as exc:  # pragma: no cover - defensive for packaging edge cases
        return {"status": "skipped", "failure_reason": str(exc), "published": [], "failures": []}

    for item in paths:
        path = Path(item)
        absolute = path if path.is_absolute() else project_dir / path
        if not absolute.exists() or not absolute.is_file():
            failures.append({"path": str(item).replace("\\", "/"), "reason": "missing file"})
            continue
        rel = _rel(absolute, project_dir)
        try:
            manifest = register_artifact(
                project_dir,
                rel,
                producer=producer,
                artifact_type=artifact_type,
                expected_by_task_ids=[task_id or producer],
                supports_subquestion_ids=[subquestion_id] if subquestion_id else [],
                producer_run_id=producer,
                qc_status=qc_status,
                limitations=[] if qc_status == "pass" else ["Output artifact requires review."],
            )
            published.append(
                {
                    "path": rel,
                    "artifact_id": manifest["artifact_id"],
                    "artifact_store_id": manifest.get("artifact_store_id", ""),
                    "object_uri": manifest.get("object_uri", ""),
                    "artifact_store_status": manifest.get("artifact_store_status", ""),
                    "checksum_sha256": manifest.get("checksum_sha256", ""),
                }
            )
        except Exception as exc:  # do not break mature analysis modules on publish failure
            failures.append({"path": rel, "reason": str(exc)})
    summary = {
        "schema_version": "v5.output_backend_publish/0.1",
        "project_id": project_dir.name,
        "producer": producer,
        "artifact_type": artifact_type,
        "published_count": len(published),
        "failure_count": len(failures),
        "published": published,
        "failures": failures,
        "status": "PASS" if published and not failures else "WARN" if published else "SKIPPED",
    }
    _append_publish_log(project_dir, summary)
    return summary


def _append_publish_log(project_dir: Path, row: dict[str, Any]) -> None:
    path = project_dir / "v5" / "storage" / "output_backend_publish.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
