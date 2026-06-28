import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge import load_registry
from .methods.registry import available_project_methods, load_method_config
from .causal_evidence import DEFAULT_CAUSAL_RUBRIC, load_causal_review_rubric
from .scoring import DEFAULT_RULES, load_scoring_rules
from .v4 import content_hash, file_hash, v4_dir


SNAPSHOT_SCHEMA = "v4.registry_snapshots/0.1"
KB_BINDING_SCHEMA = "v4.kb_snapshot_binding/0.1"


def build_registry_snapshots(project_dir: Path, rules_path: Path = DEFAULT_RULES) -> dict[str, Any]:
    method_snapshot = _method_snapshot(project_dir)
    source_snapshot = _source_snapshot(project_dir)
    rubric_snapshot = _rubric_snapshot(rules_path)
    causal_rubric_snapshot = _causal_rubric_snapshot(project_dir)
    payload = {
        "schema_version": SNAPSHOT_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshots": {
            "method_registry": method_snapshot,
            "source_registry": source_snapshot,
            "rubric": rubric_snapshot,
            "causal_review_rubric": causal_rubric_snapshot,
        },
        "snapshot_hash": content_hash(
            {
                "method_registry": method_snapshot["hash"],
                "source_registry": source_snapshot["hash"],
                "rubric": rubric_snapshot["hash"],
                "causal_review_rubric": causal_rubric_snapshot["hash"],
            }
        ),
    }
    path = registry_snapshot_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def bind_project_kb_snapshot(project_dir: Path, rules_path: Path = DEFAULT_RULES, force: bool = False) -> dict[str, Any]:
    existing = load_project_kb_snapshot(project_dir)
    if existing and not force:
        return existing
    registry_snapshot = build_registry_snapshots(project_dir, rules_path)
    snapshots = registry_snapshot.get("snapshots", {})
    seed = {
        "project_id": project_dir.name,
        "registry_snapshot_hash": registry_snapshot.get("snapshot_hash", ""),
        "method_registry": snapshots.get("method_registry", {}).get("hash", ""),
        "source_registry": snapshots.get("source_registry", {}).get("hash", ""),
        "rubric": snapshots.get("rubric", {}).get("hash", ""),
        "causal_review_rubric": snapshots.get("causal_review_rubric", {}).get("hash", ""),
    }
    payload = {
        "schema_version": KB_BINDING_SCHEMA,
        "project_id": project_dir.name,
        "kb_snapshot_id": "kb_" + content_hash(seed)[:20],
        "status": "frozen",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "registry_snapshot": "v4/registry_snapshots.json",
        "registry_snapshot_hash": registry_snapshot.get("snapshot_hash", ""),
        "components": {
            "method_registry": snapshots.get("method_registry", {}).get("hash", ""),
            "source_registry": snapshots.get("source_registry", {}).get("hash", ""),
            "scoring_rubric": snapshots.get("rubric", {}).get("hash", ""),
            "causal_review_rubric": snapshots.get("causal_review_rubric", {}).get("hash", ""),
        },
        "policy": "Project runs must reference this kb_snapshot_id until an explicit forced rebind creates a new project revision.",
    }
    path = project_kb_snapshot_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def project_kb_snapshot_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "kb_snapshot.json"


def load_project_kb_snapshot(project_dir: Path) -> dict[str, Any]:
    path = project_kb_snapshot_path(project_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def registry_snapshot_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "registry_snapshots.json"


def load_registry_snapshots(project_dir: Path) -> dict[str, Any]:
    path = registry_snapshot_path(project_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _method_snapshot(project_dir: Path) -> dict[str, Any]:
    selected = load_method_config(project_dir)
    available = available_project_methods(project_dir)
    method_rows = []
    for stage, rows in sorted(available.items()):
        for row in sorted(rows, key=lambda item: item["method_id"]):
            method_rows.append(
                {
                    "stage": stage,
                    "method_id": row["method_id"],
                    "label": row["label"],
                    "gpt_compatible": row["gpt_compatible"],
                    "human_replaceable": row["human_replaceable"],
                    "selected": selected.get(stage) == row["method_id"],
                    "stage_label": row.get("stage_label", stage),
                }
            )
    payload = {
        "schema_version": "v4.method_registry_snapshot/0.1",
        "selected": selected,
        "method_count": len(method_rows),
        "methods": method_rows,
    }
    payload["hash"] = content_hash(payload)
    return payload


def _causal_rubric_snapshot(project_dir: Path) -> dict[str, Any]:
    rubric, meta = load_causal_review_rubric(project_dir)
    path = Path(meta.get("path", ""))
    payload = {
        "schema_version": "v4.causal_review_rubric_snapshot/0.1",
        "rubric_id": rubric.get("rubric_id", ""),
        "version": rubric.get("version", ""),
        "path": str(path if path else DEFAULT_CAUSAL_RUBRIC),
        "file_hash": meta.get("file_hash", ""),
        "rules_hash": meta.get("hash", ""),
        "sections": sorted(rubric.keys()),
    }
    payload["hash"] = content_hash(payload)
    return payload


def _source_snapshot(project_dir: Path) -> dict[str, Any]:
    resources = load_registry(project_dir)
    normalized = []
    for row in sorted(resources, key=lambda item: item.get("resource_id", "")):
        source_path = Path(row.get("source_path", ""))
        adapted_path = Path(row.get("adapted_path", "")) if row.get("adapted_path") else None
        normalized.append(
            {
                "resource_id": row.get("resource_id", ""),
                "resource_type": row.get("resource_type", ""),
                "adapter": row.get("adapter", ""),
                "status": row.get("status", ""),
                "source_path": row.get("source_path", ""),
                "source_hash": file_hash(source_path) if source_path.exists() and source_path.is_file() else "",
                "adapted_path": row.get("adapted_path", ""),
                "adapted_hash": file_hash(adapted_path) if adapted_path and adapted_path.exists() and adapted_path.is_file() else "",
                "normalized_rows": row.get("normalized_rows", ""),
            }
        )
    payload = {
        "schema_version": "v4.source_registry_snapshot/0.1",
        "resource_count": len(normalized),
        "resources": normalized,
    }
    payload["hash"] = content_hash(payload)
    return payload


def _rubric_snapshot(rules_path: Path) -> dict[str, Any]:
    rules = load_scoring_rules(rules_path)
    payload = {
        "schema_version": "v4.rubric_snapshot/0.1",
        "path": str(rules_path),
        "file_hash": file_hash(rules_path) if rules_path.exists() else "",
        "rules_hash": content_hash(rules),
        "sections": sorted(rules.keys()),
    }
    payload["hash"] = content_hash(payload)
    return payload
