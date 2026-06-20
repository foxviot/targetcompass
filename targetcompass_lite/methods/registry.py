import json
import re
from pathlib import Path

from .audit_methods import METHODS as AUDIT_METHODS
from .contracts import MethodContext, MethodResult, MethodSpec
from .experiment_methods import METHODS as EXPERIMENT_METHODS
from .query_methods import METHODS as QUERY_METHODS


V4_METHOD_STAGES = {
    "query": "Idea generation / disease normalization",
    "audit": "Initial feasibility and method review",
    "experiment": "Experiment design / result review",
    "disease_normalizer": "Normalize user research request",
    "dataset_scout": "Find and qualify datasets",
    "planner": "Compile analysis plan and work orders",
    "method_reviewer": "Review methods before execution",
    "result_reviewer": "Review results after execution",
    "causal_reviewer": "Review causal evidence bundle",
    "report_writer": "Write report from accepted evidence",
}

DEFAULT_METHOD_CONFIG = {
    "query": "local_idea_query_v0",
    "audit": "local_feasibility_audit_v0",
    "experiment": "local_experiment_design_v0",
    "disease_normalizer": "local_disease_normalizer_v0",
    "dataset_scout": "local_dataset_scout_v0",
    "planner": "local_planner_v0",
    "method_reviewer": "local_method_reviewer_v0",
    "result_reviewer": "local_result_reviewer_v0",
    "causal_reviewer": "local_causal_reviewer_v0",
    "report_writer": "local_report_writer_v0",
}


def _registry() -> dict[str, dict[str, MethodSpec]]:
    registry: dict[str, dict[str, MethodSpec]] = {stage: {} for stage in V4_METHOD_STAGES}
    for method in [*QUERY_METHODS, *AUDIT_METHODS, *EXPERIMENT_METHODS]:
        registry.setdefault(method.stage, {})[method.method_id] = method
    for method in _role_method_specs():
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
                "stage_label": V4_METHOD_STAGES.get(stage, stage),
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
                "stage_label": V4_METHOD_STAGES.get(stage, stage),
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
    if stage not in V4_METHOD_STAGES:
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
    stage = _stage_from_markdown_method_id(method_id)
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


def _stage_from_markdown_method_id(method_id: str) -> str:
    if not method_id.startswith("md_"):
        return "query"
    remainder = method_id[3:]
    for stage in sorted(V4_METHOD_STAGES, key=len, reverse=True):
        if remainder == stage or remainder.startswith(f"{stage}_"):
            return stage
    return "query"


def _role_method_specs() -> list[MethodSpec]:
    return [
        MethodSpec(
            method_id="local_disease_normalizer_v0",
            stage="disease_normalizer",
            label="Local disease normalizer v0",
            description="Rule-based ResearchSpec/DiseaseSpec normalization entrypoint used by the local app.",
            runner=_metadata_runner("disease_normalizer"),
        ),
        MethodSpec(
            method_id="local_dataset_scout_v0",
            stage="dataset_scout",
            label="Local dataset scout v0",
            description="Local GEO/GSE discovery and dataset eligibility scout contract.",
            runner=_metadata_runner("dataset_scout"),
        ),
        MethodSpec(
            method_id="local_planner_v0",
            stage="planner",
            label="Local planner v0",
            description="Deterministic planner that compiles registered modules and WorkOrders.",
            runner=_metadata_runner("planner"),
        ),
        MethodSpec(
            method_id="local_method_reviewer_v0",
            stage="method_reviewer",
            label="Local method reviewer v0",
            description="Local readiness gates and feasibility audit before execution.",
            runner=_metadata_runner("method_reviewer"),
        ),
        MethodSpec(
            method_id="local_result_reviewer_v0",
            stage="result_reviewer",
            label="Local result reviewer v0",
            description="Local QC, candidate score, and result review contract.",
            runner=_metadata_runner("result_reviewer"),
        ),
        MethodSpec(
            method_id="local_causal_reviewer_v0",
            stage="causal_reviewer",
            label="Local causal reviewer v0",
            description="Configurable causal evidence rubric reviewer for GWAS/QTL/coloc/MR outputs.",
            runner=_metadata_runner("causal_reviewer"),
        ),
        MethodSpec(
            method_id="local_report_writer_v0",
            stage="report_writer",
            label="Local report writer v0",
            description="Deterministic report writer restricted to accepted/flagged evidence references.",
            runner=_metadata_runner("report_writer"),
        ),
    ]


def _metadata_runner(stage: str):
    def run(context: MethodContext) -> MethodResult:
        return MethodResult(
            status="pass",
            message=f"{stage} method contract selected.",
            details={
                "stage": stage,
                "role_id": context.role_id or stage,
                "input_refs": context.input_refs,
                "parameters": context.parameters,
            },
        )

    return run
