import json
import re
from pathlib import Path

from .audit_methods import METHODS as AUDIT_METHODS
from .contracts import MethodContext, MethodResult, MethodSpec
from .experiment_methods import METHODS as EXPERIMENT_METHODS
from .query_methods import METHODS as QUERY_METHODS


DEFAULT_METHOD_CONFIG = {
    "query": "local_idea_query_v0",
    "audit": "local_feasibility_audit_v0",
    "experiment": "local_experiment_design_v0",
}


def _registry() -> dict[str, dict[str, MethodSpec]]:
    registry: dict[str, dict[str, MethodSpec]] = {"query": {}, "audit": {}, "experiment": {}}
    for method in [*QUERY_METHODS, *AUDIT_METHODS, *EXPERIMENT_METHODS]:
        registry.setdefault(method.stage, {})[method.method_id] = method
    for method in _markdown_method_specs():
        registry.setdefault(method.stage, {})[method.method_id] = method
    return registry


def method_config_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "agent_methods.json"


def markdown_methods_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "knowledge_base" / "agent_methods"


def project_markdown_methods_dir(project_dir: Path) -> Path:
    return project_dir / "agent_methods"


def load_method_config(project_dir: Path) -> dict[str, str]:
    path = method_config_path(project_dir)
    if not path.exists():
        return dict(DEFAULT_METHOD_CONFIG)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {**DEFAULT_METHOD_CONFIG, **{k: v for k, v in loaded.items() if v}}


def save_method_config(project_dir: Path, config: dict[str, str]) -> dict[str, str]:
    available = _registry_for_project(project_dir)
    normalized = dict(DEFAULT_METHOD_CONFIG)
    for stage, method_id in config.items():
        if stage not in available:
            raise ValueError(f"unsupported method stage: {stage}")
        if method_id not in available[stage]:
            raise ValueError(f"unsupported method for {stage}: {method_id}")
        normalized[stage] = method_id
    path = method_config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    return normalized


def available_methods() -> dict[str, list[dict[str, str | bool]]]:
    out: dict[str, list[dict[str, str | bool]]] = {}
    for stage, methods in _registry().items():
        out[stage] = [
            {
                "method_id": method.method_id,
                "label": method.label,
                "description": method.description,
                "gpt_compatible": method.gpt_compatible,
                "human_replaceable": method.human_replaceable,
            }
            for method in methods.values()
        ]
    return out


def available_project_methods(project_dir: Path) -> dict[str, list[dict[str, str | bool]]]:
    out: dict[str, list[dict[str, str | bool]]] = {}
    for stage, methods in _registry_for_project(project_dir).items():
        out[stage] = [
            {
                "method_id": method.method_id,
                "label": method.label,
                "description": method.description,
                "gpt_compatible": method.gpt_compatible,
                "human_replaceable": method.human_replaceable,
            }
            for method in methods.values()
        ]
    return out


def run_method(stage: str, context: MethodContext, method_id: str | None = None) -> MethodResult:
    config = load_method_config(context.project_dir)
    selected = method_id or config.get(stage) or DEFAULT_METHOD_CONFIG[stage]
    methods = _registry_for_project(context.project_dir).get(stage, {})
    if selected not in methods:
        raise ValueError(f"method not found for {stage}: {selected}")
    result = methods[selected].runner(context)
    result.details.setdefault("method_id", selected)
    result.details.setdefault("method_label", methods[selected].label)
    return result


def install_markdown_method(project_dir: Path, stage: str, filename: str, content: str) -> dict[str, str]:
    if stage not in DEFAULT_METHOD_CONFIG:
        raise ValueError(f"unsupported method stage: {stage}")
    if not content.strip():
        raise ValueError("markdown method content is required")
    safe_name = _safe_stem(filename or "method")
    method_id = f"md_{stage}_{safe_name}"
    dest = project_markdown_methods_dir(project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{method_id}.md"
    path.write_text(content, encoding="utf-8")
    return {"method_id": method_id, "stage": stage, "path": str(path.relative_to(project_dir))}


def list_markdown_methods(project_dir: Path | None = None) -> list[dict[str, str]]:
    rows = []
    for base in _markdown_method_dirs(project_dir):
        if not base.exists():
            continue
        for path in sorted(base.glob("md_*.md")):
            meta = _parse_markdown_method(path)
            rows.append(
                {
                    "method_id": meta["method_id"],
                    "stage": meta["stage"],
                    "label": meta["label"],
                    "path": str(path),
                }
            )
    return rows


def delete_markdown_method(project_dir: Path, method_id: str) -> None:
    if not method_id.startswith("md_"):
        raise ValueError("only markdown methods can be deleted")
    for path in project_markdown_methods_dir(project_dir).glob(f"{method_id}.md"):
        path.unlink()


def _markdown_method_specs(project_dir: Path | None = None) -> list[MethodSpec]:
    specs = []
    for base in _markdown_method_dirs(project_dir):
        if not base.exists():
            continue
        for path in sorted(base.glob("md_*.md")):
            meta = _parse_markdown_method(path)
            specs.append(
                MethodSpec(
                    method_id=meta["method_id"],
                    stage=meta["stage"],
                    label=meta["label"],
                    description=meta["description"],
                    runner=_markdown_runner(meta),
                    gpt_compatible=True,
                    human_replaceable=True,
                )
            )
    return specs


def _registry_for_project(project_dir: Path) -> dict[str, dict[str, MethodSpec]]:
    registry = _registry()
    for method in _markdown_method_specs(project_dir):
        registry.setdefault(method.stage, {})[method.method_id] = method
    return registry


def _markdown_method_dirs(project_dir: Path | None = None) -> list[Path]:
    dirs = [markdown_methods_dir()]
    if project_dir is not None:
        dirs.append(project_markdown_methods_dir(project_dir))
    return dirs


def _parse_markdown_method(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    method_id = path.stem
    parts = method_id.split("_", 2)
    stage = parts[1] if len(parts) > 2 and parts[1] in DEFAULT_METHOD_CONFIG else "query"
    title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.strip().startswith("#")), method_id)
    description = next((line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")), "")
    return {
        "method_id": method_id,
        "stage": stage,
        "label": title[:80],
        "description": description[:220] or f"Markdown-guided {stage} method from {path.name}.",
        "content": text,
        "path": str(path),
    }


def _markdown_runner(meta: dict[str, str]):
    def run(context: MethodContext) -> MethodResult:
        fallback_id = DEFAULT_METHOD_CONFIG[meta["stage"]]
        fallback = _registry()[meta["stage"]][fallback_id].runner(context)
        fallback.details["markdown_method_id"] = meta["method_id"]
        fallback.details["markdown_method_path"] = meta["path"]
        fallback.details["markdown_method_prompt"] = meta["content"][:2000]
        fallback.message = f'{fallback.message} Markdown method guidance attached: {meta["label"]}.'
        return fallback

    return run


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return stem[:48] or "method"
