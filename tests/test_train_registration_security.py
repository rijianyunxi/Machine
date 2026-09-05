import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp.train_service import TrainService


class _State:
    def __init__(self, fail_registration=False):
        self.fail_registration = fail_registration
        self.registered = []

    def register_model(self, name, filename, enabled=False):
        if self.fail_registration:
            raise RuntimeError("registry failure")
        self.registered.append((name, filename, enabled))


class TrainRegistrationSecurityTests(unittest.TestCase):
    def _service(self, state):
        service = TrainService.__new__(TrainService)
        service.state = state
        service._lock = threading.Lock()
        service._proc = None
        service._meta = {}
        return service

    def test_rejects_run_and_model_path_traversal_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runs, models = root / "runs", root / "models"
            state = _State()
            service = self._service(state)
            with patch("webapp.train_service.RUNS_ROOT", runs), patch(
                "webapp.train_service.MODELS_DIR", models
            ):
                with self.assertRaisesRegex(RuntimeError, "任务名"):
                    service.register_best("../outside", "new_model")
                with self.assertRaisesRegex(RuntimeError, "模型名"):
                    service.register_best("valid_run", "../escaped")
            self.assertFalse((root / "escaped.pt").exists())
            self.assertFalse(models.exists())

    def test_registers_only_best_file_inside_run_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runs, models = root / "runs", root / "models"
            best = runs / "safe_run" / "weights" / "best.pt"
            best.parent.mkdir(parents=True)
            best.write_bytes(b"weights")
            state = _State()
            service = self._service(state)
            with patch("webapp.train_service.RUNS_ROOT", runs), patch(
                "webapp.train_service.MODELS_DIR", models
            ):
                result = service.register_best("safe_run", "ppe_v2")
            self.assertEqual(result, {"file": "ppe_v2.pt", "model": "ppe_v2"})
            self.assertEqual((models / "ppe_v2.pt").read_bytes(), b"weights")
            self.assertEqual(state.registered, [("ppe_v2", "ppe_v2.pt", False)])

    def test_rejects_symlinked_best_file_outside_run_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runs, models = root / "runs", root / "models"
            outside = root / "outside.pt"
            outside.write_bytes(b"outside")
            best = runs / "safe_run" / "weights" / "best.pt"
            best.parent.mkdir(parents=True)
            best.symlink_to(outside)
            service = self._service(_State())
            with patch("webapp.train_service.RUNS_ROOT", runs), patch(
                "webapp.train_service.MODELS_DIR", models
            ):
                with self.assertRaisesRegex(RuntimeError, "路径越界"):
                    service.register_best("safe_run", "ppe_v2")
            self.assertFalse((models / "ppe_v2.pt").exists())

    def test_rejects_in_root_symlinked_weight_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runs, models = root / "runs", root / "models"
            real_weights = runs / "safe_run" / "actual_weights"
            real_weights.mkdir(parents=True)
            (real_weights / "best.pt").write_bytes(b"weights")
            (runs / "safe_run" / "weights").symlink_to(real_weights, target_is_directory=True)
            service = self._service(_State())
            with patch("webapp.train_service.RUNS_ROOT", runs), patch(
                "webapp.train_service.MODELS_DIR", models
            ):
                with self.assertRaisesRegex(RuntimeError, "best.pt 无效"):
                    service.register_best("safe_run", "ppe_v2")
            self.assertFalse((models / "ppe_v2.pt").exists())

    def test_start_rejects_unsafe_job_names_before_touching_the_dataset(self):
        service = self._service(_State())
        with self.assertRaisesRegex(RuntimeError, "任务名"):
            service.start("anything", "yolov8n.pt", name="../escape")

    def test_removes_published_file_if_registry_rejects_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runs, models = root / "runs", root / "models"
            best = runs / "safe_run" / "weights" / "best.pt"
            best.parent.mkdir(parents=True)
            best.write_bytes(b"weights")
            service = self._service(_State(fail_registration=True))
            with patch("webapp.train_service.RUNS_ROOT", runs), patch(
                "webapp.train_service.MODELS_DIR", models
            ):
                with self.assertRaisesRegex(RuntimeError, "registry failure"):
                    service.register_best("safe_run", "ppe_v2")
            self.assertFalse((models / "ppe_v2.pt").exists())


if __name__ == "__main__":
    unittest.main()
