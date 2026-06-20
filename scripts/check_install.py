import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check_python() -> dict:
    return {"name": "python", "ok": sys.version_info >= (3, 10), "detail": sys.version.split()[0]}


def check_r_limma() -> dict:
    rscript = shutil.which("Rscript")
    if not rscript:
        for path in sorted(Path("C:/Program Files/R").glob("R-*/bin/Rscript.exe")):
            rscript = str(path)
            break
    if not rscript:
        return {"name": "r_limma", "ok": False, "detail": "Rscript not found; Python fallback remains available"}
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
    return {
        "name": "r_limma",
        "ok": result.returncode == 0,
        "detail": "Rscript and limma available" if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip()),
    }


def check_project() -> dict:
    required = [
        "tc_lite.py",
        "targetcompass_lite",
        "projects/vascular_aging_demo/dataset_cards",
        "knowledge_base",
    ]
    missing = [item for item in required if not (ROOT / item).exists()]
    return {"name": "project_files", "ok": not missing, "detail": "missing: " + ", ".join(missing) if missing else "ok"}


def main() -> int:
    checks = [check_python(), check_project(), check_r_limma()]
    print(json.dumps({"root": str(ROOT), "checks": checks}, indent=2, ensure_ascii=False))
    return 0 if all(check["ok"] or check["name"] == "r_limma" for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
