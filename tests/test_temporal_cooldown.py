import unittest

from core.analyzer import BehaviorAnalyzer, ZoneIntrusionCheck
from core.detector import Detection
from rules.definitions import LOGIC_ZONE_INTRUSION, RuleDefinition


class TemporalCooldownTests(unittest.TestCase):
    def setUp(self):
        ZoneIntrusionCheck._dwell_since.clear()
        self.analyzer = BehaviorAnalyzer(
            {"alert": {"cooldown_seconds": 30}},
            {"zone": {"logic": LOGIC_ZONE_INTRUSION}},
        )
        self.rule = RuleDefinition(
            id=1,
            name="zone dwell",
            description="zone dwell",
            template="zone",
            params={
                "target_classes": ["person"],
                "zones": [{"x": 0, "y": 0, "w": 1, "h": 1}],
                "dwell_seconds": 10,
            },
        )
        self.inside = [Detection(0, "person", 0.9, (10, 10, 20, 20))]

    def _analyze(self, timestamp, detections):
        return self.analyzer.analyze_frame(
            "cam-1", [self.rule], detections, timestamp, frame_size=(100, 100)
        )

    def test_cooldown_suppresses_alert_but_still_resets_dwell_state(self):
        self.assertEqual(self._analyze(0, self.inside), [])
        self.assertEqual(len(self._analyze(10, self.inside)), 1)
        # This frame is in cooldown but must clear the continuous-presence timer.
        self.assertEqual(self._analyze(11, []), [])
        self.assertEqual(self._analyze(41, self.inside), [])
        self.assertEqual(len(self._analyze(51, self.inside)), 1)


if __name__ == "__main__":
    unittest.main()
