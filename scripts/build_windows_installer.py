import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGING_DIR = ROOT / "packaging" / "windows"
PAYLOAD_DIR = PACKAGING_DIR / "payload"
DIST_DIR = ROOT / "dist"
INSTALLER_SCHEMA = "v4.windows_installer_bundle/0.1"


def build_windows_installer() -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    payload = _latest_v4_bundle()
    payload_dst = PAYLOAD_DIR / "targetcompass_v4_local_bundle.zip"
    shutil.copy2(payload, payload_dst)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = DIST_DIR / f"TargetCompassV4_Windows_Installer_{stamp}.zip"
    manifest = {
        "schema_version": INSTALLER_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "installer": out.name,
        "payload_source": str(payload.relative_to(ROOT)).replace("\\", "/"),
        "payload_name": "payload/targetcompass_v4_local_bundle.zip",
        "entrypoint": "Install-TargetCompassV4.ps1",
        "uninstall": "Uninstall-TargetCompassV4.ps1",
        "runtime_strategy": "download_python_embeddable_at_install_time",
        "requires_preinstalled_python": False,
        "requires_powershell": True,
        "optional_external_runtimes": ["Docker Desktop", "WSL", "Nextflow"],
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGING_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(PACKAGING_DIR).as_posix())
        zf.writestr("installer_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    manifest_path = DIST_DIR / f"{out.stem}_manifest.json"
    manifest["size_bytes"] = out.stat().st_size
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _latest_v4_bundle() -> Path:
    bundles = sorted(DIST_DIR.glob("targetcompass_v4_local_bundle_*.zip"), key=lambda path: path.stat().st_mtime)
    if not bundles:
        raise FileNotFoundError("No targetcompass_v4_local_bundle_*.zip found in dist. Run scripts/export_v4_local_bundle.py first.")
    return bundles[-1]


if __name__ == "__main__":
    print(build_windows_installer())
