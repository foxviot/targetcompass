import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.access_admin import build_access_admin_dashboard
from targetcompass_lite.canonical.access_control import issue_access_token, set_project_member


class CanonicalAccessAdminTest(unittest.TestCase):
    def test_access_dashboard_summarizes_members_tokens_permissions_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            set_project_member(project, "reviewer1", "reviewer")
            token = issue_access_token(project, "reviewer1", ttl_minutes=10, scopes=["project:read"])

            dashboard = build_access_admin_dashboard(project)

            self.assertEqual(dashboard["schema_version"], "v5.access_admin_dashboard/0.1")
            self.assertGreaterEqual(dashboard["summary"]["active_member_count"], 1)
            self.assertEqual(dashboard["summary"]["active_token_count"], 1)
            self.assertTrue(any(row["user_id"] == "reviewer1" for row in dashboard["members"]))
            self.assertTrue(any(row["token_id"] == token["token_id"] for row in dashboard["tokens"]))
            self.assertTrue(any(row["role"] == "reviewer" for row in dashboard["permission_matrix"]))
            self.assertTrue(any(row["role"] == "reviewer" and row["covered"] for row in dashboard["role_coverage"]))
            self.assertEqual(dashboard["token_lifecycle_summary"]["by_lifecycle_status"]["active"], 1)
            self.assertTrue(any(row["capability"] == "token_lifecycle" for row in dashboard["admin_capabilities"]))
            self.assertIn("productization_gaps", dashboard)
            self.assertTrue((project / "v5" / "access" / "access_admin_dashboard.json").exists())


if __name__ == "__main__":
    unittest.main()
