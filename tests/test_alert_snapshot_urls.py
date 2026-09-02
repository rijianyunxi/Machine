import tempfile
import unittest
from pathlib import Path

from webapp.snapshot_urls import snapshot_url


class AlertSnapshotUrlTests(unittest.TestCase):
    def test_absolute_snapshot_path_is_mapped_to_encoded_public_url(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "snapshots"
            image = base / "2026-09-02" / "未戴安全帽" / "CAM_4_R01_095845.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"jpeg")

            class State:
                def snapshots_dir(self):
                    return base

            self.assertEqual(
                snapshot_url(State(), str(image)),
                "/snapshots/2026-09-02/%E6%9C%AA%E6%88%B4%E5%AE%89%E5%85%A8%E5%B8%BD/CAM_4_R01_095845.jpg",
            )

    def test_snapshot_outside_configured_root_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "snapshots"
            image = root / "outside.jpg"
            base.mkdir()
            image.write_bytes(b"jpeg")

            class State:
                def snapshots_dir(self):
                    return base

            self.assertIsNone(snapshot_url(State(), str(image)))


if __name__ == "__main__":
    unittest.main()
