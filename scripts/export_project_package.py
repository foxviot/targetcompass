import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "dist"
EXCLUDE_PARTS = {"__pycache__", ".git", "dist"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    if "exports" in rel.parts:
        return False
    if path.name in {"webapp.out.log", "webapp.err.log"}:
        return False
    if path.name == "secrets.local.json":
        return False
    return True


def export_project_package() -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"targetcompass_lite_delivery_{stamp}.zip"
    roots = [
        "START_TARGETCOMPASS.bat",
        "README.md",
        "README_CN.md",
        "pyproject.toml",
        "tc_lite.py",
        "targetcompass_lite",
        "scripts",
        "docs",
        "examples",
        "schemas",
        "knowledge_base",
        "projects/vascular_aging_demo",
        "tests",
    ]
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root in roots:
            path = ROOT / root
            if not path.exists():
                continue
            if path.is_file():
                if should_include(path):
                    zf.write(path, path.relative_to(ROOT))
                continue
            for item in path.rglob("*"):
                if item.is_file() and should_include(item):
                    zf.write(item, item.relative_to(ROOT))
    return out


if __name__ == "__main__":
    print(export_project_package())
