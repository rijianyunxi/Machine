"""
Snapshot capture and storage module.

When a violation is detected, saves the current frame as a JPEG image
with bounding boxes and violation labels annotated on it.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from core.analyzer import Violation
from core.detector import Detection
from utils.logger import get_logger


# Color palette for different severity levels (BGR format)
SEVERITY_COLORS = {
    1: (0, 255, 0),      # Green - low
    2: (0, 165, 255),    # Orange - medium
    3: (0, 0, 255),      # Red - high
    4: (0, 0, 200),      # Dark red - critical
}

# Rule name display labels (Chinese + English)
RULE_LABELS = {
    1: ("No Safety Helmet", "未戴安全帽"),
    13: ("Smoking in No-fire Zone", "禁火区吸烟"),
    14: ("Person Holding Cigarette", "持烟未吸/禁火区持烟"),
}


class SnapshotManager:
    """Manages snapshot capture and storage for detected violations."""

    def __init__(self, settings: dict):
        self._logger = get_logger("snapshot")

        snap_cfg = settings.get("snapshot", {})
        self._save_dir = snap_cfg.get("save_dir", "storage/snapshots")
        self._save_root = Path(self._save_dir).expanduser().resolve()
        self._jpeg_quality = snap_cfg.get("jpeg_quality", 90)
        self._annotate = snap_cfg.get("annotate", True)
        self._box_thickness = snap_cfg.get("box_thickness", 2)
        self._font_scale = snap_cfg.get("font_scale", 0.6)

        # Ensure base save directory exists.  All later paths are resolved
        # against this root and checked before a directory or file is created.
        self._save_root.mkdir(parents=True, exist_ok=True)

    def apply_settings(self, settings: dict) -> None:
        """Apply the committed snapshot settings to this live manager.

        The main loop may learn about a settings change through the periodic
        ConfigManager refresh (for example when a standalone panel process
        writes machine.db).  Keep the writer in sync with that snapshot so a
        long-running process cannot keep using an old annotate flag.
        """
        snap_cfg = settings or {}
        if "annotate" in snap_cfg:
            self._annotate = bool(snap_cfg["annotate"])
        if "jpeg_quality" in snap_cfg:
            self._jpeg_quality = int(snap_cfg["jpeg_quality"])
        if "box_thickness" in snap_cfg:
            self._box_thickness = int(snap_cfg["box_thickness"])
        if "font_scale" in snap_cfg:
            self._font_scale = float(snap_cfg["font_scale"])
        if "save_dir" in snap_cfg and snap_cfg["save_dir"]:
            self._save_dir = str(snap_cfg["save_dir"])
            self._save_root = Path(self._save_dir).expanduser().resolve()
            self._save_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_component(value: object, fallback: str) -> str:
        """Return a single filesystem-safe path component.

        Rule names and camera IDs are configuration/runtime data, not trusted
        path components.  Replacing separators (including Windows separators)
        also prevents values such as ``../../outside`` from changing the
        snapshot root.
        """
        component = str(value or "").strip()
        component = component.replace("/", "_").replace("\\", "_")
        component = re.sub(r"[\x00-\x1f\x7f]", "_", component)
        component = re.sub(r"[^\w .-]", "_", component, flags=re.UNICODE)
        component = component.strip(" .")
        return component if component and component not in {".", ".."} else fallback

    def _assert_in_save_dir(self, path: Path) -> None:
        """Reject paths that resolve outside the configured save directory."""
        try:
            path.resolve().relative_to(self._save_root)
        except ValueError as exc:
            raise ValueError(
                f"snapshot path escapes save_dir: {path}"
            ) from exc

    def save_snapshot(
        self,
        frame: np.ndarray,
        violation: Violation,
        detections: Optional[List[Detection]] = None,
    ) -> Optional[str]:
        """
        Save a violation snapshot to disk.

        Args:
            frame: Current video frame (BGR).
            violation: The detected violation.
            detections: Optional list of all detections to draw on frame.

        Returns:
            File path of saved snapshot, or None on error.
        """
        try:
            # Create date-based subdirectory.  Use the resolved root so the
            # containment check has one stable reference even when save_dir is
            # configured as a relative path.
            date_str = datetime.fromtimestamp(violation.timestamp).strftime("%Y-%m-%d")
            day_dir = self._save_root / date_str

            # Rule names are display data and must never be used as raw paths.
            safe_rule_name = self._safe_component(violation.rule_name, "rule")
            rule_dir = day_dir / safe_rule_name

            # Camera IDs become part of a filename, so sanitize them as well.
            safe_camera_id = self._safe_component(violation.camera_id, "camera")
            time_str = datetime.fromtimestamp(violation.timestamp).strftime(
                "%H%M%S_%f"
            )[:12]
            filename = f"{safe_camera_id}_R{violation.rule_id:02d}_{time_str}.jpg"
            filepath = rule_dir / filename

            # Resolve before mkdir/imwrite.  This also rejects pre-existing
            # symlinks under save_dir that point outside the configured root.
            self._assert_in_save_dir(rule_dir)
            self._assert_in_save_dir(filepath)
            rule_dir.mkdir(parents=True, exist_ok=True)

            # Prepare annotated frame
            output_frame = frame.copy()

            if self._annotate:
                output_frame = self._annotate_frame(
                    output_frame, violation, detections
                )

            # Save as JPEG
            quality = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
            success = cv2.imwrite(str(filepath), output_frame, quality)

            if success:
                self._logger.info(
                    f"Snapshot saved: {filepath} "
                    f"[{violation.rule_name}, conf={violation.confidence:.2f}]"
                )
                return str(filepath)
            else:
                self._logger.error(f"Failed to save snapshot: {filepath}")
                return None

        except Exception as e:
            self._logger.error(f"Snapshot error: {e}")
            return None

    def _annotate_frame(
        self,
        frame: np.ndarray,
        violation: Violation,
        detections: Optional[List[Detection]] = None,
    ) -> np.ndarray:
        """Draw annotations on the frame: bounding boxes, labels, violation info."""
        color = SEVERITY_COLORS.get(violation.severity, (0, 0, 255))
        thickness = self._box_thickness
        font_scale = self._font_scale

        # Draw all person detection boxes (light gray)
        if detections:
            for det in detections:
                if det.class_name.lower() == "person":
                    x1, y1, x2, y2 = det.bbox
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (200, 200, 200), 1)
                    label = f"{det.class_name} {det.confidence:.1f}"
                    cv2.putText(
                        frame,
                        label,
                        (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale * 0.7,
                        (200, 200, 200),
                        1,
                    )

        # Draw violation bounding box (colored, thicker)
        if violation.bbox:
            x1, y1, x2, y2 = violation.bbox
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        # Draw violation banner at top of frame
        rule_info = RULE_LABELS.get(violation.rule_id, (violation.rule_name, ""))
        banner_text = f"[R{violation.rule_id:02d}] {rule_info[0]}"
        if rule_info[1]:
            banner_text += f" | {rule_info[1]}"

        # Background rectangle for banner
        (text_w, text_h), _ = cv2.getTextSize(
            banner_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        cv2.rectangle(frame, (0, 0), (text_w + 20, text_h + 20), color, -1)
        cv2.putText(
            frame,
            banner_text,
            (10, text_h + 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            thickness,
        )

        # Draw timestamp and camera ID at bottom
        time_str = datetime.fromtimestamp(violation.timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        footer = f"{violation.camera_id} | {time_str} | conf={violation.confidence:.2f}"
        (fw, fh), _ = cv2.getTextSize(
            footer, cv2.FONT_HERSHEY_SIMPLEX, font_scale * 0.8, 1
        )
        h = frame.shape[0]
        cv2.rectangle(frame, (0, h - fh - 15), (fw + 20, h), (0, 0, 0), -1)
        cv2.putText(
            frame,
            footer,
            (10, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale * 0.8,
            (255, 255, 255),
            1,
        )

        return frame
