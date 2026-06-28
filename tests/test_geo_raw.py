import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from targetcompass_lite.geo_raw import geo_raw_archive_url, prepare_geo_raw


class _FakeResponse:
    def __init__(self, data: bytes):
        self._bio = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        return self._bio.read(size)


class GeoRawTest(unittest.TestCase):
    def test_geo_raw_archive_url_uses_series_bucket(self):
        self.assertEqual(
            geo_raw_archive_url("GSE167186"),
            "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE167nnn/GSE167186/suppl/GSE167186_RAW.tar",
        )

    def test_prepare_geo_raw_downloads_extracts_and_inventories_h5(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            tar_bytes = _tar_with_file("HM1_filtered_feature_bc_matrix.h5", b"not really h5")

            with patch("targetcompass_lite.geo_raw.urllib.request.urlopen", return_value=_FakeResponse(tar_bytes)):
                result = prepare_geo_raw(project, "GSE167186")

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["h5_count"], 1)
            self.assertEqual(result["h5_files"][0]["sample_id_guess"], "HM1")
            self.assertEqual(result["h5_files"][0]["inspect_status"], "failed")
            self.assertTrue((project / "data" / "GSE167186" / "geo_raw_manifest.json").exists())

    def test_prepare_geo_raw_records_structured_download_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()

            def fail(_req, timeout=120):
                raise URLError("offline")

            with patch("targetcompass_lite.geo_raw.urllib.request.urlopen", fail):
                result = prepare_geo_raw(project, "GSE167186")

            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["error"]["retryable"])
            self.assertTrue(result["recovery"])
            saved = json.loads((project / "data" / "GSE167186" / "geo_raw_status.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed")


def _tar_with_file(name: str, data: bytes) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return out.getvalue()
