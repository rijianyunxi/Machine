"""
YOLOv8 detection engine - multi-model registry.

Loads multiple YOLOv8 models (PPE, smoking, user-imported...) from
the committed machine.db model settings and merges their results into Detection objects.

Supports runtime management from the web panel:
  - per-model threshold updates (applied on the next inference)
  - load / unload / reload in a background thread
  - model routing: detect_all(frame, model_names=...) runs only the
    models required by the camera's active rules
"""

import os
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from infrastructure.storage_paths import resolve_model_path
from utils.logger import get_logger


@dataclass
class Detection:
    """A single object detection result."""

    class_id: int
    class_name: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2) in pixel coordinates
    model_name: str = "default"  # which model produced this detection


class DetectionError(RuntimeError):
    """Raised when a configured detector cannot produce a valid result.

    An inference failure is deliberately distinct from a successful inference
    with zero detections.  Callers can catch this exception to mark the model
    unhealthy instead of feeding an artificial empty result into absence rules.
    """

    def __init__(self, model_name: str, message: str, cause: Optional[BaseException] = None):
        self.model_name = model_name
        self.cause = cause
        super().__init__(f"Detection failed for model [{model_name}]: {message}")


def _resolve_model_path(path: str) -> str:
    """Resolve canonical and legacy model references independent of CWD."""
    return str(resolve_model_path(path))


class Detector:
    """Single YOLOv8 model wrapper."""

    def __init__(
        self,
        name: str,
        model_path: str,
        device: str,
        confidence: float = 0.5,
        iou: float = 0.45,
        img_size: int = 640,
    ):
        self.name = name
        self.model_path = model_path
        self.device = device
        self.confidence = confidence
        self.iou = iou
        self.img_size = img_size
        self._model = None
        self._last_error: Optional[DetectionError] = None
        self._error_count = 0
        self._logger = get_logger(f"detector.{name}")
        self._load_model()

    def _load_model(self):
        """Load the YOLOv8 model."""
        try:
            from ultralytics import YOLO

            self._logger.info(f"Loading model [{self.name}]: {self.model_path}")
            self._model = YOLO(self.model_path)

            if hasattr(self._model, "names"):
                classes = list(self._model.names.values())
                self._logger.info(
                    f"Model [{self.name}] loaded: {len(classes)} classes: {classes[:15]}"
                )
        except Exception as e:
            self._logger.error(f"Failed to load model [{self.name}]: {e}")
            raise

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run detection on a frame.

        A returned empty list means inference completed successfully and no
        objects were found.  Model/runtime failures raise ``DetectionError``
        so they cannot be mistaken for a real empty frame.
        """
        if self._model is None:
            error = DetectionError(self.name, "model is not loaded")
            self._last_error = error
            self._error_count += 1
            raise error

        try:
            results = self._model(
                frame,
                conf=self.confidence,
                iou=self.iou,
                imgsz=self.img_size,
                device=self.device,
                verbose=False,
            )

            detections = []
            if results and len(results) > 0:
                result = results[0]
                if result.boxes is not None:
                    boxes = result.boxes
                    # Single device sync for all tensors, then plain iteration.
                    xyxy = boxes.xyxy.cpu().numpy()
                    confs = boxes.conf.cpu().numpy()
                    clss = boxes.cls.cpu().numpy().astype(int)
                    for i in range(len(xyxy)):
                        x1, y1, x2, y2 = xyxy[i]
                        class_name = result.names.get(clss[i], f"class_{clss[i]}")
                        detections.append(
                            Detection(
                                class_id=clss[i],
                                class_name=class_name,
                                confidence=float(confs[i]),
                                bbox=(int(x1), int(y1), int(x2), int(y2)),
                                model_name=self.name,
                            )
                        )
            self._last_error = None
            return detections

        except DetectionError:
            raise
        except Exception as e:
            error = DetectionError(self.name, str(e), cause=e)
            self._last_error = error
            self._error_count += 1
            self._logger.error(f"Detection error [{self.name}]: {e}", exc_info=True)
            raise error from e

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "path": self.model_path,
            "device": self.device,
            "confidence": self.confidence,
            "iou": self.iou,
            "img_size": self.img_size,
            "classes": dict(self._model.names) if self._model else {},
            "healthy": self._model is not None and self._last_error is None,
            "last_error": str(self._last_error) if self._last_error else None,
            "error_count": self._error_count,
        }

    @property
    def last_error(self) -> Optional[DetectionError]:
        """Most recent inference error, or ``None`` after a successful run."""
        return self._last_error

    @property
    def error_count(self) -> int:
        """Number of inference failures since this detector was created."""
        return self._error_count

    @property
    def class_names(self) -> dict:
        """Get model class names."""
        if self._model and hasattr(self._model, "names"):
            return self._model.names
        return {}


class MultiDetector:
    """Multi-model detection registry that merges results from enabled models."""

    def __init__(self, settings: dict, *, allow_empty: bool = False):
        self._detectors: Dict[str, Detector] = {}
        self._registry_lock = threading.Lock()
        self._load_errors: Dict[str, str] = {}
        self._logger = get_logger("multi_detector")

        model_cfg = settings.get("model", {})
        self._device = self._detect_device(model_cfg.get("device", "auto"))
        self._confidence = model_cfg.get("confidence_threshold", 0.5)
        self._iou = model_cfg.get("iou_threshold", 0.45)
        self._img_size = model_cfg.get("img_size", 640)
        self._allow_empty = bool(allow_empty)

        self._load_models(settings)

    def _detect_device(self, device_config: str) -> str:
        """Auto-detect GPU/CPU device."""
        if device_config != "auto":
            return device_config

        try:
            import torch

            if torch.cuda.is_available():
                device = "cuda:0"
                self._logger.info(f"GPU detected: {torch.cuda.get_device_name(0)}")
                return device
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self._logger.info("Apple MPS detected, using mps")
                return "mps"
        except ImportError:
            pass

        self._logger.info("No GPU detected, using CPU")
        return "cpu"

    def _load_models(self, settings: dict):
        """Load all configured models, failing fast if none can be loaded."""
        model_cfg = settings.get("model", {})
        models_list = model_cfg.get("models", [])

        if not models_list:
            message = "No models configured: register a model in machine.db"
            if self._allow_empty:
                self._logger.warning(
                    "%s; starting without detectors and waiting for panel configuration",
                    message,
                )
                return
            raise RuntimeError(message)

        for m in models_list:
            name = m.get("name", "unnamed")
            path = m.get("path", "")
            enabled = m.get("enabled", True)

            if not enabled:
                self._logger.info(f"Model [{name}] disabled, skipping")
                continue
            if not path:
                self._logger.warning(f"Model [{name}] has no path, skipping")
                continue
            path = _resolve_model_path(path)
            if not os.path.exists(path):
                self._logger.warning(
                    f"Model [{name}] file not found: {path}, skipping"
                )
                continue

            conf = m.get("confidence_override", self._confidence)
            try:
                detector = Detector(
                    name=name,
                    model_path=path,
                    device=self._device,
                    confidence=conf,
                    iou=self._iou,
                    img_size=self._img_size,
                )
                with self._registry_lock:
                    self._detectors[name] = detector
            except Exception as e:
                self._logger.error(f"Failed to load model [{name}]: {e}")
                self._load_errors[name] = str(e)

        if not self._detectors:
            message = "No detection models loaded - check model paths in machine.db"
            if self._allow_empty:
                self._logger.warning(
                    "%s; starting without detectors and waiting for a usable model",
                    message,
                )
                return
            raise RuntimeError(message)

        self._logger.info(
            f"Loaded {len(self._detectors)} model(s): {list(self._detectors.keys())}"
        )

    # ---------- runtime management (panel) ----------

    def is_loaded(self, name: str) -> bool:
        with self._registry_lock:
            return name in self._detectors

    def set_thresholds(
        self, name: str, confidence: Optional[float] = None,
        iou: Optional[float] = None, img_size: Optional[int] = None,
    ) -> bool:
        """Update inference thresholds - applied on the next call."""
        with self._registry_lock:
            det = self._detectors.get(name)
        if det is None:
            return False
        if confidence is not None:
            det.confidence = float(confidence)
        if iou is not None:
            det.iou = float(iou)
        if img_size is not None:
            det.img_size = int(img_size)
        return True

    def load_model(self, name: str, path: str, confidence: Optional[float] = None):
        """Load a model synchronously (call from a background thread)."""
        try:
            detector = Detector(
                name=name,
                model_path=_resolve_model_path(path),
                device=self._device,
                confidence=confidence if confidence is not None else self._confidence,
                iou=self._iou,
                img_size=self._img_size,
            )
            with self._registry_lock:
                self._detectors[name] = detector
                self._load_errors.pop(name, None)
            self._logger.info(f"Model [{name}] loaded from {path}")
            return True
        except Exception as e:
            self._logger.error(f"Failed to load model [{name}]: {e}")
            self._load_errors[name] = str(e)
            return False

    def unload_model(self, name: str) -> bool:
        with self._registry_lock:
            det = self._detectors.pop(name, None)
        if det is None:
            return False
        try:
            det._model = None
        except Exception:
            pass
        self._logger.info(f"Model [{name}] unloaded")
        return True

    # ---------- inference ----------

    def detect_all(
        self, frame: np.ndarray, model_names: Optional[List[str]] = None
    ) -> List[Detection]:
        """Run the given models (default: all loaded) on a frame and merge."""
        all_detections = []
        with self._registry_lock:
            if model_names is None:
                detectors = list(self._detectors.values())
            else:
                requested = list(dict.fromkeys(model_names))
                missing = [name for name in requested if name not in self._detectors]
                if missing:
                    message = (
                        "requested model(s) are not loaded: "
                        f"{', '.join(missing)}"
                    )
                    error = DetectionError(",".join(missing), message)
                    self._logger.error(message)
                    raise error
                detectors = [self._detectors[name] for name in requested]

        for detector in detectors:
            try:
                all_detections.extend(detector.detect(frame))
            except DetectionError:
                # Do not continue with the other models: a partial result can
                # make absence/PPE rules semantically unsafe for this frame.
                raise
            except Exception as e:
                # Keep the aggregate API explicit even if a custom detector
                # implementation violates the Detector contract.
                name = getattr(detector, "name", "unknown")
                error = DetectionError(name, str(e), cause=e)
                self._logger.error(f"Detection error [{name}]: {e}", exc_info=True)
                raise error from e
        return all_detections

    def models_providing(self, class_names) -> List[str]:
        """Names of loaded models whose class table contains any of class_names."""
        wanted = {c.lower() for c in class_names}
        with self._registry_lock:
            return [
                name for name, det in self._detectors.items()
                if any(str(v).lower() in wanted for v in det.class_names.values())
            ]

    def get_status(self) -> List[dict]:
        with self._registry_lock:
            return [d.get_status() for d in self._detectors.values()]

    @property
    def loaded_models(self) -> List[str]:
        """Get names of all loaded models."""
        with self._registry_lock:
            return list(self._detectors.keys())

    @property
    def device(self) -> str:
        return self._device
