"""Regression tests for the core capture-to-analysis rule path.

Run from the project root with::

    .venv/bin/python tests/test_core_chain.py
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analyzer import ZoneIntrusionCheck
from core.detector import Detection
from core.rules_graph import _reset_state, evaluate_graph
from main import MachineVisionSystem
from rules.rules_engine import RuleDefinition


def det(class_name="person", confidence=0.9, bbox=(10, 10, 20, 20)):
    return Detection(
        class_id=0,
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        model_name="test",
    )


def node(node_id, node_type, params=None):
    result = {"id": node_id, "type": node_type}
    if params is not None:
        result["params"] = params
    return result


def edge(source, target):
    return {"from": source, "to": target}


def duration_graph(seconds=10):
    return {
        "nodes": [
            node("present", "class_present", {"classes": ["person"], "min_confidence": 0.5}),
            node("duration", "duration", {"seconds": seconds}),
            node("alert", "alert"),
        ],
        "edges": [
            edge("present", "duration"),
            edge("duration", "alert"),
        ],
    }


def test_graph_duration_isolated_by_camera():
    _reset_state()
    graph = duration_graph()
    person = [det()]

    assert evaluate_graph(graph, person, (100, 100), 0.0, 101, camera_id="cam-a") is None
    # cam-b must start its own timer instead of inheriting cam-a's timer.
    assert evaluate_graph(graph, person,  (100, 100), 10.0, 101, camera_id="cam-b") is None
    assert evaluate_graph(graph, person,  (100, 100), 10.0, 101, camera_id="cam-a") is not None
    assert evaluate_graph(graph, person,  (100, 100), 20.0, 101, camera_id="cam-b") is not None
    _reset_state()


def test_zone_dwell_resets_when_class_disappears_and_is_class_aggregated():
    check = ZoneIntrusionCheck()
    check._dwell_since.clear()
    rule = RuleDefinition(
        id=202,
        name="zone",
        description="zone",
        template="zone_intrusion",
        params={
            "target_classes": ["person"],
            "zones": [{"x": 0, "y": 0, "w": 0.5, "h": 1}],
            "dwell_seconds": 10,
            "min_confidence": 0.5,
        },
    )
    inside = det(bbox=(10, 10, 20, 20))
    outside = det(bbox=(80, 80, 90, 90))

    assert check("cam-1", rule, [inside], 0.0, frame_size=(100, 100)) is None
    # An entirely empty frame resets the class timer.
    assert check("cam-1", rule, [], 5.0, frame_size=(100, 100)) is None
    assert check("cam-1", rule, [inside], 10.0, frame_size=(100, 100)) is None

    # Since the timer started again at t=10, this is still below dwell.
    # The outside instance must not reset the class timer after the inside one.
    assert check("cam-1", rule, [inside, outside], 19.0, frame_size=(100, 100)) is None
    violation = check("cam-1", rule, [outside, inside], 20.0, frame_size=(100, 100))
    assert violation is not None and violation.bbox == inside.bbox
    check._dwell_since.clear()


def test_main_analyzes_empty_detection_frames():
    """An empty detector result is valid input for absence/class_absent rules."""
    system = MachineVisionSystem.__new__(MachineVisionSystem)
    system._running = True
    system._settings = {"capture": {"target_fps": 1000}}
    system._config_dir = "config"
    system._cameras_config = {
        "cameras": [{"id": "cam-1", "enabled": True, "rules": [303]}]
    }
    system._stats = {
        "frames_processed": 0,
        "violations_detected": 0,
        "snapshots_saved": 0,
        "start_time": time.time(),
    }
    system._next_stats_time = time.time() + 3600
    system._logger = SimpleNamespace(info=lambda *args, **kwargs: None)

    frame_data = SimpleNamespace(
        frame=np.zeros((20, 30, 3), dtype=np.uint8),
        timestamp=time.time(),
    )

    class CameraManager:
        def get_frame(self, camera_id):
            return frame_data

    class Detector:
        def detect_all(self, frame, model_names=None):
            return []

    class Analyzer:
        def all_in_cooldown(self, camera_id, rules, timestamp):
            return False

        def analyze_frame(self, camera_id, rules, detections, timestamp, frame_size=None):
            assert detections == []
            assert frame_size == (30, 20)
            system._running = False
            return []

    system._camera_manager = CameraManager()
    system._detector = Detector()
    system._analyzer = Analyzer()

    fake_rule = SimpleNamespace(models=[], params={})
    fake_rules_store = SimpleNamespace(
        get_rules_for_camera=lambda rule_ids: [fake_rule]
    )
    with patch("main.get_rules_store", return_value=fake_rules_store):
        system._processing_loop()

    assert system._stats["frames_processed"] == 1


TESTS = [
    ("graph duration camera isolation", test_graph_duration_isolated_by_camera),
    ("zone dwell reset and class aggregation", test_zone_dwell_resets_when_class_disappears_and_is_class_aggregated),
    ("main analyzes empty detections", test_main_analyzes_empty_detection_frames),
]


if __name__ == "__main__":
    for name, test in TESTS:
        test()
        print(f"[PASS] {name}")
    print("ALL PASSED")
