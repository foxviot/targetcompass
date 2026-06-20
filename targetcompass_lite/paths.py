from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects"
KB = ROOT / "knowledge_base"


def project_path(project: str) -> Path:
    p = Path(project)
    if p.exists() or str(project).startswith("projects"):
        return p
    return PROJECTS / project


def ensure_project_dirs(project_dir: Path) -> None:
    for name in [
        "dataset_cards",
        "literature_cards",
        "configs",
        "data",
        "results",
        "work_orders",
        "reports",
    ]:
        (project_dir / name).mkdir(parents=True, exist_ok=True)
