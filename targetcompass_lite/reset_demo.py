import shutil
from pathlib import Path


OUTPUT_FILES = [
    "analysis_plan.json",
    "candidate_scores.csv",
    "dataset_match_report.csv",
    "dataset_match_report.md",
    "eligible_datasets.csv",
    "evidence.sqlite",
    "screening_report.md",
]

OUTPUT_DIRS = [
    "results",
    "reports",
    "work_orders",
    "exports",
    "knowledge_imports",
]


def reset_demo_outputs(project_dir: Path, keep_registry: bool = True) -> list[str]:
    removed = []
    for relative in OUTPUT_FILES:
        path = project_dir / relative
        if path.exists():
            path.unlink()
            removed.append(relative)
    for relative in OUTPUT_DIRS:
        path = project_dir / relative
        if path.exists():
            shutil.rmtree(path)
            removed.append(relative)
    if not keep_registry:
        registry = project_dir / "configs" / "knowledge_registry.json"
        if registry.exists():
            registry.unlink()
            removed.append("configs/knowledge_registry.json")
    for relative in ["results", "reports", "work_orders"]:
        (project_dir / relative).mkdir(parents=True, exist_ok=True)
    return removed
