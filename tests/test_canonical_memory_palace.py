import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.memory_palace import (
    build_agent_memory_context,
    build_memory_audit_dashboard,
    diff_memory_versions,
    install_pilotdeck_memory,
    list_memory_versions,
    load_memory_palace,
    run_memory_rollback_drill,
    run_memory_usage_scenarios,
    rollback_memory,
    update_memory_entry,
)


class CanonicalMemoryPalaceTest(unittest.TestCase):
    def test_install_pilotdeck_memory_writes_v5_only_context_not_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            payload = install_pilotdeck_memory(project, source_doc="PilotDeck_bioinfo_agent_architecture_summary.md", actor="unit")

            self.assertEqual(payload["schema_version"], "v5.memory_palace/0.1")
            self.assertEqual(payload["scope"], "pilotdeck_workspace_memory_not_scientific_evidence")
            self.assertIn("memory_vs_evidence_db", payload["memory"])
            self.assertIn("bioinfo_orchestrator", payload["memory"]["agent_topology"])
            self.assertIn("No unsupported dataset IDs.", payload["memory"]["schema_bound_rules"])
            self.assertTrue(payload["active_version_id"])
            self.assertTrue(payload["memory_hash"])
            self.assertFalse((project / "v4").exists())
            self.assertFalse((project / "evidence.sqlite").exists())

            loaded = load_memory_palace(project)
            self.assertEqual(loaded["memory_id"], payload["memory_id"])
            events = (project / "v5" / "memory_palace" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(events), 1)
            event = json.loads(events[0])
            self.assertEqual(event["event_type"], "memory_palace_installed")
            self.assertEqual(event["actor"], "unit")

    def test_reinstall_appends_event_and_keeps_stable_memory_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            first = install_pilotdeck_memory(project, actor="unit")
            second = install_pilotdeck_memory(project, actor="unit")

            self.assertEqual(first["memory_id"], second["memory_id"])
            events = (project / "v5" / "memory_palace" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(events), 2)

    def test_memory_update_agent_context_and_rollback_are_auditable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            first = install_pilotdeck_memory(project, actor="unit")
            updated = update_memory_entry(project, "user_preferences", {"language": "zh-CN"}, actor="unit", reason="test update")

            versions = list_memory_versions(project)
            self.assertGreaterEqual(len(versions), 2)
            self.assertNotEqual(first["memory_hash"], updated["memory_hash"])

            context = build_agent_memory_context(project, "question_normalizer")
            self.assertEqual(context["agent_id"], "question_normalizer")
            self.assertIn("Memory context is not scientific evidence.", context["rules"])

            rolled = rollback_memory(project, first["active_version_id"], actor="unit", reason="test rollback")
            self.assertEqual(rolled["memory_hash"], first["memory_hash"])

    def test_memory_diff_dashboard_and_rollback_drill_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            first = install_pilotdeck_memory(project, actor="unit")
            update_memory_entry(project, "user_preferences", {"language": "zh-CN"}, actor="unit", reason="test update")
            versions = list_memory_versions(project)

            diff = diff_memory_versions(project, first["active_version_id"], versions[-1]["version_id"], actor="unit")
            drill = run_memory_rollback_drill(project, actor="unit")
            dashboard = build_memory_audit_dashboard(project, actor="unit")

            self.assertEqual(diff["schema_version"], "v5.memory_diff/0.1")
            self.assertGreaterEqual(diff["change_count"], 1)
            self.assertEqual(drill["status"], "PASS")
            self.assertEqual(dashboard["schema_version"], "v5.memory_audit_dashboard/0.1")
            self.assertGreaterEqual(dashboard["version_count"], 2)
            self.assertTrue((project / "v5" / "memory_palace" / "last_diff.json").exists())
            self.assertTrue((project / "v5" / "memory_palace" / "rollback_drill.json").exists())
            self.assertTrue((project / "v5" / "memory_palace" / "memory_audit_dashboard.json").exists())

    def test_memory_usage_scenarios_write_contexts_diff_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            install_pilotdeck_memory(project, actor="unit")

            scenarios = run_memory_usage_scenarios(project, actor="unit")
            dashboard = build_memory_audit_dashboard(project, actor="unit")

            self.assertEqual(scenarios["schema_version"], "v5.memory_usage_scenarios/0.1")
            self.assertEqual(scenarios["status"], "PASS")
            self.assertTrue(scenarios["agent_context_refs"])
            self.assertTrue(scenarios["scientific_boundary_ok"])
            self.assertIn("usage_scenarios", dashboard)
            self.assertTrue((project / "v5" / "memory_palace" / "usage_scenarios.json").exists())


if __name__ == "__main__":
    unittest.main()
