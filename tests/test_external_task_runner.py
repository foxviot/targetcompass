import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.external_task_runner import run_external_codex_task_packets


class ExternalTaskRunnerTest(unittest.TestCase):
    def test_missing_external_inputs_fail_and_block_dependents(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            packet_file = project / "packets.json"
            packet_file.write_text(
                json.dumps(
                    {
                        "schema_version": "v4.external_codex_task_packets/0.1",
                        "packets": [
                            {
                                "task_id": "t1",
                                "name": "QC",
                                "purpose": "qc",
                                "input_artifacts": ["raw_scRNAseq_data"],
                                "output_artifacts": ["QC_filtered_data"],
                                "dependencies": [],
                                "method_contract_id": "method_contract_scRNAseq_QC",
                                "acceptance_criteria": ["input exists"],
                                "failure_condition": "missing input",
                                "notes": "test",
                            },
                            {
                                "task_id": "t2",
                                "name": "Annotate",
                                "purpose": "annotate",
                                "input_artifacts": ["QC_filtered_data"],
                                "output_artifacts": ["cell_type_annotations"],
                                "dependencies": ["t1"],
                                "method_contract_id": "method_contract_cell_type_annotation",
                                "acceptance_criteria": ["dependency ok"],
                                "failure_condition": "dependency failed",
                                "notes": "test",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_external_codex_task_packets(project, packet_file)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["blocked_count"], 1)
            self.assertEqual(result["tasks"][0]["status"], "failed")
            self.assertEqual(result["tasks"][1]["status"], "blocked")
            self.assertIn("raw_scRNAseq_data", result["tasks"][0]["failure_reason"])
            self.assertTrue((project / result["manifest"]).exists())


if __name__ == "__main__":
    unittest.main()
