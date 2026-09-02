from pathlib import Path
import tempfile
import unittest

from core.snapshot import SnapshotManager


class SnapshotSettingsTests(unittest.TestCase):
    def test_apply_settings_updates_live_annotation_flag(self):
        with tempfile.TemporaryDirectory() as td:
            manager = SnapshotManager({"snapshot": {"save_dir": td, "annotate": True}})
            self.assertTrue(manager._annotate)

            manager.apply_settings({"annotate": False})

            self.assertFalse(manager._annotate)

    def test_apply_settings_rebases_save_root(self):
        with tempfile.TemporaryDirectory() as td:
            first = Path(td) / "first"
            second = Path(td) / "second"
            manager = SnapshotManager({"snapshot": {"save_dir": str(first)}})

            manager.apply_settings({"save_dir": str(second)})

            self.assertEqual(manager._save_root, second.resolve())
            self.assertTrue(second.is_dir())


if __name__ == "__main__":
    unittest.main()
