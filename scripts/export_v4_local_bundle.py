import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "dist"
BUNDLE_SCHEMA = "v4.local_bundle/0.1"

INCLUDE_ROOTS = [
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
    "tests",
    "results/full_unittest_discover_log.txt",
    "results/full_unittest_module_summary.json",
    "results/test_suites",
    "results/external_network_llm_e2e_summary.json",
    "results/external_llm_role_matrix.json",
    "results/geo_discovery_retry_live.log",
    "projects/vascular_aging_demo",
    "projects/sarcopenia_muscle_sasp_demo",
    "projects/engineering_packet_validation_50",
    "projects/external_network_llm_e2e",
]

EXCLUDE_PARTS = {
    ".git",
    ".nextflow",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "tmp_ocr_test",
    "exports",
    "git_worktrees",
    "workspaces",
    "integration_worktree",
    "role_runs",
    "forest_plots",
    "raw",
    "raw_extracted",
    "tools",
    "external_agent_runs",
}
EXCLUDE_NAMES = {
    "secrets.local.json",
    "webapp.out.log",
    "webapp.err.log",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".zip"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]+", re.IGNORECASE),
    re.compile(r"Authorization\s*[:=]", re.IGNORECASE),
]


def export_v4_local_bundle() -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_path = OUT_DIR / f"targetcompass_v4_local_bundle_{stamp}.zip"
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bundle": bundle_path.name,
        "include_roots": INCLUDE_ROOTS,
        "files": [],
        "skipped_sensitive_files": [],
        "excluded_rules": {
            "parts": sorted(EXCLUDE_PARTS),
            "names": sorted(EXCLUDE_NAMES),
            "suffixes": sorted(EXCLUDE_SUFFIXES),
        },
    }
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = []
        for path in _iter_included_files():
            rel = path.relative_to(ROOT).as_posix()
            if _contains_secret(path):
                manifest["skipped_sensitive_files"].append(rel)
                continue
            zf.write(path, rel)
            files.append(rel)
        manifest["files"] = files
        manifest["file_count"] = len(files)
        manifest["skipped_sensitive_count"] = len(manifest["skipped_sensitive_files"])
        zf.writestr("v4_local_bundle_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    manifest_path = OUT_DIR / f"{bundle_path.stem}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return bundle_path


def _iter_included_files() -> list[Path]:
    files = []
    for root in INCLUDE_ROOTS:
        path = ROOT / root
        if not path.exists():
            continue
        if path.is_file():
            if _should_include(path):
                files.append(path)
            continue
        for item in path.rglob("*"):
            if item.is_file() and _should_include(item):
                files.append(item)
    return sorted(set(files))


def _should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    if path.name.startswith("evidence.sqlite.corrupt_") or path.name.startswith("evidence.sqlite.pre_"):
        return False
    return True


def _contains_secret(path: Path) -> bool:
    if path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md", ".py", ".ps1", ".bat", ".log", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".tsv", ".csv"}:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


if __name__ == "__main__":
    print(export_v4_local_bundle())
