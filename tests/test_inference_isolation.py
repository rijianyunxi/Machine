import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core.detector import Detector, MultiDetector
from webapp.detect_service import DetectTestService


class _Model:
    def __init__(self):
        self.calls = []

    def __call__(self, frame, **kwargs):
        self.calls.append(kwargs)
        return []


class _FakeDetector:
    def __init__(self):
        self.name = "ppe"
        self.calls = []

    def detect(self, frame, **kwargs):
        self.calls.append(kwargs)
        return []


class _PanelDetector:
    loaded_models = ["ppe"]

    def __init__(self):
        self.calls = []
        self.threshold_updates = []

    def detect_all(self, frame, model_names=None, **kwargs):
        self.calls.append((model_names, kwargs))
        return []

    def set_thresholds(self, *args, **kwargs):
        self.threshold_updates.append((args, kwargs))


class _State:
    def __init__(self, detector):
        self.detector = detector

    def _get_standalone_detector(self):
        return self.detector


class InferenceIsolationTests(unittest.TestCase):
    def test_detector_uses_request_local_thresholds(self):
        model = _Model()
        detector = Detector.__new__(Detector)
        detector.name = "ppe"
        detector.confidence = 0.5
        detector.iou = 0.45
        detector.img_size = 640
        detector.device = "cpu"
        detector._model = model
        import threading
        detector._inference_lock = threading.Lock()
        detector._last_error = None
        detector._error_count = 0
        detector._logger = type("Log", (), {"error": lambda *args, **kwargs: None})()

        detector.detect(np.zeros((2, 2, 3), dtype=np.uint8), confidence=0.1, iou=0.2)

        self.assertEqual(detector.confidence, 0.5)
        self.assertEqual(detector.iou, 0.45)
        self.assertEqual(model.calls, [{"conf": 0.1, "iou": 0.2, "imgsz": 640,
                                        "device": "cpu", "verbose": False}])

    def test_multi_detector_forwards_local_thresholds(self):
        fake = _FakeDetector()
        registry = MultiDetector.__new__(MultiDetector)
        import threading
        registry._registry_lock = threading.Lock()
        registry._detectors = {"ppe": fake}
        registry._logger = type("Log", (), {"error": lambda *args, **kwargs: None})()

        registry.detect_all(object(), confidence=0.2, iou=0.3, img_size=512)

        self.assertEqual(fake.calls, [{"confidence": 0.2, "iou": 0.3, "img_size": 512}])

    def test_detection_test_bench_does_not_mutate_registry_thresholds(self):
        detector = _PanelDetector()
        with tempfile.TemporaryDirectory() as td, patch(
            "webapp.detect_service.RESULTS_DIR", Path(td)
        ):
            service = DetectTestService(_State(detector))
            service.run(np.zeros((8, 8, 3), dtype=np.uint8), conf=0.2, iou=0.3)

        self.assertEqual(detector.threshold_updates, [])
        self.assertEqual(detector.calls, [(None, {"confidence": 0.2, "iou": 0.3})])


if __name__ == "__main__":
    unittest.main()
