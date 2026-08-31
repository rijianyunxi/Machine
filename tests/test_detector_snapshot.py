"""Regression tests for detector failure semantics and snapshot path safety.

Run from the project root with::

    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/test_detector_snapshot.py
"""

import tempfile
import threading
from datetime import datetime
from pathlib import Path
import sys

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analyzer import Violation
from core.detector import DetectionError, Detector, MultiDetector
from core.snapshot import SnapshotManager


class RaisingModel:
    names = {}

    def __call__(self, *args, **kwargs):
        raise RuntimeError("GPU inference failed")


class EmptyModel:
    names = {}

    def __call__(self, *args, **kwargs):
        return []


def make_detector(model):
    """Build a Detector without importing/loading a real Ultralytics model."""
    detector = object.__new__(Detector)
    detector.name = "test-model"
    detector.model_path = "test.pt"
    detector.device = "cpu"
    detector.confidence = 0.5
    detector.iou = 0.45
    detector.img_size = 640
    detector._model = model
    detector._last_error = None
    detector._error_count = 0
    detector._logger = __import__("utils.logger", fromlist=["get_logger"]).get_logger(
        "test.detector"
    )
    return detector


def make_violation(rule_name, camera_id="cam-1"):
    return Violation(
        camera_id=camera_id,
        rule_id=1,
        rule_name=rule_name,
        description="test violation",
        confidence=0.9,
        severity=3,
        timestamp=0.0,
        bbox=None,
    )


def test_detector_error_is_not_empty_detection():
    detector = make_detector(RaisingModel())

    try:
        detector.detect(np.zeros((8, 8, 3), dtype=np.uint8))
    except DetectionError as exc:
        assert exc.model_name == "test-model"
        assert isinstance(exc.cause, RuntimeError)
        assert "GPU inference failed" in str(exc)
    else:
        raise AssertionError("inference failure must raise DetectionError")

    assert detector.last_error is not None
    assert detector.error_count == 1
    assert detector.get_status()["healthy"] is False

    # A later successful empty inference clears the transient error.  [] then
    # retains its original meaning: inference succeeded and found no objects.
    detector._model = EmptyModel()
    assert detector.detect(np.zeros((8, 8, 3), dtype=np.uint8)) == []
    assert detector.last_error is None
    assert detector.get_status()["healthy"] is True


def test_multi_detector_rejects_unloaded_requested_model():
    multi = object.__new__(MultiDetector)
    multi._detectors = {}
    multi._registry_lock = threading.Lock()
    multi._logger = __import__("utils.logger", fromlist=["get_logger"]).get_logger(
        "test.multi_detector"
    )

    try:
        multi.detect_all(np.zeros((8, 8, 3), dtype=np.uint8), ["missing-model"])
    except DetectionError as exc:
        assert exc.model_name == "missing-model"
        assert "not loaded" in str(exc)
    else:
        raise AssertionError("a requested unavailable model must be reported")


def test_snapshot_sanitizes_rule_and_camera_paths():
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp) / "snapshots"
        manager = SnapshotManager(
            {
                "snapshot": {
                    "save_dir": str(save_dir),
                    "annotate": False,
                }
            }
        )
        violation = make_violation("../../outside", camera_id="../camera")

        saved = manager.save_snapshot(
            np.zeros((16, 16, 3), dtype=np.uint8), violation
        )

        assert saved is not None
        saved_path = Path(saved)
        assert saved_path.exists()
        assert saved_path.resolve().is_relative_to(save_dir.resolve())
        assert saved_path.parent.name not in {"..", ".", "../../outside"}
        assert "/" not in saved_path.parent.name
        assert "\\" not in saved_path.parent.name
        assert saved_path.name.endswith(".jpg")
        assert "/" not in saved_path.name
        assert "\\" not in saved_path.name
        assert ".." not in saved_path.name
        assert not (Path(tmp) / "outside").exists()


def test_snapshot_rejects_symlinked_directory_outside_save_root():
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp) / "snapshots"
        outside = Path(tmp) / "outside"
        outside.mkdir()
        manager = SnapshotManager(
            {
                "snapshot": {
                    "save_dir": str(save_dir),
                    "annotate": False,
                }
            }
        )

        date_dir = save_dir / datetime.fromtimestamp(0).strftime("%Y-%m-%d")
        date_dir.mkdir()
        (date_dir / "Unsafe").symlink_to(outside, target_is_directory=True)

        saved = manager.save_snapshot(
            np.zeros((16, 16, 3), dtype=np.uint8), make_violation("Unsafe")
        )

        assert saved is None
        assert not list(outside.glob("*.jpg"))


if __name__ == "__main__":
    test_detector_error_is_not_empty_detection()
    test_multi_detector_rejects_unloaded_requested_model()
    test_snapshot_sanitizes_rule_and_camera_paths()
    test_snapshot_rejects_symlinked_directory_outside_save_root()
    print("detector/snapshot regression tests passed")
