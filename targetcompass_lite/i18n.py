import json
from pathlib import Path


SUPPORTED_LANGUAGES = {"zh", "en"}


TRANSLATIONS = {
    "zh": {
        "app_title": "TargetCompass Lite",
        "eyebrow": "GPT 引导的靶点发现",
        "hero_title": "生成、审查、再执行。",
        "hero_copy": "把研究问题转成 ResearchSpec 和候选点子，再由本地 Agent 审查、运行真实数据分析并输出可复核证据。",
        "demo_title": "血管衰老 Demo",
        "demo_copy": "六步流程：生成、初审、查证、执行、复审、报告。",
        "api_key": "API Key",
        "openai_api_key": "OpenAI API Key",
        "save_key": "保存 Key",
        "clear_key": "清除 Key",
        "system_status": "系统状态",
        "reset_demo": "重置 Demo",
        "reset_demo_button": "清空输出并重建 Demo",
        "agent_request": "Agent 研究请求",
        "datasets_for_run": "本次运行的数据集",
        "replaceable_methods": "可替换方法",
        "run_status": "运行状态",
        "structured_spec": "结构化 ResearchSpec",
        "audit_gates": "审查门控",
        "agent_trace": "Agent 轨迹",
        "idea_feasibility": "点子可行性",
        "manual_review": "人工审核",
        "experiment_designs": "实验设计",
        "method_config": "方法配置",
        "knowledge_registry": "知识库 / 数据库注册",
        "delivery_package": "交付包",
        "dataset_match_review": "数据集匹配审查",
        "research_prompt": "研究请求",
        "generation_engine": "生成引擎",
        "idea_volume": "点子数量",
        "confirm_spec": "我已审核并确认生成的 ResearchSpec",
        "run_agent": "运行 GPT-guided Agent",
        "open_report": "打开报告",
        "save_methods": "保存方法",
        "add_resource": "添加资源",
        "adapt_resources": "适配已注册资源",
        "build_adapter_audit": "生成 Adapter 审核",
        "export_package": "导出运行包",
        "approve": "通过",
        "review": "复核",
        "reject": "驳回",
        "remove": "移除",
        "review_note": "审核备注",
        "switch_language": "Switch to English",
    },
    "en": {
        "app_title": "TargetCompass Lite",
        "eyebrow": "GPT-guided target discovery",
        "hero_title": "Generate, audit, then run.",
        "hero_copy": "Turn a research question into ResearchSpec and candidate ideas, then let the local Agent audit, analyze real data, and produce reviewable evidence.",
        "demo_title": "Vascular aging demo",
        "demo_copy": "Six-step flow: generation, initial review, verification, execution, final review, report.",
        "api_key": "API Key",
        "openai_api_key": "OpenAI API Key",
        "save_key": "Save key",
        "clear_key": "Clear key",
        "system_status": "System status",
        "reset_demo": "Reset demo",
        "reset_demo_button": "Clear outputs and rebuild demo",
        "agent_request": "Agent research request",
        "datasets_for_run": "Datasets for this run",
        "replaceable_methods": "Replaceable methods",
        "run_status": "Run status",
        "structured_spec": "Structured ResearchSpec",
        "audit_gates": "Audit gates",
        "agent_trace": "Agent trace",
        "idea_feasibility": "Idea feasibility",
        "manual_review": "Manual review",
        "experiment_designs": "Experiment designs",
        "method_config": "Method configuration",
        "knowledge_registry": "Knowledge / Database registry",
        "delivery_package": "Delivery package",
        "dataset_match_review": "Dataset match review",
        "research_prompt": "Research prompt",
        "generation_engine": "Generation engine",
        "idea_volume": "Idea volume",
        "confirm_spec": "I reviewed and confirm the generated ResearchSpec",
        "run_agent": "Run GPT-guided Agent",
        "open_report": "Open report",
        "save_methods": "Save methods",
        "add_resource": "Add resource",
        "adapt_resources": "Adapt registered resources",
        "build_adapter_audit": "Build adapter audit",
        "export_package": "Export run package",
        "approve": "Approve",
        "review": "Review",
        "reject": "Reject",
        "remove": "Remove",
        "review_note": "Review note",
        "switch_language": "切换到中文",
    },
}


def language_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "ui_language.json"


def get_language(project_dir: Path) -> str:
    path = language_path(project_dir)
    if not path.exists():
        return "zh"
    try:
        lang = json.loads(path.read_text(encoding="utf-8")).get("language", "zh")
    except json.JSONDecodeError:
        return "zh"
    return lang if lang in SUPPORTED_LANGUAGES else "zh"


def set_language(project_dir: Path, language: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "zh"
    path = language_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"language": lang}, indent=2), encoding="utf-8")
    return lang


def translator(project_dir: Path):
    lang = get_language(project_dir)

    def t(key: str) -> str:
        return TRANSLATIONS.get(lang, TRANSLATIONS["zh"]).get(key, TRANSLATIONS["en"].get(key, key))

    return lang, t
