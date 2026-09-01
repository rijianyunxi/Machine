import unittest

from core.detector import Detection
from core.rules_graph import _reset_state, evaluate_graph, validate_graph


class RulesGraphModelTests(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_detector_node_requires_model_when_requested(self):
        graph = {
            "nodes": [
                {"id": "source", "type": "class_present", "params": {"classes": ["person"]}},
                {"id": "alert", "type": "alert", "params": {}},
            ],
            "edges": [{"from": "source", "to": "alert"}],
        }
        with self.assertRaisesRegex(ValueError, "必须选择检测模型"):
            validate_graph(graph, available_models=["ppe"], require_models=True)

    def test_model_is_persisted_and_unknown_model_is_rejected(self):
        graph = {
            "nodes": [
                {"id": "source", "type": "class_present", "model": "ppe",
                 "params": {"classes": ["person"]}},
                {"id": "alert", "type": "alert", "params": {}},
            ],
            "edges": [{"from": "source", "to": "alert"}],
        }
        normalized = validate_graph(graph, available_models=["ppe"], require_models=True)
        self.assertEqual(normalized["nodes"][0]["model"], "ppe")
        with self.assertRaisesRegex(ValueError, "不存在的模型"):
            validate_graph(graph | {"nodes": [
                {"id": "source", "type": "class_present", "model": "missing",
                 "params": {"classes": ["person"]}},
                {"id": "alert", "type": "alert", "params": {}},
            ]}, available_models=["ppe"], require_models=True)

    def test_graph_node_uses_only_its_model_detections(self):
        graph = {
            "nodes": [
                {"id": "source", "type": "class_present", "model": "ppe",
                 "params": {"classes": ["person"], "min_confidence": 0.5}},
                {"id": "alert", "type": "alert", "params": {}},
            ],
            "edges": [{"from": "source", "to": "alert"}],
        }
        rule = type("Rule", (), {"name": "model filter", "description": "", "severity": 3})()
        smoking_only = [Detection(0, "person", 0.9, (0, 0, 10, 10), "smoking")]
        self.assertIsNone(evaluate_graph(graph, smoking_only, (100, 100), 1.0, 1, rule=rule))
        ppe = Detection(0, "person", 0.9, (0, 0, 10, 10), "ppe")
        self.assertIsNotNone(evaluate_graph(graph, [ppe], (100, 100), 2.0, 1, rule=rule))


if __name__ == "__main__":
    unittest.main()
