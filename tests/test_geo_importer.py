import gzip
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from targetcompass_lite.deg import run_deg
from targetcompass_lite.geo_importer import (
    GeoImportError,
    build_metadata,
    download_file,
    geo_platform_annotation_url,
    extract_sample_metadata_table,
    geo_status_path,
    import_geo_series_auto,
    infer_platform_id,
    infer_grouping,
    infer_grouping_from_column,
    import_geo_series,
    parse_series_matrix,
)
from targetcompass_lite.screening import screen_project
from targetcompass_lite.validators import validate_dataset_card


SERIES = """!Sample_title\t"young rep1"\t"young rep2"\t"senescent rep1"\t"senescent rep2"
!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"\t"GSM4"
!Sample_characteristics_ch1\t"condition: young"\t"condition: young"\t"condition: senescent"\t"condition: senescent"
!series_matrix_table_begin
ID_REF\tGSM1\tGSM2\tGSM3\tGSM4
IL6\t1\t1.2\t4\t4.2
VCAM1\t2\t2.1\t5\t5.1
BAD_PROBE_001\t7\t7\t7\t7
!series_matrix_table_end
"""

ONE_PER_GROUP_SERIES = """!Sample_title\t"young rep1"\t"senescent rep1"
!Sample_geo_accession\t"GSM1"\t"GSM2"
!Sample_characteristics_ch1\t"condition: young"\t"condition: senescent"
!series_matrix_table_begin
ID_REF\tGSM1\tGSM2
IL6\t1\t4
VCAM1\t2\t5
!series_matrix_table_end
"""

PROBE_SERIES = """!Sample_title\t"young rep1"\t"young rep2"\t"senescent rep1"\t"senescent rep2"
!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"\t"GSM4"
!Sample_platform_id\t"GPL1234"\t"GPL1234"\t"GPL1234"\t"GPL1234"
!Sample_characteristics_ch1\t"condition: young"\t"condition: young"\t"condition: senescent"\t"condition: senescent"
!series_matrix_table_begin
ID_REF\tGSM1\tGSM2\tGSM3\tGSM4
ILMN_001\t1\t1.2\t4\t4.2
AFFX_BAD\t2\t2.1\t5\t5.1
!series_matrix_table_end
"""


class GeoImporterTest(unittest.TestCase):
    def test_parse_series_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "GSETEST_series_matrix.txt.gz"
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(SERIES)
            meta, samples, matrix = parse_series_matrix(path)
            self.assertEqual(samples, ["GSM1", "GSM2", "GSM3", "GSM4"])
            self.assertIn("!Sample_title", meta)
            self.assertEqual(matrix["IL6"]["GSM3"], 4.0)

    def test_manual_grouping_from_locked_column(self):
        rows = [
            {"sample_id": "S1", "condition": "case"},
            {"sample_id": "S2", "condition": "case"},
            {"sample_id": "S3", "condition": "control"},
            {"sample_id": "S4", "condition": "control"},
        ]
        inference = infer_grouping_from_column(rows, "condition", case_label="case", control_label="control")
        self.assertEqual(inference.group_column, "condition")
        self.assertEqual(inference.case_label, "case")
        self.assertEqual(inference.control_label, "control")
        self.assertEqual(inference.sample_groups["S1"], "case")
        self.assertEqual(inference.sample_groups["S4"], "control")

    def test_download_failure_is_structured_and_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_urlopen(url, timeout=60):
                raise URLError("offline")

            with patch("targetcompass_lite.geo_importer.urllib.request.urlopen", fake_urlopen):
                with self.assertRaises(GeoImportError) as ctx:
                    download_file("https://example.invalid/GSE.txt.gz", Path(tmp) / "x.gz", force=True)

            self.assertEqual(ctx.exception.code, "GEO_DOWNLOAD_NETWORK_ERROR")
            self.assertTrue(ctx.exception.retryable)

    def test_imported_geo_dataset_can_enter_deg(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "dataset_cards").mkdir(parents=True)
            source = Path(tmp) / "source_series.txt.gz"
            with gzip.open(source, "wt", encoding="utf-8") as f:
                f.write(SERIES)

            def fake_download(url, out, force=False):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(source.read_bytes())
                return out

            with patch("targetcompass_lite.geo_importer.download_file", fake_download):
                result = import_geo_series(
                    project,
                    "GSE999999",
                    "senescent",
                    "young",
                    ["senescent"],
                    ["young"],
                    tissue="vascular endothelium",
                    organism="human",
                )

            self.assertEqual(result.samples, 4)
            self.assertGreaterEqual(result.genes, 2)
            self.assertEqual(validate_dataset_card(result.dataset_card), [])
            rows = screen_project(project)
            self.assertEqual(rows[0]["grade"], "B")
            deg_path = run_deg(project, "GSE999999")
            self.assertTrue(Path(deg_path).exists())

    def test_auto_grouping_infers_metadata_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "GSETEST_series_matrix.txt.gz"
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(SERIES)
            sample_meta, samples, _ = parse_series_matrix(path)
            table = extract_sample_metadata_table(sample_meta, samples)
            inference = infer_grouping(table, case_hint="senescent", control_hint="young")
            self.assertEqual(inference.group_column, "condition")
            self.assertEqual(inference.case_label, "senescent")
            self.assertEqual(inference.control_label, "young")
            self.assertGreaterEqual(inference.confidence, 55)

    def test_auto_imported_geo_dataset_can_enter_deg(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = Path(tmp) / "source_series.txt.gz"
            with gzip.open(source, "wt", encoding="utf-8") as f:
                f.write(SERIES)

            def fake_download(url, out, force=False):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(source.read_bytes())
                return out

            with patch("targetcompass_lite.geo_importer.download_file", fake_download):
                result = import_geo_series_auto(
                    project,
                    "GSE999990",
                    tissue="vascular endothelium",
                    organism="human",
                    case_hint="senescent",
                    control_hint="young",
                )

            self.assertEqual(result.samples, 4)
            self.assertEqual(result.case_n, 2)
            self.assertEqual(result.control_n, 2)
            self.assertTrue((project / "data" / "GSE999990" / "metadata_profile.json").exists())
            self.assertTrue((project / "data" / "GSE999990" / "group_inference.json").exists())
            self.assertTrue((project / "data" / "GSE999990" / "handoff_manifest.json").exists())
            self.assertEqual(validate_dataset_card(result.dataset_card), [])
            rows = screen_project(project)
            self.assertEqual(rows[0]["grade"], "B")
            self.assertTrue(Path(run_deg(project, "GSE999990")).exists())

    def test_ambiguous_case_control_match_is_skipped(self):
        sample_meta = {
            "!Sample_title": ["case control mixed", "clean control"],
            "!Sample_geo_accession": ["GSM1", "GSM2"],
        }
        rows, warnings = build_metadata(
            sample_meta,
            ["GSM1", "GSM2"],
            "case",
            "control",
            ["case"],
            ["control"],
        )
        self.assertEqual([row["sample_id"] for row in rows], ["GSM2"])
        self.assertIn("matched both case and control", warnings[0])

    def test_group_assignment_failure_writes_recovery_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = Path(tmp) / "source_series.txt.gz"
            with gzip.open(source, "wt", encoding="utf-8") as f:
                f.write(SERIES)

            def fake_download(url, out, force=False):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(source.read_bytes())
                return out

            with patch("targetcompass_lite.geo_importer.download_file", fake_download):
                with self.assertRaises(GeoImportError) as ctx:
                    import_geo_series(project, "GSE999991", "case", "control", ["missing_case"], ["missing_control"])

            self.assertEqual(ctx.exception.code, "GEO_GROUP_ASSIGNMENT_FAILED")
            status = geo_status_path(project, "GSE999991").read_text(encoding="utf-8")
            self.assertIn("sample_preview", status)
            self.assertIn("case/control", status)

    def test_platform_id_and_annotation_url_are_inferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "GSETEST_series_matrix.txt.gz"
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(PROBE_SERIES)
            sample_meta, _, _ = parse_series_matrix(path)

            self.assertEqual(infer_platform_id(sample_meta), "GPL1234")
            self.assertEqual(
                geo_platform_annotation_url("GPL1234"),
                "https://ftp.ncbi.nlm.nih.gov/geo/platforms/GPL1nnn/GPL1234/annot/GPL1234.annot.gz",
            )

    def test_sample_size_failure_is_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = Path(tmp) / "source_series.txt.gz"
            with gzip.open(source, "wt", encoding="utf-8") as f:
                f.write(ONE_PER_GROUP_SERIES)

            def fake_download(url, out, force=False):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(source.read_bytes())
                return out

            with patch("targetcompass_lite.geo_importer.download_file", fake_download):
                with self.assertRaises(GeoImportError) as ctx:
                    import_geo_series(project, "GSE999992", "senescent", "young", ["senescent"], ["young"])

            self.assertEqual(ctx.exception.code, "GEO_SAMPLE_SIZE_TOO_SMALL")
            self.assertIn("fewer than two samples", ctx.exception.message)

    def test_platform_annotation_missing_is_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = Path(tmp) / "source_series.txt.gz"
            with gzip.open(source, "wt", encoding="utf-8") as f:
                f.write(PROBE_SERIES)

            def fake_download(url, out, force=False):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(source.read_bytes())
                return out

            with patch("targetcompass_lite.geo_importer.download_file", fake_download):
                with self.assertRaises(GeoImportError) as ctx:
                    import_geo_series(project, "GSE999993", "senescent", "young", ["senescent"], ["young"])

            self.assertEqual(ctx.exception.code, "GEO_PLATFORM_ANNOTATION_MISSING")
            self.assertIn("--platform-annotation", " ".join(ctx.exception.recovery))


if __name__ == "__main__":
    unittest.main()
