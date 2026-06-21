import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.consistency import run_consistency_check
from targetcompass_lite.evidence_index import build_evidence_review_report_index, query_evidence_trace
from targetcompass_lite.registry_snapshots import build_registry_snapshots
from targetcompass_lite.service_boundaries import build_service_boundaries
from targetcompass_lite.services import (
    dispatch_service_request,
    query_service_audit,
    service_runtime_manifest,
)


class ServicesTest(unittest.TestCase):
    def test_service_runtime_manifest_identity_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            runtime = service_runtime_manifest(project)
            self.assertEqual(runtime["mode"], "local_standalone_services")
            self.assertEqual(runtime["external_tool_entrypoint"], "mcp_gateway")
            service_ids = {row["service_id"] for row in runtime["services"]}
            self.assertIn("project_api", service_ids)
            self.assertIn("evidence_service", service_ids)
            self.assertIn("registry_service", service_ids)
            self.assertIn("report_service", service_ids)

            result = dispatch_service_request("project_api", "boundaries", project, caller="mcp_gateway")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["result"]["policy"]["mcp_gateway_is_the_only_external_tool_entrypoint"], True)

            with self.assertRaises(RuntimeError):
                dispatch_service_request("report_service", "validate", project, caller="registry_service")

            audit = query_service_audit(project)
            self.assertEqual(audit["match_count"], 2)
            statuses = [row["status"] for row in audit["items"]]
            self.assertIn("success", statuses)
            self.assertIn("failed", statuses)

    def test_service_mode_matches_monolith_for_core_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _write_project(project)

            monolith_index = build_evidence_review_report_index(project)
            service_index = dispatch_service_request("evidence_service", "trace_index", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_index["index_id"], monolith_index["index_id"])
            self.assertEqual(service_index["evidence_count"], monolith_index["evidence_count"])

            monolith_query = query_evidence_trace(project, gene="CXCL8")
            service_query = dispatch_service_request(
                "evidence_service",
                "trace_query",
                project,
                {"gene": "CXCL8"},
                caller="mcp_gateway",
            )["result"]
            self.assertEqual(service_query["match_count"], monolith_query["match_count"])
            self.assertEqual(service_query["items"][0]["evidence_id"], monolith_query["items"][0]["evidence_id"])

            monolith_registry = build_registry_snapshots(project)
            service_registry = dispatch_service_request("registry_service", "snapshot", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_registry["snapshot_hash"], monolith_registry["snapshot_hash"])

            monolith_consistency = run_consistency_check(project)
            service_consistency = dispatch_service_request("report_service", "validate", project, caller="mcp_gateway")["result"]
            self.assertEqual(service_consistency["status"], monolith_consistency["status"])
            self.assertEqual(
                [row["check"] for row in service_consistency["checks"]],
                [row["check"] for row in monolith_consistency["checks"]],
            )


def _write_project(project: Path) -> None:
    project.mkdir()
    (project / "configs").mkdir()
    (project / "results").mkdir()
    (project / "reports").mkdir()
    (project / "v4").mkdir()
    (project / "research_spec.json").write_text(
        json.dumps({"project_id": "demo", "research_theme": "vascular aging", "disease_scope": {"canonical": "vascular aging"}}),
        encoding="utf-8",
    )
    (project / "analysis_plan.json").write_text(json.dumps({"project_id": "demo", "modules": []}), encoding="utf-8")
    (project / "research_interest.md").write_text("vascular aging\n", encoding="utf-8")
    source = project / "source.tsv"
    source.write_text("gene_symbol\troute\nCXCL8\tsecreted\n", encoding="utf-8")
    (project / "configs" / "knowledge_registry.json").write_text(
        json.dumps(
            [
                {
                    "resource_id": "demo_source",
                    "resource_type": "annotation_table",
                    "source_path": str(source),
                    "adapter": "copy",
                    "status": "registered",
                }
            ]
        ),
        encoding="utf-8",
    )
    _write_evidence_db(project)
    (project / "results" / "review_queue.json").write_text(
        json.dumps({"queue_count": 1, "items": [{"item_type": "causal_grade", "item_id": "CXCL8", "review_status": "pending"}]}),
        encoding="utf-8",
    )
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps(
            {
                "report_evidence_refs": {
                    "CXCL8": {"score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"]}
                },
                "evidence_review_report_index": {"path": "v4/evidence_review_report_index.json", "index_id": ""},
            }
        ),
        encoding="utf-8",
    )
    index = build_evidence_review_report_index(project)
    (project / "reports" / "target_report_structured.json").write_text(
        json.dumps(
            {
                "report_evidence_refs": {
                    "CXCL8": {"score_id": "score_1", "evidence_snapshot_id": "es_1", "evidence_refs": ["ev1"]}
                },
                "evidence_review_report_index": {"path": "v4/evidence_review_report_index.json", "index_id": index["index_id"]},
            }
        ),
        encoding="utf-8",
    )
    (project / "v4" / "work_order_dag.json").write_text(
        json.dumps({"nodes": [{"work_order_id": "wo1", "outputs": [{"path": "results/x.tsv"}], "evidence_writes": [{"evidence_id": "ev1"}]}]}),
        encoding="utf-8",
    )
    build_service_boundaries(project)


def _write_evidence_db(project: Path) -> None:
    con = sqlite3.connect(project / "evidence.sqlite")
    con.executescript(
        """
        CREATE TABLE evidence_item (
          evidence_id TEXT PRIMARY KEY, project_id TEXT, entity_symbol TEXT, entity_type TEXT,
          disease_context TEXT, organism TEXT, tissue TEXT, route TEXT, evidence_type TEXT,
          direction TEXT, effect_size REAL, p_value REAL, quality_score REAL, review_status TEXT,
          source_dataset TEXT, artifact_path TEXT, run_id TEXT, artifact_id TEXT, module_version TEXT,
          limitation TEXT, created_at TEXT
        );
        INSERT INTO evidence_item
        (evidence_id, project_id, entity_symbol, evidence_type, source_dataset, artifact_path, run_id, artifact_id, module_version, review_status, created_at)
        VALUES ('ev1', 'demo', 'CXCL8', 'qtl_colocalization', 'genetic_demo', 'results\\genetic_coloc_mr\\genetic_evidence.tsv', 'run_1', 'artifact_1', 'genetic_coloc_mr_v1', 'PENDING', 'now');
        """
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    unittest.main()
