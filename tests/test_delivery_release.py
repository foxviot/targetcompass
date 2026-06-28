import tempfile
import unittest
from pathlib import Path

from targetcompass_lite.delivery_release import freeze_v5_development_delivery


class DeliveryReleaseTests(unittest.TestCase):
    def test_freeze_delivery_writes_manifest_with_p0_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            result = freeze_v5_development_delivery(project, release_label="test-release")

            self.assertEqual(result["release_label"], "test-release")
            self.assertIn("p0_status", result)
            self.assertIn("delivery_hash", result)
            self.assertTrue((project / "v5" / "delivery" / "v5_development_delivery_freeze.json").exists())
            self.assertIn("professor_demo_bundle", result["recommended_delivery_files"])


if __name__ == "__main__":
    unittest.main()
