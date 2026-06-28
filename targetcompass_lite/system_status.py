import os
import shutil
import subprocess
import sys
from pathlib import Path

from .knowledge import load_registry
from .secrets import llm_provider_summary


def _rscript() -> str | None:
    found = shutil.which("Rscript")
    if found:
        return found
    for path in sorted(Path("C:/Program Files/R").glob("R-*/bin/Rscript.exe")):
        return str(path)
    return None


def _limma_available() -> tuple[bool, str]:
    rscript = _rscript()
    if not rscript:
        return False, "Rscript not found; Python fallback available"
    env = os.environ.copy()
    user_libs = sorted(Path.home().glob("Documents/R/win-library/*"))
    if user_libs:
        env["R_LIBS_USER"] = str(user_libs[-1])
    result = subprocess.run(
        [rscript, "-e", "suppressPackageStartupMessages(library(limma)); cat('ok')"],
        text=True,
        capture_output=True,
        timeout=20,
        env=env,
    )
    if result.returncode == 0:
        return True, "Rscript and limma available"
    return False, result.stderr.strip() or result.stdout.strip() or "limma unavailable"


def system_status(project_dir: Path) -> list[dict]:
    limma_ok, limma_detail = _limma_available()
    registry = load_registry(project_dir)
    llm = llm_provider_summary(project_dir)
    return [
        {"name": "Python", "status": "PASS", "detail": sys.version.split()[0]},
        {"name": "R/limma", "status": "PASS" if limma_ok else "REVIEW", "detail": limma_detail},
        {
            "name": "LLM API key",
            "status": "PASS" if os.environ.get("OPENAI_API_KEY") else "REVIEW",
            "detail": f"{llm.get('provider', 'openai')} configured" if os.environ.get("OPENAI_API_KEY") else f"{llm.get('provider', 'openai')} not set; local fallback available",
        },
        {
            "name": "Dataset cards",
            "status": "PASS" if list((project_dir / "dataset_cards").glob("*.yaml")) else "FAIL",
            "detail": f"{len(list((project_dir / 'dataset_cards').glob('*.yaml')))} registered dataset card(s)",
        },
        {
            "name": "Database adapters",
            "status": "PASS" if registry else "REVIEW",
            "detail": f"{len(registry)} custom resource(s) registered",
        },
        {
            "name": "Report",
            "status": "PASS" if (project_dir / "reports" / "target_report.html").exists() else "REVIEW",
            "detail": "target_report.html exists" if (project_dir / "reports" / "target_report.html").exists() else "run workflow to generate report",
        },
    ]
