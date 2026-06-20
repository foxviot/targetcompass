import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.annotation import annotate_project
from targetcompass_lite.paths import KB


def _write_project(tmp: str) -> Path:
    project = Path(tmp) / "demo"
    out = project / "results" / "bulk_deg_ds_test"
    out.mkdir(parents=True)
    (out / "deg_results.tsv").write_text(
        "gene_symbol\tcase_mean\tcontrol_mean\tlogFC\tp_value\tadj_p_value\tdirection\n"
        "SELE\t10\t2\t2\t0.01\t0.02\tup\n"
        "UNKNOWN_GENE\t5\t1\t2\t0.02\t0.04\tup\n",
        encoding="utf-8",
    )
    return project


class AnnotationTest(unittest.TestCase):
    def test_curated_annotation_tables_include_expanded_vascular_markers(self):
        access = (KB / "annotation_tables" / "accessibility.tsv").read_text(encoding="utf-8")
        safety = (KB / "annotation_tables" / "safety.tsv").read_text(encoding="utf-8")
        self.assertIn("SELE\tsurface\tSUPPORTED\tcurated_local_v0", access)
        self.assertIn("CDH5\tsurface\tSUPPORTED\tcurated_local_v0", access)
        self.assertIn("SELE\tREVIEW_REQUIRED\tinflammatory_endothelium", safety)

    def test_unknown_review_file_lists_missing_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _write_project(tmp)
            access_path, safety_path, review_path = annotate_project(project)
            self.assertTrue(access_path.exists())
            self.assertTrue(safety_path.exists())
            text = review_path.read_text(encoding="utf-8")
            self.assertIn("UNKNOWN_GENE", text)
            self.assertIn("accessibility,safety", text)
            self.assertNotIn("SELE\t", text)


if __name__ == "__main__":
    unittest.main()
