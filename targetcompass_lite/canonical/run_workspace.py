from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .schemas import now_iso


RUN_WORKSPACE_SCHEMA_VERSION = "v5.run_workspace/0.1"


def snapshot_v5_run_workspace(project_dir: str | Path, run_id: str, refs: list[str], *, question: str = "") -> dict[str, Any]:
    project_dir = Path(project_dir)
    safe_run_id = _safe_run_id(run_id)
    run_dir = project_dir / "v5" / "runs" / safe_run_id
    files_dir = run_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    missing: list[str] = []
    for ref in _dedupe_refs(refs):
        src = project_dir / ref
        if not src.exists() or not src.is_file():
            missing.append(ref)
            continue
        dst = files_dir / ref
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append({"source_ref": ref, "snapshot_ref": str(dst.relative_to(project_dir)).replace("\\", "/"), "size_bytes": dst.stat().st_size})
    manifest = {
        "schema_version": RUN_WORKSPACE_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "run_id": safe_run_id,
        "question": question,
        "created_at": now_iso(),
        "run_dir": str(run_dir.relative_to(project_dir)).replace("\\", "/"),
        "copied_count": len(copied),
        "missing_count": len(missing),
        "copied_refs": copied,
        "missing_refs": missing,
        "purpose": "Project-scoped immutable snapshot for one v5 question/run. Runtime modules may still write global working outputs, but this snapshot preserves the result space for review and export.",
    }
    (run_dir / "run_workspace_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return manifest


def _safe_run_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip())
    return cleaned[:120] or "v5_run"


def _dedupe_refs(refs: list[str]) -> list[str]:
    out = []
    seen = set()
    for ref in refs:
        clean = str(ref).replace("\\", "/").strip()
        if clean and clean not in seen and not clean.startswith("../") and ":/" not in clean:
            out.append(clean)
            seen.add(clean)
    return out
