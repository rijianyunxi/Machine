"""
Behavior analyzer - logic-primitive-driven rule evaluation.

Each rule's template (config/rule_templates.yaml) binds to a check LOGIC
primitive; the analyzer dispatches on that primitive, not the template name:
  presence         - trigger class detected (above min confidence) -> violation
  presence_near    - trigger object near/overlapping a person
  absence_required - person missing required PPE (or negative class hit)
  graph            - visual canvas node graph stored on the rule itself
                     (rule.graph, evaluated by core/rules_graph.py)

Rule params (class sets, margins, ratios) come from config/rules.yaml, so
panel edits take effect on the next frame. Class names are matched
case-insensitively.
"""

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.detector import Detection
from core.rules_graph import evaluate_graph
from rules.rules_engine import (
    LOGIC_ABSENCE_REQUIRED,
    LOGIC_GRAPH,
    LOGIC_PRESENCE,
    LOGIC_PRESENCE_NEAR,
    LOGIC_ZONE_INTRUSION,
    RuleDefinition,
    get_rules_store,
    get_template_store,
)
from utils.logger import get_logger


@dataclass
class Violation:
    """A detected rule violation."""

    camera_id: str
    rule_id: int
    rule_name: str
    description: str
    confidence: float
    severity: int
    timestamp: float
    # Bounding box of the violating person/object (if applicable)
    bbox: Optional[tuple] = None
    # Extra context info
    extra: Optional[dict] = None


def _lower_set(names) -> set:
    return {n.lower() for n in (names or [])}


def _make_violation(camera_id, rule, det, timestamp) -> Violation:
    return Violation(
        camera_id=camera_id,
        rule_id=rule.id,
        rule_name=rule.name,
        description=rule.description,
        confidence=det.confidence,
        severity=rule.severity,
        timestamp=timestamp,
        bbox=det.bbox,
    )


class PresenceCheck:
    """Logic: any trigger detection above min confidence is a violation."""

    def __call__(
        self, camera_id: str, rule: RuleDefinition, detections: List[Detection],
        timestamp: float, frame_size: Optional[tuple] = None,
    ) -> Optional[Violation]:
        p = rule.params or {}
        trigger_classes = _lower_set(p.get("trigger_classes", []))
        min_conf = float(p.get("min_confidence", 0.0))

        best = None
        for det in detections:
            if det.class_name.lower() not in trigger_classes:
                continue
            if det.confidence < min_conf:
                continue
            if best is None or det.confidence > best.confidence:
                best = det
        if best is None:
            return None
        return _make_violation(camera_id, rule, best, timestamp)


class PresenceNearCheck:
    """Logic: trigger object overlapping a person box (smoking, cigarette...)."""

    def __call__(
        self, camera_id: str, rule: RuleDefinition, detections: List[Detection],
        timestamp: float, frame_size: Optional[tuple] = None,
    ) -> Optional[Violation]:
        p = rule.params or {}
        trigger_classes = _lower_set(p.get("trigger_classes", []))
        person_classes = _lower_set(p.get("person_classes", ["person"]))
        margin = float(p.get("overlap_margin", 0.2))
        min_conf = float(p.get("min_confidence", 0.0))

        triggers = [
            d for d in detections
            if d.class_name.lower() in trigger_classes and d.confidence >= min_conf
        ]
        if not triggers:
            return None
        persons = [d for d in detections if d.class_name.lower() in person_classes]
        if not persons:
            return None

        # Only accept triggers that actually overlap a person box; this
        # ignores background false positives (objects on desks etc.).
        best = None
        for t in triggers:
            if not any(
                self._bboxes_overlap(t.bbox, pers.bbox, margin=margin)
                for pers in persons
            ):
                continue
            if best is None or t.confidence > best.confidence:
                best = t
        if best is None:
            return None

        return _make_violation(camera_id, rule, best, timestamp)

    @staticmethod
    def _bboxes_overlap(a: tuple, b: tuple, margin: float = 0.0) -> bool:
        """True if box a overlaps box b (b expanded by margin fraction of size)."""
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        mx = (bx2 - bx1) * margin
        my = (by2 - by1) * margin
        return ax1 < bx2 + mx and ax2 > bx1 - mx and ay1 < by2 + my and ay2 > by1 - my


class AbsenceRequiredCheck:
    """Logic: person without required PPE, or explicit negative class hit."""

    def __call__(
        self, camera_id: str, rule: RuleDefinition, detections: List[Detection],
        timestamp: float, frame_size: Optional[tuple] = None,
    ) -> Optional[Violation]:
        p = rule.params or {}
        person_classes = _lower_set(p.get("person_classes", ["person"]))
        required = _lower_set(p.get("required_classes", []))
        absence = _lower_set(p.get("absence_classes", []))
        coverage_ratio = float(p.get("coverage_ratio", 0.5))

        persons = [d for d in detections if d.class_name.lower() in person_classes]
        if not persons:
            return None

        # Definite violation: model detected an explicit absence class.
        for det in detections:
            if det.class_name.lower() in absence:
                return _make_violation(camera_id, rule, det, timestamp)

        # Otherwise any person not covered by a required item is a violation.
        items = [d for d in detections if d.class_name.lower() in required]
        for person in persons:
            if not self._bbox_covers_person(person, items, coverage_ratio):
                return _make_violation(camera_id, rule, person, timestamp)
        return None

    @staticmethod
    def _bbox_covers_person(
        person: Detection, item_boxes: List[Detection], ratio: float
    ) -> bool:
        """True if any item bbox overlaps the person bbox significantly
        (more than ``ratio`` of the item box inside the person box)."""
        if not item_boxes:
            return False
        px1, py1, px2, py2 = person.bbox
        for item in item_boxes:
            ox1, oy1, ox2, oy2 = item.bbox
            overlap_x1 = max(px1, ox1)
            overlap_y1 = max(py1, oy1)
            overlap_x2 = min(px2, ox2)
            overlap_y2 = min(py2, oy2)
            if overlap_x2 > overlap_x1 and overlap_y2 > overlap_y1:
                overlap_area = (overlap_x2 - overlap_x1) * (overlap_y2 - overlap_y1)
                item_area = max((ox2 - ox1) * (oy2 - oy1), 1)
                if overlap_area / item_area > ratio:
                    return True
        return False


# Dispatch table: template logic primitive -> check implementation.
class ZoneIntrusionCheck:
    """Logic: target class inside a drawn alert zone (normalized rects).

    Params: target_classes / zones [{x,y,w,h}] / dwell_seconds / min_confidence.
    Zones are frame-normalized rectangles (top-left x/y + w/h) — compared
    against the detection center after converting to pixels via frame_size.
    With dwell_seconds > 0 the target must stay inside continuously before
    the violation fires; dwell state is per (camera, rule, class)."""

    # (camera_id, rule_id, cls) -> first-seen timestamp of continuous presence.
    # This is intentionally class-level because detections have no tracker ID.
    _dwell_since: Dict[tuple, float] = {}

    def __call__(
        self, camera_id: str, rule: RuleDefinition, detections: List[Detection],
        timestamp: float, frame_size: Optional[tuple] = None,
    ) -> Optional[Violation]:
        p = rule.params or {}
        target_classes = _lower_set(p.get("target_classes", []))
        zones = [z for z in (p.get("zones") or []) if isinstance(z, dict)]
        min_conf = float(p.get("min_confidence", 0.0))
        dwell = max(0.0, float(p.get("dwell_seconds", 0) or 0))
        if not target_classes or not zones or not frame_size:
            return None
        fw, fh = float(frame_size[0]), float(frame_size[1])

        # Aggregate the complete frame before updating class-level state.  An
        # outside detection for a class must not reset that class's timer when
        # another detection of the same class is inside the zone.  Likewise,
        # a class with no qualifying detection in this frame must be reset so
        # it cannot resume an old timer after disappearing and reappearing.
        detections_by_class = {cls: [] for cls in target_classes}
        for det in detections:
            cls = det.class_name.lower()
            if cls in detections_by_class and det.confidence >= min_conf:
                detections_by_class[cls].append(det)

        # Also discard state for classes removed by a hot-reloaded rule.
        for key in list(self._dwell_since):
            if (key[0], key[1]) == (camera_id, rule.id) and key[2] not in target_classes:
                self._dwell_since.pop(key, None)

        best = None
        for cls, class_detections in detections_by_class.items():
            key = (camera_id, rule.id, cls)
            inside = [
                det for det in class_detections
                if self._in_zones(det.bbox, zones, fw, fh)
            ]
            if not inside:
                self._dwell_since.pop(key, None)
                continue

            since = self._dwell_since.setdefault(key, timestamp)
            if timestamp - since < dwell:
                continue

            candidate = max(inside, key=lambda det: det.confidence)
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        if best is None:
            return None
        return _make_violation(camera_id, rule, best, timestamp)

    @staticmethod
    def _in_zones(bbox: tuple, zones: list, fw: float, fh: float) -> bool:
        """True if the detection center point falls inside any zone rect."""
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        for z in zones:
            try:
                zx, zy = float(z["x"]) * fw, float(z["y"]) * fh
                zw, zh = float(z["w"]) * fw, float(z["h"]) * fh
            except (KeyError, TypeError, ValueError):
                continue
            if zx <= cx <= zx + zw and zy <= cy <= zy + zh:
                return True
        return False


class GraphCheck:
    """Logic: visual rule-canvas node graph stored on the rule (rule.graph).

    Evaluation is delegated to core/rules_graph.py; missing/empty/invalid
    (or cyclic) graphs never fire. See docs/RULE_GRAPH_DESIGN.md."""

    def __call__(
        self, camera_id: str, rule: RuleDefinition, detections: List[Detection],
        timestamp: float, frame_size: Optional[tuple] = None,
    ) -> Optional[Violation]:
        graph = getattr(rule, "graph", None)
        if not graph:
            return None  # graph 缺失/为空：不告警
        return evaluate_graph(
            graph, detections, frame_size, timestamp, rule.id,
            rule=rule, camera_id=camera_id,
        )


RULE_LOGICS = {
    LOGIC_PRESENCE: PresenceCheck(),
    LOGIC_PRESENCE_NEAR: PresenceNearCheck(),
    LOGIC_ABSENCE_REQUIRED: AbsenceRequiredCheck(),
    LOGIC_ZONE_INTRUSION: ZoneIntrusionCheck(),
    LOGIC_GRAPH: GraphCheck(),
}


class BehaviorAnalyzer:
    """Evaluates detections against the enabled rules for each camera."""

    def __init__(self, settings: dict, config_dir: str = "config"):
        self._logger = get_logger("analyzer")

        alert_cfg = settings.get("alert", {})
        self._cooldown = alert_cfg.get("cooldown_seconds", 30)

        self._rules = get_rules_store(config_dir)
        self._templates = get_template_store(config_dir)

        # Cooldown tracking: (camera_id, rule_id) -> last alert timestamp
        self._last_alert: Dict[tuple, float] = {}
        self._lock = threading.Lock()

    # ---------- cooldown ----------

    def _in_cooldown(self, camera_id: str, rule_id: int, timestamp: float) -> bool:
        key = (camera_id, rule_id)
        last_time = self._last_alert.get(key)
        if last_time is None:
            return False
        return (timestamp - last_time) < self._cooldown

    def all_in_cooldown(
        self, camera_id: str, rules: List[RuleDefinition], timestamp: float
    ) -> bool:
        """True when every candidate rule is in cooldown (detection can be skipped)."""
        return all(self._in_cooldown(camera_id, r.id, timestamp) for r in rules)

    # ---------- main entry ----------

    def analyze_frame(
        self,
        camera_id: str,
        rules: List[RuleDefinition],
        detections: List[Detection],
        timestamp: float,
        frame_size: Optional[tuple] = None,
    ) -> List[Violation]:
        """Evaluate the given (enabled) rules for one camera frame.

        ``frame_size`` is (width, height) in pixels — required by rules whose
        params use normalized frame coordinates (e.g. zone intrusion)."""
        violations = []
        for rule in rules:
            logic = self._templates.logic_of(rule.template)
            check = RULE_LOGICS.get(logic) if logic else None
            if check is None:
                continue
            if self._in_cooldown(camera_id, rule.id, timestamp):
                continue

            v = check(camera_id, rule, detections, timestamp,
                      frame_size=frame_size)
            if v is not None:
                violations.append(v)
                with self._lock:
                    self._last_alert[(camera_id, rule.id)] = timestamp
        return violations
