import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp.state import RuntimeState


class LogsTests(unittest.TestCase):
    def _state(self, path: Path):
        state = RuntimeState.__new__(RuntimeState)
        settings = {"logging": {"file": str(path)}}
        with patch.object(RuntimeState, "settings", return_value=settings):
            return state

    def test_tail_logs_filters_exact_level_with_warn_alias(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "app.log"
            path.write_text(
                "2026-09-05 12:00:00 [INFO] [machine_vision] ready\n"
                "2026-09-05 12:00:01 [WARNING] [camera] reconnect\n"
                "2026-09-05 12:00:02 [ERROR] [detector] timeout\n"
                "2026-09-05 12:00:03 no-level line\n",
                encoding="utf-8",
            )
            state = self._state(path)
            with patch.object(RuntimeState, "settings", return_value={"logging": {"file": str(path)}}):
                self.assertEqual(state.tail_logs(level="WARNING"), [path.read_text().splitlines()[1]])
                self.assertEqual(state.tail_logs(level="WARN"), [path.read_text().splitlines()[1]])
                self.assertEqual(state.tail_logs(level="ERROR"), [path.read_text().splitlines()[2]])

    def test_clear_log_truncates_and_keeps_backups_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "app.log"
            backup = Path(td) / "app.log.1"
            unrelated = Path(td) / "app.old"
            path.write_text("data\n", encoding="utf-8")
            backup.write_text("old\n", encoding="utf-8")
            unrelated.write_text("keep\n", encoding="utf-8")
            state = self._state(path)
            with patch.object(RuntimeState, "settings", return_value={"logging": {"file": str(path)}}):
                result = state.clear_logs()
            self.assertEqual(result, {"ok": True, "file": "app.log", "removed_backups": 0})
            self.assertEqual(path.read_text(), "")
            self.assertTrue(backup.exists())
            self.assertTrue(unrelated.exists())

    def test_clear_log_can_remove_numbered_backups(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "app.log"
            backup = Path(td) / "app.log.1"
            unrelated = Path(td) / "app.old"
            path.write_text("data\n", encoding="utf-8")
            backup.write_text("old\n", encoding="utf-8")
            unrelated.write_text("keep\n", encoding="utf-8")
            state = self._state(path)
            with patch.object(RuntimeState, "settings", return_value={"logging": {"file": str(path)}}):
                result = state.clear_logs(include_backups=True)
            self.assertEqual(result["removed_backups"], 1)
            self.assertFalse(backup.exists())
            self.assertTrue(unrelated.exists())

    def test_clear_log_rejects_directory(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "logdir"
            path.mkdir()
            state = self._state(path)
            with patch.object(RuntimeState, "settings", return_value={"logging": {"file": str(path)}}):
                with self.assertRaises(ValueError):
                    state.clear_logs()


if __name__ == "__main__":
    unittest.main()
