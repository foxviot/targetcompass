import csv
import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from targetcompass_lite.scrna_10x import build_10x_h5_donor_pseudobulk


class Scrna10xTest(unittest.TestCase):
    def test_build_10x_h5_donor_pseudobulk_writes_standard_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            raw = project / "data" / "GSE1" / "raw_extracted"
            raw.mkdir(parents=True)
            h5_a = raw / "GSM1_HM1_filtered_feature_bc_matrix.h5"
            h5_b = raw / "GSM2_HM2_filtered_feature_bc_matrix.h5"
            _write_10x_h5(h5_a, ["IL6", "CD44"], [[1, 0], [2, 3]])
            _write_10x_h5(h5_b, ["IL6", "CD44"], [[4, 1], [0, 2]])
            manifest = {
                "h5_files": [
                    {"name": h5_a.name, "path": str(h5_a.relative_to(project)).replace("\\", "/")},
                    {"name": h5_b.name, "path": str(h5_b.relative_to(project)).replace("\\", "/")},
                ]
            }
            (project / "data" / "GSE1" / "geo_raw_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            metadata = project / "data" / "GSE1" / "metadata.tsv"
            with metadata.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["sample_id", "donor_id", "group", "condition", "cell_type", "geo_accession_list_gsmxxx_eg_gsm5098661", "raw_file"],
                    delimiter="\t",
                )
                writer.writeheader()
                writer.writerow({"sample_id": "S1", "donor_id": "HM1", "group": "old", "condition": "old", "cell_type": "mixed", "geo_accession_list_gsmxxx_eg_gsm5098661": "GSM1", "raw_file": "HM1_filtered_feature_bc_matrix.h5"})
                writer.writerow({"sample_id": "S2", "donor_id": "HM2", "group": "young", "condition": "young", "cell_type": "mixed", "geo_accession_list_gsmxxx_eg_gsm5098661": "GSM2", "raw_file": "HM2_filtered_feature_bc_matrix.h5"})

            out = build_10x_h5_donor_pseudobulk(project, "GSE1", "data/GSE1/metadata.tsv")

            self.assertEqual(out["samples"], 2)
            self.assertEqual(out["genes"], 2)
            matrix = (project / out["outputs"]["expression_matrix"]).read_text(encoding="utf-8")
            self.assertIn("IL6\t1\t5", matrix)
            self.assertIn("CD44\t5\t2", matrix)
            self.assertTrue((project / out["outputs"]["dataset_card"]).exists())


def _write_10x_h5(path: Path, genes: list[str], dense: list[list[int]]) -> None:
    import scipy.sparse as sp

    arr = np.array(dense)
    csc = sp.csc_matrix(arr)
    with h5py.File(path, "w") as h5:
        matrix = h5.create_group("matrix")
        matrix.create_dataset("data", data=csc.data)
        matrix.create_dataset("indices", data=csc.indices)
        matrix.create_dataset("indptr", data=csc.indptr)
        matrix.create_dataset("shape", data=np.array(csc.shape))
        matrix.create_dataset("barcodes", data=np.array([b"cell1", b"cell2"]))
        features = matrix.create_group("features")
        features.create_dataset("name", data=np.array([g.encode() for g in genes]))
        features.create_dataset("id", data=np.array([g.encode() for g in genes]))
        features.create_dataset("feature_type", data=np.array([b"Gene Expression"] * len(genes)))
