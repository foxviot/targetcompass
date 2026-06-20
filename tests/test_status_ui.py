import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.run_state import write_status
from targetcompass_lite.status_ui import build_status_center


class StatusUiTest(unittest.TestCase):
    def test_status_center_includes_stage_cards_and_geo_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            write_status(
                project,
                "failed",
                "Workflow failed.",
                stages=[{"name": "execution", "status": "failed", "message": "boom", "details": {"purpose": "run"}}],
                failure_reason="boom",
            )
            geo = project / "data" / "GSEFAIL"
            geo.mkdir(parents=True)
            (geo / "geo_import_status.json").write_text(
                json.dumps(
                    {
                        "accession": "GSEFAIL",
                        "status": "failed",
                        "error": {
                            "code": "GEO_GROUP_ASSIGNMENT_FAILED",
                            "stage": "metadata_grouping",
                            "message": "group failed",
                            "recovery": ["inspect metadata_profile.json"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            center = build_status_center(project)
            self.assertEqual(len(center["stage_cards"]), 6)
            self.assertTrue(any(item["name"] == "execution" and item["status"] == "failed" for item in center["stage_cards"]))
            self.assertTrue(any("GSEFAIL" in item["title"] for item in center["recovery"]))
            self.assertEqual(center["geo_statuses"][0]["code"], "GEO_GROUP_ASSIGNMENT_FAILED")
            actions = center["geo_statuses"][0]["recovery_actions"]
            self.assertTrue(any(action["mode"] == "manual" for action in actions))
            self.assertTrue(any(action["mode"] == "low_confidence" for action in actions))

    def test_geo_platform_error_exposes_annotation_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            geo = project / "data" / "GSEGPL"
            geo.mkdir(parents=True)
            (geo / "geo_import_status.json").write_text(
                json.dumps(
                    {
                        "accession": "GSEGPL",
                        "status": "failed",
                        "error": {
                            "code": "GEO_PLATFORM_ANNOTATION_MISSING",
                            "stage": "probe_to_gene_mapping",
                            "message": "annotation missing",
                            "recovery": ["add platform annotation"],
                            "details": {"platform_annotation": "", "symbol_column": ""},
                        },
                    }
                ),
                encoding="utf-8",
            )
            center = build_status_center(project)
            actions = center["geo_statuses"][0]["recovery_actions"]
            self.assertTrue(any(action["mode"] == "platform_annotation" for action in actions))


if __name__ == "__main__":
    unittest.main()
