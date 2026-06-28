import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.artifacts import load_artifact_registry
from targetcompass_lite.canonical.local_demo_runner import run_v5_local_demo
from targetcompass_lite.canonical.nextflow_execution import load_qc_reports, load_task_runs


def fake_fetch_json(url: str, timeout: int):
    if "db=gds" in url and "esearch.fcgi" in url:
        return {"esearchresult": {"idlist": ["1001"]}}
    if "db=gds" in url and "esummary.fcgi" in url:
        return {
            "result": {
                "uids": ["1001"],
                "1001": {
                    "uid": "1001",
                    "accession": "GSE_LOCAL",
                    "title": "Human sarcopenia skeletal muscle expression",
                    "summary": "Muscle transcriptome metadata.",
                    "organism": "Homo sapiens",
                    "platform": "GPL570",
                },
            }
        }
    if "db=pubmed" in url and "esearch.fcgi" in url:
        return {"esearchresult": {"idlist": ["3001"]}}
    if "db=pubmed" in url and "esummary.fcgi" in url:
        return {"result": {"uids": ["3001"], "3001": {"uid": "3001", "title": "SASP in muscle", "source": "Journal"}}}
    return {"resultList": {"result": [{"id": "PMC_LOCAL", "title": "Sarcopenia SASP review", "abstractText": "Abstract"}]}}


class CanonicalLocalDemoRunnerTest(unittest.TestCase):
    def test_run_v5_local_demo_writes_state_handoffs_resources_taskrun_qc_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            result = run_v5_local_demo(
                project,
                "肌少症患者肌肉背景细胞中是否存在特征性表面分子的 SASP 高细胞？",
                sources=("geo", "pubmed", "europe_pmc"),
                fetch_json=fake_fetch_json,
            )
            self.assertEqual(result["status"], "completed")
            self.assertTrue((project / "v5" / "project_state.json").exists())
            self.assertTrue((project / "v5" / "handoffs").exists())
            self.assertTrue((project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").exists())
            self.assertTrue((project / "v5" / "local_demo" / "local_demo_run.json").exists())
            self.assertTrue(result["task_run_refs"])
            self.assertTrue(result["qc_report_refs"])
            self.assertTrue(result["run_workspace_ref"])
            workspace = project / result["run_workspace_ref"]
            self.assertTrue(workspace.exists())
            workspace_manifest = json.loads(workspace.read_text(encoding="utf-8"))
            self.assertGreaterEqual(workspace_manifest["copied_count"], 3)
            self.assertTrue((project / "v5" / "runs" / result["run_id"] / "files" / "v5" / "resource_discovery" / "resource_discovery_bundle.json").exists())
            state = json.loads((project / "v5" / "project_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current_stage"], "QC_COMPLETED")
            self.assertGreaterEqual(len(load_task_runs(project)), 1)
            self.assertGreaterEqual(len(load_qc_reports(project)), 1)
            registry = load_artifact_registry(project)
            self.assertGreaterEqual(len(registry), 2)
            self.assertTrue(all(row["checksum_sha256"] for row in registry))
            bundle = json.loads((project / "v5" / "resource_discovery" / "resource_discovery_bundle.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(bundle["verified_candidate_count"], 1)
            self.assertEqual(bundle["locked_dataset_count"], 0)


if __name__ == "__main__":
    unittest.main()
