"""
Detection test bench: run uploaded images through the live model registry.

- Single-flight lock: only one test inference at a time (CPU courtesy toward
  the main detection loop).
- Results (annotated JPEG + JSON) are kept for the most recent N runs;
  older files are pruned on each write.
"""

import itertools
import json
import threading
import time
from collections import deque
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "storage" / "test_results"
KEEP_RESULTS = 50


class DetectTestService:
    def __init__(self, state):
        self.state = state
        self._lock = threading.Lock()  # single-flight
        self._results = deque(maxlen=20)
        self._ids = itertools.count(1)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def run(self, image_bgr, model_names=None, conf=None, iou=None) -> dict:
        detector = self.state._get_standalone_detector()
        if detector is None or not detector.loaded_models:
            raise RuntimeError("没有可用的检测模型")

        with self._lock:  # one test inference at a time
            overrides = []
            try:
                for name in (model_names or detector.loaded_models):
                    if conf is not None:
                        detector.set_thresholds(name, confidence=conf)
                        overrides.append(name)
                start = time.time()
                detections = detector.detect_all(image_bgr, model_names=model_names)
                latency_ms = round((time.time() - start) * 1000)
            finally:
                # restore configured thresholds
                cfg = {m["name"]: m for m in
                       self.state.settings().get("model", {}).get("models", [])}
                for name in overrides:
                    conf_val = cfg.get(name, {}).get("confidence_override")
                    detector.set_thresholds(name, confidence=conf_val)

        result_id = next(self._ids)
        annotated_path = RESULTS_DIR / f"test_{result_id}.jpg"
        annotated = _annotate(image_bgr, detections)
        cv2.imwrite(str(annotated_path), annotated,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        self._prune(keep=KEEP_RESULTS)

        record = {
            "result_id": result_id,
            "latency_ms": latency_ms,
            "models": model_names or detector.loaded_models,
            "conf_override": conf,
            "detections": [
                {
                    "class_name": d.class_name,
                    "confidence": round(d.confidence, 3),
                    "bbox": list(d.bbox),
                    "model": d.model_name,
                }
                for d in detections
            ],
            "annotated_url": f"/api/detect/test/{result_id}/annotated.jpg",
            "time": time.strftime("%H:%M:%S"),
        }
        self._results.appendleft(record)
        return record

    def annotated_jpeg(self, result_id: int) -> bytes:
        path = RESULTS_DIR / f"test_{result_id}.jpg"
        return path.read_bytes()

    def recent(self) -> list:
        return list(self._results)

    def _prune(self, keep: int):
        files = sorted(RESULTS_DIR.glob("test_*.jpg"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)


def _annotate(frame, detections):
    out = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = d.bbox
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 80), 2)
        label = f"{d.class_name} {d.confidence:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x1, y1 - th - 8), (x1 + tw + 4, y1), (0, 200, 80), -1)
        cv2.putText(out, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return out


# keep json import referenced for future extra serialization
_ = json
