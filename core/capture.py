"""
Video stream capture module.

Handles RTSP camera stream connections with:
- Multi-threaded capture (one thread per camera)
- Continuous draining for network streams (prevents socket buffer buildup
  which otherwise makes the camera/server drop the session and causes
  recurring decode errors + reconnect loops)
- Automatic reconnection on failure
- Configurable frame sampling (FPS reduction) for local file sources
- Thread-safe frame access
"""

import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional

import cv2
import numpy as np

from utils.logger import get_logger

# Sources that need continuous draining (network backpressure can stall them).
_NETWORK_PREFIXES = (
    "rtsp://", "rtsps://", "rtmp://", "http://", "https://", "udp://", "tcp://",
)


@contextmanager
def _suppress_ffmpeg_stderr():
    """Temporarily redirect OS-level stderr (fd 2) to devnull.

    ffmpeg's libav* writes directly to the C stderr stream, which bypasses
    Python's sys.stderr. ``io.StringIO()`` has no ``fileno()``, so
    ``contextlib.redirect_stderr`` only swaps the Python object and the
    h264/RTSP noise still reaches the console. Here we ``dup2`` the real
    file descriptor, so C-level messages are discarded too.
    """
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield
        return

    saved_fd = os.dup(fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, fd)
        yield
    finally:
        os.dup2(saved_fd, fd)
        os.close(devnull_fd)
        os.close(saved_fd)


@dataclass
class CameraConfig:
    """Configuration for a single camera channel."""

    id: str
    name: str
    rtsp_url: str
    enabled: bool = True
    rules: list = field(default_factory=list)


@dataclass
class FrameData:
    """A captured frame with metadata."""

    camera_id: str
    frame: np.ndarray
    timestamp: float
    frame_index: int


class CameraStream:
    """
    Manages a single RTSP camera stream with auto-reconnect and frame sampling.
    """

    def __init__(
        self,
        config: CameraConfig,
        target_fps: float = 2.0,
        reconnect_delay: float = 5.0,
        max_failures: int = 30,
        read_timeout: float = 10.0,
        warmup_frames: int = 5,
        stall_timeout: float = 15.0,
    ):
        self.config = config
        self.target_fps = target_fps
        self.reconnect_delay = reconnect_delay
        self.max_failures = max_failures
        self.read_timeout = read_timeout
        self.warmup_frames = warmup_frames
        self.stall_timeout = stall_timeout

        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frame_index: int = 0
        self._frames_captured: int = 0

        self._consecutive_failures = 0
        self._is_connected = False
        self._source_fps = 25.0  # default assumption
        self._last_success_time = 0.0

        # Network streams must be drained continuously; local files are sampled.
        url_lower = (config.rtsp_url or "").lower()
        self._is_network = url_lower.startswith(_NETWORK_PREFIXES)

        self._logger = get_logger(f"camera.{config.id}")

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def camera_id(self) -> str:
        return self.config.id

    def get_panel_status(self) -> dict:
        """Snapshot of runtime state for the web panel."""
        with self._lock:
            frame_age = (
                time.time() - self._latest_timestamp if self._latest_timestamp else None
            )
            frames = self._frames_captured
        return {
            "id": self.config.id,
            "name": self.config.name,
            "url": self.config.rtsp_url,
            "rules": self.config.rules,
            "connected": self._is_connected,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "failures": self._consecutive_failures,
            "frames_captured": frames,
            "frame_age": round(frame_age, 1) if frame_age is not None else None,
            "source_fps": self._source_fps,
            "is_network": self._is_network,
        }

    def start(self):
        """Start the capture thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"cam-{self.config.id}",
            daemon=True,
        )
        self._thread.start()
        self._logger.info(
            f"Capture thread started: {self.config.name} ({self.config.rtsp_url})"
        )

    def stop(self):
        """Stop the capture thread and release resources."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._release_capture()
        self._logger.info(f"Capture thread stopped: {self.config.id}")

    def get_frame(self) -> Optional[FrameData]:
        """
        Get the latest captured frame (thread-safe).

        Returns:
            FrameData with the latest frame, or None if no frame available.
        """
        with self._lock:
            if self._latest_frame is None:
                return None
            return FrameData(
                camera_id=self.config.id,
                frame=self._latest_frame.copy(),
                timestamp=self._latest_timestamp,
                frame_index=self._frame_index,
            )

    def _connect(self) -> bool:
        """Attempt to connect to the RTSP stream."""
        self._release_capture()

        try:
            self._logger.info(f"Connecting to {self.config.rtsp_url}...")
            with _suppress_ffmpeg_stderr():
                self._cap = cv2.VideoCapture(self.config.rtsp_url, cv2.CAP_FFMPEG)

                # Keep only the latest frame instead of buffering the whole GOP.
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # Apply connect/read timeouts so a stalled stream cannot block forever.
                self._cap.set(
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.read_timeout * 1000)
                )
                self._cap.set(
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout * 1000)
                )

            if self._cap.isOpened():
                self._source_fps = self._cap.get(cv2.CAP_PROP_FPS)
                if self._source_fps <= 0:
                    self._source_fps = 25.0

                width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self._logger.info(
                    f"Connected: {width}x{height} @ {self._source_fps:.1f} FPS"
                )
                self._is_connected = True
                self._consecutive_failures = 0
                self._last_success_time = time.time()
                return True
            else:
                self._logger.warning("Failed to open stream")
                self._is_connected = False
                return False

        except Exception as e:
            self._logger.error(f"Connection error: {e}")
            self._is_connected = False
            return False

    def _release_capture(self):
        """Release the underlying VideoCapture object."""
        self._is_connected = False
        # Drop the cached frame: during reconnection the consumer must not keep
        # detecting on a frozen stale image (repeat alerts, inflated stats).
        with self._lock:
            self._latest_frame = None
            self._latest_timestamp = 0.0
        cap = self._cap
        self._cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _capture_loop(self):
        """Capture loop.

        For network streams (RTSP/HTTP) the loop reads frames continuously and
        only keeps the newest one. This keeps the socket/decoder buffer drained,
        so the camera never sees backpressure and the H.264 stream stays in sync.
        The consumer side (main loop) already throttles processing to target_fps.

        For local video files it keeps the old behavior: read one frame per
        sample interval (target_fps).
        """
        sample_interval = 1.0 / self.target_fps if self.target_fps > 0 else 0.5
        last_sample_time = 0.0
        frames_since_connect = 0

        while self._running:
            # Reconnect if needed
            if self._cap is None or not self._cap.isOpened():
                self._logger.info("Reconnecting...")
                if not self._connect():
                    time.sleep(self.reconnect_delay)
                    continue
                frames_since_connect = 0

            # Local files: only read when it is time to sample.
            if not self._is_network:
                now = time.time()
                if now - last_sample_time < sample_interval:
                    time.sleep(0.01)  # small sleep to avoid busy waiting
                    continue

            # Read a frame (ffmpeg decode warnings go to stderr; suppress them)
            try:
                read_start = time.time()
                with _suppress_ffmpeg_stderr():
                    success, frame = self._cap.read()
                read_elapsed = time.time() - read_start
            except Exception as e:
                self._logger.error(f"Frame read error: {e}")
                success, frame = False, None
                read_elapsed = 0.0

            now = time.time()

            if success and frame is not None:
                self._consecutive_failures = 0
                self._is_connected = True
                self._last_success_time = now
                last_sample_time = now

                # After (re)connect the decoder may start mid-GOP on a P-frame;
                # discard the first few frames until it syncs to a keyframe.
                frames_since_connect += 1
                if frames_since_connect <= self.warmup_frames:
                    continue

                with self._lock:
                    self._latest_frame = frame
                    self._latest_timestamp = now
                    self._frame_index += 1
                    self._frames_captured += 1

            else:
                self._consecutive_failures += 1

                # A single read that burned the whole timeout means the socket is
                # dead; tear down immediately instead of waiting for 30 failures.
                if read_elapsed >= self.read_timeout - 0.5:
                    self._logger.warning(
                        f"Read timed out after {read_elapsed:.1f}s, reconnecting..."
                    )
                    self._release_capture()
                    time.sleep(self.reconnect_delay)
                    continue

                if self._consecutive_failures >= self.max_failures:
                    self._logger.warning(
                        f"Too many consecutive failures ({self._consecutive_failures}), "
                        f"reconnecting..."
                    )
                    self._release_capture()
                    time.sleep(self.reconnect_delay)
                else:
                    time.sleep(0.02)

            # Stall watchdog: safety net in case a stream stops producing frames
            # without returning read errors (e.g. read timeout not enforced).
            if (
                self._is_network
                and self._last_success_time > 0
                and (time.time() - self._last_success_time) > self.stall_timeout
            ):
                self._logger.warning(
                    f"Stream stalled "
                    f"({time.time() - self._last_success_time:.0f}s without a frame), "
                    f"reconnecting..."
                )
                self._release_capture()
                time.sleep(self.reconnect_delay)


class CameraManager:
    """
    Manages multiple camera streams.
    """

    def __init__(self, settings: dict):
        self._cameras: Dict[str, CameraStream] = {}
        self._settings = settings
        self._logger = get_logger("camera_manager")

        capture_cfg = settings.get("capture", {})
        self._target_fps = capture_cfg.get("target_fps", 2.0)
        self._reconnect_delay = capture_cfg.get("reconnect_delay", 5.0)
        self._max_failures = capture_cfg.get("max_failures", 30)
        self._read_timeout = capture_cfg.get("read_timeout", 10.0)
        self._warmup_frames = capture_cfg.get("warmup_frames", 5)
        self._stall_timeout = capture_cfg.get("stall_timeout", 15.0)

    def add_camera(self, config: CameraConfig):
        """Add and start a camera stream."""
        if not config.enabled:
            self._logger.info(f"Camera {config.id} is disabled, skipping")
            return

        if config.id in self._cameras:
            self._logger.warning(f"Camera {config.id} already exists, replacing")
            self.remove_camera(config.id)

        stream = CameraStream(
            config=config,
            target_fps=self._target_fps,
            reconnect_delay=self._reconnect_delay,
            max_failures=self._max_failures,
            read_timeout=self._read_timeout,
            warmup_frames=self._warmup_frames,
            stall_timeout=self._stall_timeout,
        )
        self._cameras[config.id] = stream
        stream.start()

    def remove_camera(self, camera_id: str):
        """Stop and remove a camera stream."""
        if camera_id in self._cameras:
            self._cameras[camera_id].stop()
            del self._cameras[camera_id]

    def get_frame(self, camera_id: str) -> Optional[FrameData]:
        """Get the latest frame from a specific camera."""
        camera = self._cameras.get(camera_id)
        if camera is None:
            return None
        return camera.get_frame()

    def get_status(self) -> Dict[str, dict]:
        """Get connection status of all cameras."""
        return {
            cid: {
                "name": cam.config.name,
                "connected": cam.is_connected,
                "url": cam.config.rtsp_url,
                "rules": cam.config.rules,
            }
            for cid, cam in self._cameras.items()
        }

    def start_all(self, camera_configs: list):
        """Start all cameras from config list."""
        for cfg in camera_configs:
            config = CameraConfig(
                id=cfg["id"],
                name=cfg["name"],
                rtsp_url=cfg["rtsp_url"],
                enabled=cfg.get("enabled", True),
                rules=cfg.get("rules", []),
            )
            self.add_camera(config)

        self._logger.info(f"Started {len(self._cameras)} camera streams")

    def stop_all(self):
        """Stop all camera streams."""
        self._logger.info("Stopping all camera streams...")
        for cam in self._cameras.values():
            cam.stop()
        self._cameras.clear()
        self._logger.info("All camera streams stopped")

