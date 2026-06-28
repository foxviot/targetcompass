import json
import re
import zipfile
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from targetcompass_lite.packaging_profiles import build_packaging_profile, write_packaging_manifests


OUT_DIR = ROOT / "dist"
BUNDLE_SCHEMA = "v5.local_bundle/0.1"

INCLUDE_ROOTS = [
    "README.md",
    "README_CN.md",
    "pyproject.toml",
    "tc_lite.py",
    "targetcompass_lite",
    "scripts",
    "docs",
    "schemas",
    "knowledge_base",
    "projects/vascular_aging_demo",
]

EXCLUDE_PARTS = {
    ".git",
    ".nextflow",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "tools",
    "external_agent_runs",
    "exports",
    "raw",
    "raw_extracted",
}
EXCLUDE_NAMES = {"secrets.local.json", "webapp.out.log", "webapp.err.log"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".zip"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]+", re.IGNORECASE),
    re.compile(r"Authorization\s*[:=]", re.IGNORECASE),
]


def export_v5_local_bundle(profile: str = "professor_demo") -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    profile_manifest = build_packaging_profile(profile)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_path = OUT_DIR / f"targetcompass_v5_{profile}_bundle_{stamp}.zip"
    manifest_dir = OUT_DIR / f"{bundle_path.stem}_manifests"
    extra_manifests = write_packaging_manifests(ROOT, manifest_dir, profile)
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "profile_manifest": profile_manifest,
        "bundle": bundle_path.name,
        "entrypoint": "tc_lite.py v5-run-local",
        "ui": "tc_lite.py serve --project vascular_aging_demo --port 8801",
        "backend_activation": "tc_lite.py v5-backends-activate --project vascular_aging_demo",
        "include_roots": _include_roots_for_profile(profile_manifest),
        "files": [],
        "skipped_sensitive_files": [],
        "dependency_cache": extra_manifests["dependencies"],
    }
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = []
        for path in _iter_included_files(profile_manifest):
            rel = path.relative_to(ROOT).as_posix()
            if _contains_secret(path):
                manifest["skipped_sensitive_files"].append(rel)
                continue
            zf.write(path, rel)
            files.append(rel)
        for path in sorted(manifest_dir.glob("*.json")):
            zf.write(path, f"packaging_manifests/{path.name}")
        manifest["files"] = files
        manifest["file_count"] = len(files)
        manifest["skipped_sensitive_count"] = len(manifest["skipped_sensitive_files"])
        zf.writestr("v5_local_bundle_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    manifest_path = OUT_DIR / f"{bundle_path.stem}_manifest.json"
    manifest["size_bytes"] = bundle_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return bundle_path


def _include_roots_for_profile(profile_manifest: dict) -> list[str]:
    roots = list(INCLUDE_ROOTS)
    if profile_manifest.get("include_tests"):
        roots.append("tests")
    if not profile_manifest.get("include_docs"):
        roots = [root for root in roots if root != "docs"]
    return roots


def _iter_included_files(profile_manifest: dict) -> list[Path]:
    files = []
    for root in _include_roots_for_profile(profile_manifest):
        path = ROOT / root
        if not path.exists():
            continue
        if path.is_file():
            if _should_include(path, profile_manifest):
                files.append(path)
            continue
        for item in path.rglob("*"):
            if item.is_file() and _should_include(item, profile_manifest):
                files.append(item)
    return sorted(set(files))


def _should_include(path: Path, profile_manifest: dict | None = None) -> bool:
    profile_manifest = profile_manifest or build_packaging_profile("professor_demo")
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return False
    if not profile_manifest.get("include_dev_artifacts") and any(part in {"external_network_llm_e2e", "engineering_packet_validation_50", "sarcopenia_muscle_sasp_demo"} for part in rel.parts):
        return False
    if not profile_manifest.get("include_external_agent_runs") and "external_agent_runs" in rel.parts:
        return False
    if not profile_manifest.get("include_demo_outputs") and any(part in {"results", "reports", "v4", "v5"} for part in rel.parts):
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["professor_demo", "developer"], default="professor_demo")
    args = parser.parse_args()
    print(export_v5_local_bundle(profile=args.profile))
