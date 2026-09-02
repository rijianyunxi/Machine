from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from webapp.api.alerts import snapshot_thumb


class SnapshotThumbSecurityTests(unittest.TestCase):
    def test_sibling_with_same_prefix_is_not_treated_as_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "snapshots"
            sibling = root / "snapshots_evil"
            base.mkdir()
            sibling.mkdir()
            image = sibling / "secret.jpg"
            image.write_bytes(b"not a real jpeg")

            class State:
                def snapshots_dir(self):
                    return base

            with patch("webapp.api.alerts.get_state", return_value=State()):
                with self.assertRaises(HTTPException) as ctx:
                    snapshot_thumb(None, p="../snapshots_evil/secret.jpg", w=420)
            self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
