import json
import shutil
import zipfile
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from targetcompass_lite.packaging_profiles import build_dependency_cache_manifest, build_packaging_profile, build_runtime_repair_plan


PACKAGING_DIR = ROOT / "packaging" / "windows_v5"
PAYLOAD_DIR = PACKAGING_DIR / "payload"
DIST_DIR = ROOT / "dist"
INSTALLER_SCHEMA = "v5.windows_installer_bundle/0.1"


def build_windows_installer_v5(profile: str = "professor_demo") -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    profile_manifest = build_packaging_profile(profile)
    dependency_cache = build_dependency_cache_manifest(ROOT)
    repair_plan = build_runtime_repair_plan(ROOT)
    payload = _latest_v5_bundle(profile=profile)
    payload_dst = PAYLOAD_DIR / "targetcompass_v5_local_bundle.zip"
    shutil.copy2(payload, payload_dst)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = DIST_DIR / f"TargetCompassV5_Windows_Installer_{stamp}.zip"
    manifest = {
        "schema_version": INSTALLER_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "profile_manifest": profile_manifest,
        "installer": out.name,
        "payload_source": _display_path(payload),
        "payload_name": "payload/targetcompass_v5_local_bundle.zip",
        "entrypoint": "Install-TargetCompassV5.ps1",
        "launcher": "Launch-TargetCompassV5.ps1",
        "launcher_cmd": "TargetCompassV5-Launcher.cmd",
        "stop": "Stop-TargetCompassV5.ps1",
        "restart": "Restart-TargetCompassV5.ps1",
        "uninstall": "Uninstall-TargetCompassV5.ps1",
        "offline_cache_prepare": "Prepare-OfflineRuntimeCache.ps1",
        "formal_installer": {
            "type": "Inno Setup",
            "script": "TargetCompassV5.iss",
            "build_script": "build_setup_exe.ps1",
            "output": "dist/TargetCompassV5_Setup.exe",
            "wizard_style": "modern",
            "icon": "TargetCompassV5.ico",
            "signing": {
                "status": "not_configured",
                "requires_certificate": True,
                "environment_variable": "TARGETCOMPASS_CODESIGN_CERT",
                "signtool": "signtool.exe",
            },
        },
        "runtime_strategy": "embedded_python_with_optional_offline_cache",
        "requires_preinstalled_python": False,
        "requires_powershell": True,
        "gui_strategy": "inno_setup_or_zip_wrapper_with_desktop_and_start_menu_shortcuts_launch_local_web_ui",
        "default_demo_project": "vascular_aging_demo",
        "install_self_checks": ["embedded_python", "Rscript", "Nextflow", "Docker CLI", "Docker daemon", "v5-doctor"],
        "diagnostic_repair": {
            "script": "Repair-TargetCompassV5.ps1",
            "repair_plan": repair_plan,
        },
        "dependency_cache": dependency_cache,
        "offline_cache_dirs": ["runtime_cache", "runtime_cache/r_packages", "runtime_cache/nextflow", "runtime_cache/docker_images", "wheelhouse"],
        "optional_external_runtimes": ["R/Rscript", "Docker Desktop", "PostgreSQL/MinIO via docker compose", "Nextflow"],
        "browser_launch": "TargetCompassV5-Launcher.cmd starts the local service, waits for readiness, then opens the selected local URL in the system default browser.",
        "uninstall_features": ["stop local service", "remove shortcuts", "remove install dir", "optional project backup"],
        "service_management": {
            "start": "TargetCompassV5-Launcher.cmd",
            "stop": "Stop-TargetCompassV5.ps1",
            "restart": "Restart-TargetCompassV5.ps1",
            "status": "python tc_lite.py v5-service-control --project vascular_aging_demo",
            "port_conflict_recovery": "v5 service control manifest reports selected fallback port and shortcut update advice",
        },
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGING_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(PACKAGING_DIR).as_posix())
        zf.writestr("installer_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("packaging_profile.json", json.dumps(profile_manifest, indent=2, ensure_ascii=False))
        zf.writestr("dependency_cache_manifest.json", json.dumps(dependency_cache, indent=2, ensure_ascii=False))
        zf.writestr("runtime_repair_plan.json", json.dumps(repair_plan, indent=2, ensure_ascii=False))
    manifest_path = DIST_DIR / f"{out.stem}_manifest.json"
    manifest["size_bytes"] = out.stat().st_size
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _latest_v5_bundle(profile: str = "") -> Path:
    patterns = [f"targetcompass_v5_{profile}_bundle_*.zip", "targetcompass_v5_local_bundle_*.zip"] if profile else ["targetcompass_v5_local_bundle_*.zip", "targetcompass_v5_*_bundle_*.zip"]
    for pattern in patterns:
        bundles = sorted(set(DIST_DIR.glob(pattern)), key=lambda path: path.stat().st_mtime)
        if bundles:
            return bundles[-1]
    raise FileNotFoundError("No targetcompass_v5 bundle found in dist. Run scripts/export_v5_local_bundle.py first.")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["professor_demo", "developer"], default="professor_demo")
    args = parser.parse_args()
    print(build_windows_installer_v5(profile=args.profile))
