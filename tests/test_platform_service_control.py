import socket
import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.platform_service_control import build_service_control_manifest, find_recoverable_port


class PlatformServiceControlTest(unittest.TestCase):
    def test_service_control_manifest_reports_commands_and_port_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = int(sock.getsockname()[1])
                sock.listen(1)

                manifest = build_service_control_manifest(project, preferred_port=port)

            self.assertEqual(manifest["schema_version"], "v5.service_control_manifest/0.1")
            self.assertTrue(manifest["port_conflict"])
            self.assertGreater(manifest["selected_port"], port)
            self.assertIn("start", manifest["commands"])
            self.assertIn("stop_windows", manifest["commands"])
            self.assertIn("restart_windows", manifest["commands"])
            self.assertEqual(manifest["installer_contract"]["default_demo_project"], "vascular_aging_demo")
            self.assertTrue((project / "v5" / "platform" / "service_control_manifest.json").exists())

    def test_find_recoverable_port_returns_preferred_when_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        self.assertEqual(find_recoverable_port("127.0.0.1", port), port)


if __name__ == "__main__":
    unittest.main()
