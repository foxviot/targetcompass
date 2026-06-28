import csv
import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.wet_lab_protocol import (
    build_wet_lab_sop_bundle,
    build_wet_lab_protocol_bundle,
    build_wet_lab_protocols,
    load_wet_lab_signoffs,
    signoff_wet_lab_protocol,
    summarize_wet_lab_protocol_signoffs,
)


class CanonicalWetLabProtocolTest(unittest.TestCase):
    def test_build_protocol_manifest_requires_human_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project_with_candidates(Path(tmp) / "demo")

            manifest = build_wet_lab_protocols(project, actor="unit", max_protocols=1)

            self.assertEqual(manifest["protocol_count"], 1)
            protocol = manifest["protocols"][0]
            self.assertEqual(protocol["protocol_version"], 1)
            self.assertEqual(protocol["human_review_gate"]["status"], "pending_signoff")
            self.assertIn("approval_requirements", protocol)
            self.assertIn("decision_points", protocol)
            self.assertIn("No wet-lab execution should start before human signoff.", protocol["exclusions"])
            self.assertTrue((project / "v5" / "wet_lab_protocols" / "wet_lab_protocol_manifest.json").exists())

    def test_signoff_requires_reason_and_records_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project_with_candidates(Path(tmp) / "demo")
            manifest = build_wet_lab_protocols(project, actor="unit", max_protocols=1)
            protocol_id = manifest["protocols"][0]["protocol_id"]

            with self.assertRaises(ValueError):
                signoff_wet_lab_protocol(project, protocol_id, signer="pi", decision="approved", reason="")

            signoff = signoff_wet_lab_protocol(project, protocol_id, signer="pi", decision="needs_revision", reason="Add orthogonal validation.")
            self.assertEqual(signoff["decision"], "needs_revision")
            self.assertEqual(len(load_wet_lab_signoffs(project)), 1)
            summary = summarize_wet_lab_protocol_signoffs(project, manifest["protocols"])
            self.assertEqual(summary["needs_revision_count"], 1)
            self.assertTrue((project / "v5" / "wet_lab_protocols" / "wet_lab_protocol_signoff_bundle.json").exists())

    def test_bundle_reports_signed_out_after_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project_with_candidates(Path(tmp) / "demo")
            manifest = build_wet_lab_protocols(project, actor="unit", max_protocols=1)
            protocol_id = manifest["protocols"][0]["protocol_id"]

            pending = build_wet_lab_protocol_bundle(project, actor="unit")
            self.assertEqual(pending["status"], "review_required")

            signoff_wet_lab_protocol(project, protocol_id, signer="pi", decision="approved", reason="Evidence and controls are sufficient for validation planning.")
            approved = build_wet_lab_protocol_bundle(project, actor="unit")

            self.assertEqual(approved["status"], "signed_out")
            self.assertEqual(approved["signoff_summary"]["signed_out_count"], 1)
            self.assertTrue((project / "v5" / "wet_lab_protocols" / "wet_lab_protocol_bundle.json").exists())

    def test_sop_bundle_tracks_governance_and_signoff_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project_with_candidates(Path(tmp) / "demo")
            manifest = build_wet_lab_protocols(project, actor="unit", max_protocols=1)

            pending = build_wet_lab_sop_bundle(project, actor="unit")
            self.assertEqual(pending["schema_version"], "v5.wet_lab_sop_bundle/0.1")
            self.assertEqual(pending["status"], "review_required")
            self.assertEqual(pending["review_required_count"], 1)
            sop = pending["sops"][0]
            self.assertIn("roles_and_responsibilities", sop)
            self.assertIn("pre_execution_gate", sop)
            self.assertIn("deviation_policy", sop)
            self.assertEqual(sop["sop_status"], "review_required")

            signoff_wet_lab_protocol(
                project,
                manifest["protocols"][0]["protocol_id"],
                signer="pi",
                decision="approved",
                reason="Governance and controls are sufficient for planning.",
            )
            approved = build_wet_lab_sop_bundle(project, actor="unit")
            self.assertEqual(approved["status"], "signed_out")
            self.assertEqual(approved["approved_for_planning_count"], 1)
            self.assertTrue((project / "v5" / "wet_lab_protocols" / "wet_lab_sop_bundle.json").exists())


def _project_with_candidates(project: Path) -> Path:
    project.mkdir(parents=True)
    with (project / "candidate_scores.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["gene", "route", "safety", "score"])
        writer.writeheader()
        writer.writerow({"gene": "CXCL8", "route": "secreted", "safety": "review", "score": "88"})
    exp_dir = project / "results" / "experiments"
    exp_dir.mkdir(parents=True)
    (exp_dir / "experiment_designs.json").write_text(
        json.dumps(
            [
                {
                    "candidate": "CXCL8",
                    "objective": "Validate secreted SASP marker association.",
                    "readouts": ["ELISA-level abundance", "SASP score consistency"],
                    "risks": ["Requires human review."],
                }
            ]
        ),
        encoding="utf-8",
    )
    return project


if __name__ == "__main__":
    unittest.main()
