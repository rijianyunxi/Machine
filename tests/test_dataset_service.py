from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.dataset_service import (
    DatasetBusyError,
    DatasetError,
    DatasetService,
)


class DatasetServiceSplitTests(unittest.TestCase):
    def _service(self, root: Path) -> DatasetService:
        service = DatasetService.__new__(DatasetService)
        service.state = object()
        service._prelabel_job = {"running": False, "done": 0, "total": 0,
                                 "error": None}
        self.dataset_dir = root / "demo"
        self.dataset_dir.mkdir()
        (self.dataset_dir / "dataset.yaml").write_text(
            "train: images/train\nval: images/val\ntest: images/test\n"
            "nc: 2\nnames: {0: helmet, 1: vest}\n",
            encoding="utf-8",
        )
        for split in ("train", "val", "test"):
            (self.dataset_dir / "images" / split).mkdir(parents=True)
            (self.dataset_dir / "labels" / split).mkdir(parents=True)
        return service

    def test_delete_blocks_running_prelabel_and_clears_finished_logs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = self._service(root)
            service._prelabel_job = {
                "running": True,
                "dataset": "demo",
                "logs": ["正在处理 demo"],
            }
            with patch("webapp.dataset_service.resolve_dataset_dir",
                       return_value=self.dataset_dir):
                with self.assertRaises(DatasetBusyError):
                    service.delete("demo")
            self.assertTrue(self.dataset_dir.exists())

            service._prelabel_job = {
                "running": False,
                "dataset": "demo",
                "logs": ["任务完成"],
            }
            with patch("webapp.dataset_service.resolve_dataset_dir",
                       return_value=self.dataset_dir):
                service.delete("demo")
            self.assertFalse(self.dataset_dir.exists())
            self.assertEqual(service._prelabel_job["logs"], [])
            self.assertFalse(service._prelabel_job["running"])

    def test_duplicate_filename_operations_are_split_aware(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = self._service(root)
            train = self.dataset_dir / "images" / "train" / "same.jpg"
            val = self.dataset_dir / "images" / "val" / "same.jpg"
            train.write_bytes(b"train")
            val.write_bytes(b"val")

            with patch("webapp.dataset_service.resolve_dataset_dir",
                       return_value=self.dataset_dir):
                self.assertEqual(service.image_path("demo", "same.jpg", "val"),
                                 val)
                self.assertEqual(service.image_path("demo", "same.jpg", "train"),
                                 train)
                service.save_labels("demo", "same", [{
                    "cls": 1, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.3,
                }], split="val")
                self.assertEqual(service.get_labels("demo", "same", split="train"), [])
                self.assertEqual(service.get_labels("demo", "same", split="val"), [{
                    "cls": 1, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.3,
                }])
                self.assertEqual(service.delete_images(
                    "demo", [{"file": "same.jpg", "split": "val"}]), 1)

            self.assertTrue(train.exists())
            self.assertFalse(val.exists())
            self.assertFalse((self.dataset_dir / "labels" / "val" / "same.txt").exists())

    def test_save_labels_ignores_class_ids_outside_dataset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = self._service(root)
            image = self.dataset_dir / "images" / "train" / "one.jpg"
            image.write_bytes(b"image")
            with patch("webapp.dataset_service.resolve_dataset_dir",
                       return_value=self.dataset_dir):
                saved = service.save_labels("demo", "one", [
                    {"cls": 9, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.3},
                    {"cls": 0, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.3},
                ], split="train")
            self.assertEqual(saved, 1)
            self.assertEqual(
                (self.dataset_dir / "labels" / "train" / "one.txt").read_text(),
                "0 0.500000 0.500000 0.200000 0.300000\n",
            )

    def test_set_classes_refuses_to_orphan_existing_labels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = self._service(root)
            image = self.dataset_dir / "images" / "train" / "one.jpg"
            image.write_bytes(b"image")
            label = self.dataset_dir / "labels" / "train" / "one.txt"
            label.write_text("1 0.5 0.5 0.2 0.3\n", encoding="utf-8")
            with patch("webapp.dataset_service.resolve_dataset_dir",
                       return_value=self.dataset_dir):
                with self.assertRaises(DatasetError):
                    service.set_classes("demo", ["helmet"])
                self.assertEqual(service._names(service._load_yaml("demo")),
                                 ["helmet", "vest"])


    def test_prelabel_maps_model_class_name_to_dataset_class_id(self):
        from core.detector import Detection

        mapping = DatasetService._class_ids_by_name(["person", "helmet"])
        boxes, skipped = DatasetService._dets_to_yolo(
            [Detection(0, "helmet", 0.9, (10, 10, 30, 30))],
            (100, 100, 3),
            mapping,
        )
        self.assertEqual(skipped, 0)
        self.assertEqual(boxes[0]["cls"], 1)

    def test_prelabel_skips_unmapped_model_classes(self):
        from core.detector import Detection

        boxes, skipped = DatasetService._dets_to_yolo(
            [Detection(0, "cigarette", 0.9, (10, 10, 30, 30))],
            (100, 100, 3),
            DatasetService._class_ids_by_name(["person"]),
        )
        self.assertEqual(boxes, [])
        self.assertEqual(skipped, 1)

    def test_prelabel_class_mapping_normalizes_names_and_rejects_duplicates(self):
        from core.detector import Detection

        mapping = DatasetService._class_ids_by_name(["Person", "Safety Helmet"])
        boxes, skipped = DatasetService._dets_to_yolo(
            [Detection(4, "  safety   helmet  ", 0.9, (10, 10, 30, 30))],
            (100, 100, 3), mapping,
        )
        self.assertEqual((boxes[0]["cls"], skipped), (1, 0))
        with self.assertRaisesRegex(DatasetError, "重复类别名"):
            DatasetService._class_ids_by_name(["person", " Person "])


if __name__ == "__main__":
    unittest.main()
