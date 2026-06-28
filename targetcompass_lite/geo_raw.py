import hashlib
import json
import tarfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError


GEO_FTP = "https://ftp.ncbi.nlm.nih.gov/geo"


@dataclass
class GeoRawResult:
    accession: str
    status: str
    raw_url: str
    raw_archive: Path
    extract_dir: Path
    manifest_path: Path
    h5_files: list[dict]
    recovery: list[str]

    def to_dict(self, project_dir: Path) -> dict:
        return {
            "schema_version": "v4.geo_raw_manifest/0.1",
            "accession": self.accession,
            "status": self.status,
            "raw_url": self.raw_url,
            "raw_archive": _rel(self.raw_archive, project_dir),
            "extract_dir": _rel(self.extract_dir, project_dir),
            "manifest_path": _rel(self.manifest_path, project_dir),
            "h5_files": self.h5_files,
            "h5_count": len(self.h5_files),
            "recovery": self.recovery,
        }


def geo_raw_archive_url(accession: str) -> str:
    accession = accession.upper().strip()
    if not accession.startswith("GSE") or not accession[3:].isdigit():
        raise ValueError(f"Unsupported GEO accession: {accession}")
    prefix = accession[:-3] + "nnn"
    return f"{GEO_FTP}/series/{prefix}/{accession}/suppl/{accession}_RAW.tar"


def prepare_geo_raw(
    project_dir: Path,
    accession: str,
    force_download: bool = False,
    extract: bool = True,
    extract_h5_only: bool = True,
    timeout: int = 120,
) -> dict:
    accession = accession.upper().strip()
    data_dir = project_dir / "data" / accession
    raw_dir = data_dir / "raw"
    extract_dir = data_dir / "raw_extracted"
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_url = geo_raw_archive_url(accession)
    archive = raw_dir / f"{accession}_RAW.tar"
    manifest_path = data_dir / "geo_raw_manifest.json"
    status_path = data_dir / "geo_raw_status.json"

    recovery: list[str] = []
    status = "success"
    try:
        _download(raw_url, archive, force=force_download, timeout=timeout)
        if extract:
            _extract_tar(archive, extract_dir, h5_only=extract_h5_only)
        h5_files = inspect_h5_inventory(extract_dir if extract else raw_dir, project_dir)
        if not h5_files:
            status = "review"
            recovery.extend(
                [
                    "No .h5 files were found after RAW preparation.",
                    "Check whether this GEO series uses mtx/barcode/features files instead of 10x H5.",
                    f"Inspect {archive} manually or re-run with --extract-all.",
                ]
            )
        result = GeoRawResult(
            accession=accession,
            status=status,
            raw_url=raw_url,
            raw_archive=archive,
            extract_dir=extract_dir,
            manifest_path=manifest_path,
            h5_files=h5_files,
            recovery=recovery,
        ).to_dict(project_dir)
    except Exception as exc:
        result = {
            "schema_version": "v4.geo_raw_manifest/0.1",
            "accession": accession,
            "status": "failed",
            "raw_url": raw_url,
            "raw_archive": _rel(archive, project_dir),
            "extract_dir": _rel(extract_dir, project_dir),
            "manifest_path": _rel(manifest_path, project_dir),
            "h5_files": [],
            "h5_count": 0,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "retryable": isinstance(exc, (HTTPError, URLError, TimeoutError, OSError)),
            },
            "recovery": [
                "Retry with --force-download if the archive is partial or corrupted.",
                "If network download is slow, manually place the RAW tar under data/<GSE>/raw/<GSE>_RAW.tar and rerun without --force-download.",
                "If GEO has no RAW tar, download supplemental H5/MTX files manually and place them under data/<GSE>/raw_extracted.",
            ],
        }
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    status_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    publish_paths = [manifest_path, status_path]
    if archive.exists():
        publish_paths.append(archive)
    publish_paths.extend(project_dir / row.get("path", "") for row in result.get("h5_files", []) if row.get("path"))
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            publish_paths,
            producer="geo_raw",
            artifact_type="geo_raw_output",
            task_id=f"geo_raw_{accession}",
            qc_status="pass" if result.get("status") == "success" else "review",
        )
    except Exception:
        pass
    return result


def inspect_h5_inventory(root: Path, project_dir: Path | None = None) -> list[dict]:
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.rglob("*.h5")):
        rel = _rel(path, project_dir or root)
        row = {
            "path": rel,
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _file_sha256(path),
            "format": "unknown_h5",
            "sample_id_guess": _sample_id_from_h5(path.name),
        }
        row.update(_inspect_10x_h5(path))
        rows.append(row)
    return rows


def _download(url: str, out: Path, force: bool, timeout: int) -> Path:
    if out.exists() and out.stat().st_size > 0 and not force:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".partial")
    req = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.4 GEO RAW importer"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        with tmp.open("wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    tmp.replace(out)
    return out


def _extract_tar(archive: Path, out_dir: Path, h5_only: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            if h5_only and not name.lower().endswith(".h5"):
                continue
            target = (out_dir / name).resolve()
            if out_dir.resolve() not in [target.parent, *target.parents]:
                raise ValueError(f"Unsafe tar member path: {member.name}")
            source = tar.extractfile(member)
            if source is None:
                continue
            with target.open("wb") as f:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)


def _inspect_10x_h5(path: Path) -> dict:
    try:
        import h5py
    except Exception as exc:
        return {"inspect_status": "skipped", "inspect_error": f"h5py unavailable: {exc}"}
    try:
        with h5py.File(path, "r") as h5:
            if "matrix" not in h5:
                return {"inspect_status": "review", "inspect_error": "missing /matrix group"}
            matrix = h5["matrix"]
            genes = _dataset_len(matrix, "features/name") or _dataset_len(matrix, "features/id")
            cells = _dataset_len(matrix, "barcodes")
            nnz = _dataset_len(matrix, "data")
            return {
                "inspect_status": "pass" if genes and cells else "review",
                "format": "10x_h5",
                "genes": genes,
                "cells": cells,
                "nonzero_entries": nnz,
            }
    except Exception as exc:
        return {"inspect_status": "failed", "inspect_error": str(exc)}


def _dataset_len(group, name: str) -> int:
    try:
        return int(len(group[name]))
    except Exception:
        return 0


def _sample_id_from_h5(name: str) -> str:
    lowered = name.lower()
    for suffix in ["_filtered_feature_bc_matrix.h5", "_raw_feature_bc_matrix.h5", ".h5"]:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)
