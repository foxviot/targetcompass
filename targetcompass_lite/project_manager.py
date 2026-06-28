from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import PROJECTS, ensure_project_dirs, project_path


PROJECT_REGISTRY_SCHEMA = "v5.project_registry/0.1"


def list_projects(root: Path = PROJECTS) -> dict[str, Any]:
    rows = []
    root.mkdir(parents=True, exist_ok=True)
    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        if not _looks_like_project(item):
            continue
        state = _read_json(item / "v5" / "project_state.json", {})
        meta = _read_json(item / "v5" / "project_meta.json", {})
        rows.append(
            {
                "project_id": item.name,
                "path": str(item).replace("\\", "/"),
                "stage": state.get("current_stage", "not_initialized"),
                "archived": bool(meta.get("archived")),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            }
        )
    return {"schema_version": PROJECT_REGISTRY_SCHEMA, "root": str(root).replace("\\", "/"), "projects": rows, "project_count": len(rows)}


def create_project(project_id: str, *, template_project: str = "") -> Path:
    project_id = _safe_project_id(project_id)
    if not project_id:
        raise ValueError("project_id is required")
    target = project_path(project_id)
    if target.exists():
        raise FileExistsError(target)
    if template_project:
        template = project_path(template_project)
        if not template.exists():
            raise FileNotFoundError(template)
        shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "webapp.*.log"))
    else:
        ensure_project_dirs(target)
        (target / "research_spec.json").write_text(json.dumps({"project_id": project_id, "confirmed": False}, indent=2), encoding="utf-8")
        (target / "research_interest.md").write_text("", encoding="utf-8")
    _write_meta(target, archived=False)
    return target


def archive_project(project_id: str, archived: bool = True) -> dict[str, Any]:
    target = project_path(project_id)
    if not target.exists():
        raise FileNotFoundError(target)
    meta = _write_meta(target, archived=archived)
    return meta


def delete_project(project_id: str, *, backup: bool = True) -> dict[str, Any]:
    target = project_path(project_id)
    if not target.exists():
        raise FileNotFoundError(target)
    backup_path = ""
    if backup:
        backup_path = str(export_project(project_id)).replace("\\", "/")
    shutil.rmtree(target)
    return {"project_id": project_id, "deleted": True, "backup": backup_path}


def export_project(project_id: str, out_dir: Path | None = None) -> Path:
    target = project_path(project_id)
    if not target.exists():
        raise FileNotFoundError(target)
    out_dir = out_dir or (PROJECTS.parent / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{project_id}_project_export_{stamp}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in target.rglob("*"):
            if path.is_file() and not _skip(path):
                zf.write(path, Path(project_id) / path.relative_to(target))
        zf.writestr("project_export_manifest.json", json.dumps({"project_id": project_id, "created_at": _now()}, indent=2))
    return out


def import_project(zip_path: str | Path, project_id: str = "") -> Path:
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    original_project_id = zip_path.stem.split("_project_export_")[0]
    project_id = _safe_project_id(project_id or zip_path.stem.split("_project_export_")[0])
    target = project_path(project_id)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.endswith("/") or member == "project_export_manifest.json":
                continue
            parts = Path(member).parts
            rel = Path(*parts[1:]) if parts and parts[0] in {project_id, original_project_id} else Path(member)
            dest = (target / rel).resolve()
            if target.resolve() not in [dest.parent, *dest.parents]:
                raise ValueError(f"unsafe project zip member: {member}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(member))
    ensure_project_dirs(target)
    _write_meta(target, archived=False)
    return target


def _write_meta(project_dir: Path, *, archived: bool) -> dict[str, Any]:
    path = project_dir / "v5" / "project_meta.json"
    existing = _read_json(path, {})
    now = _now()
    payload = {
        "schema_version": "v5.project_meta/0.1",
        "project_id": project_dir.name,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "archived": archived,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _looks_like_project(path: Path) -> bool:
    return any((path / name).exists() for name in ["research_spec.json", "dataset_cards", "v5", "results"])


def _safe_project_id(value: str) -> str:
    return "".join(ch for ch in value.strip() if ch.isalnum() or ch in {"_", "-"})


def _skip(path: Path) -> bool:
    return path.name in {"secrets.local.json"} or "__pycache__" in path.parts or path.suffix in {".pyc", ".tmp"}


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
