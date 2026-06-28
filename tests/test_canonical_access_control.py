import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.canonical.access_control import (
    access_readiness,
    authorize,
    create_user,
    initialize_access_control,
    issue_access_token,
    query_access_audit,
    revoke_access_token,
    set_project_member,
)


class CanonicalAccessControlTest(unittest.TestCase):
    def test_initialize_creates_owner_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            registry = initialize_access_control(project)

            self.assertEqual(registry["members"][0]["role"], "owner")
            self.assertTrue((project / "v5" / "access" / "access_registry.json").exists())
            self.assertGreaterEqual(query_access_audit(project)["match_count"], 1)

    def test_member_permissions_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            initialize_access_control(project)
            create_user(project, "reviewer1", actor="local_owner")
            set_project_member(project, "reviewer1", "reviewer", actor="local_owner")

            self.assertEqual(authorize(project, "reviewer1", "review:write")["status"], "allowed")
            with self.assertRaises(PermissionError):
                authorize(project, "reviewer1", "token:write")

    def test_token_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            initialize_access_control(project)
            create_user(project, "operator1", actor="local_owner")
            set_project_member(project, "operator1", "operator", actor="local_owner")

            token = issue_access_token(project, "operator1", actor="local_owner", ttl_minutes=10, scopes=["project:read"])
            self.assertEqual(token["project_id"], "demo")
            readiness = access_readiness(project)
            self.assertGreaterEqual(readiness["summary"]["active_token_count"], 1)

            registry = revoke_access_token(project, token["token_id"], actor="local_owner", reason="rotation")
            revoked = [row for row in registry["tokens"] if row["token_id"] == token["token_id"]][0]
            self.assertEqual(revoked["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
