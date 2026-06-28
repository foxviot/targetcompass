import json
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.store import (
    append_event,
    init_project_state,
    load_events,
    load_project_state,
    transition_state,
    validate_transition,
)


class CanonicalStateTest(unittest.TestCase):
    def test_init_project_state_writes_v5_only_state_and_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            state = init_project_state(project_dir, "Does sarcopenia muscle contain SASP-high cells?")
            self.assertEqual(state["current_stage"], "INTAKE")
            self.assertTrue((project_dir / "v5" / "project_state.json").exists())
            self.assertTrue((project_dir / "v5" / "events.jsonl").exists())
            self.assertFalse((project_dir / "project_state.json").exists())
            self.assertEqual(load_events(project_dir)[0]["event_type"], "PROJECT_STATE_INITIALIZED")

    def test_legal_transition_updates_state_and_appends_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            init_project_state(project_dir, "question")
            state = transition_state(project_dir, "QUESTION_RESOLVED", "QUESTION_RESOLVED", "question_normalizer", [], "Question normalized.")
            self.assertEqual(state["current_stage"], "QUESTION_RESOLVED")
            events = load_events(project_dir)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[-1]["previous_stage"], "INTAKE")
            self.assertEqual(events[-1]["next_stage"], "QUESTION_RESOLVED")

    def test_illegal_skip_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_transition("INTAKE", "REPORT_READY")

    def test_events_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            init_project_state(project_dir, "question")
            event_path = project_dir / "v5" / "events.jsonl"
            before = event_path.read_text(encoding="utf-8")
            append_event(
                project_dir,
                {
                    "event_id": "event_manual",
                    "project_id": "demo_project",
                    "event_type": "MANUAL_NOTE",
                    "actor": "tester",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "previous_stage": "INTAKE",
                    "next_stage": "INTAKE",
                    "object_refs": [],
                    "message": "manual note",
                    "payload_hash": "abc",
                },
            )
            after = event_path.read_text(encoding="utf-8")
            self.assertTrue(after.startswith(before))
            self.assertEqual(len(after.splitlines()), 2)

    def test_failed_is_terminal_without_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            init_project_state(project_dir, "question")
            transition_state(project_dir, "FAILED", "FAILED", "runner", [], "Failed for test.")
            with self.assertRaises(ValueError):
                transition_state(project_dir, "QUESTION_RESOLVED", "RESUME", "tester", [], "Resume without flag.")

    def test_multiple_reads_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo_project"
            init_project_state(project_dir, "question")
            first = load_project_state(project_dir)
            second = load_project_state(project_dir)
            self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
