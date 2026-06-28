import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.evidence_db import import_evidence
from targetcompass_lite.evidence_levels import classify_evidence_level


class SaspCellSurfaceEvidenceTest(unittest.TestCase):
    def test_imports_sasp_cell_type_and_surface_marker_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            (project / "research_spec.json").write_text(
                json.dumps({"disease_scope": {"canonical": "sarcopenia"}, "candidate_gene_sets": {"sasp": ["IL6", "CXCL8"]}}),
                encoding="utf-8",
            )
            deg_dir = project / "results" / "bulk_deg_ds1"
            deg_dir.mkdir(parents=True)
            (deg_dir / "deg_results.tsv").write_text(
                "gene_symbol\tlogFC\tp_value\tadj_p_value\tdirection\nIL6\t2\t0.001\t0.01\tup\nCXCL8\t1\t0.01\t0.04\tup\n",
                encoding="utf-8",
            )
            sasp_dir = project / "results" / "sasp_score"
            sasp_dir.mkdir(parents=True)
            (sasp_dir / "sasp_gene_scores.tsv").write_text(
                "dataset_id\tgene_symbol\tlogFC\tadj_p_value\tdirection\tis_sasp_core\tsasp_component_score\tsource_artifact\n"
                "ds1\tIL6\t2\t0.01\tup\ttrue\t12.5\tresults/bulk_deg_ds1/deg_results.tsv\n",
                encoding="utf-8",
            )
            ann_dir = project / "results" / "annotation"
            ann_dir.mkdir(parents=True)
            (ann_dir / "accessibility_annotation.tsv").write_text(
                "gene_symbol\troute\taccessibility_status\tsource\nIL6\tsecreted\tSUPPORTED\tfixture\nCXCL8\tsurface\tSUPPORTED\tfixture\n",
                encoding="utf-8",
            )
            cell_dir = project / "results" / "cell_type_evidence"
            cell_dir.mkdir(parents=True)
            (cell_dir / "cell_type_evidence.tsv").write_text(
                "evidence_id\tproject_id\tentity_symbol\tentity_type\tcell_type\ttissue\tevidence_source\tevidence_type\tevidence_level\tquality_score\tsource_dataset\tartifact_path\tlimitation\treview_status\trun_id\tmodule_version\tcreated_at\n"
                "celltype_il6\tdemo\tIL6\tgene\tFAP\tskeletal muscle\tfixture\tcell_type_expression\tL2_database\t0.6\tfixture\tresults/cell.tsv\tannotation only\tPENDING\tcell_type\tcell_type_evidence_v1\tnow\n",
                encoding="utf-8",
            )

            import_evidence(project)

            con = sqlite3.connect(project / "evidence.sqlite")
            rows = con.execute("SELECT entity_symbol, evidence_type, evidence_level, evidence_weight FROM evidence_item ORDER BY evidence_type, entity_symbol").fetchall()
            con.close()
            evidence_types = [row[1] for row in rows]
            self.assertIn("sasp_score", evidence_types)
            self.assertIn("cell_type_expression", evidence_types)
            self.assertIn("surface_marker_annotation", evidence_types)
            sasp = next(row for row in rows if row[1] == "sasp_score")
            self.assertEqual(sasp[2], "L3_omics")
            surface = next(row for row in rows if row[1] == "surface_marker_annotation")
            self.assertEqual(surface[2], "L2_database")

    def test_surface_marker_limitation_does_not_promote_to_experimental(self):
        level, weight, basis = classify_evidence_level(
            {
                "evidence_type": "surface_marker_annotation",
                "module_version": "surface_marker_annotation_v1",
                "limitation": "annotation-level surface/secreted/ECD evidence; not experimental accessibility proof",
            }
        )
        self.assertEqual(level, "L2_database")
        self.assertLess(weight, 1.0)
        self.assertIn("database", basis.lower())


if __name__ == "__main__":
    unittest.main()
