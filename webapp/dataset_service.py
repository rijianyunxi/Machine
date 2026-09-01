"""
Dataset management for online annotation and training.

Standard YOLO layout:
  datasets/<name>/
    dataset.yaml          # portable relative train/val/test + names
    images/train/*.jpg
    images/val/*.jpg
    images/test/*.jpg
    labels/train/<stem>.txt
    labels/val/<stem>.txt
    labels/test/<stem>.txt # "cls cx cy w h" normalized per line
"""

import re
import shutil
import threading
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = PROJECT_ROOT / "datasets"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SAFE_NAME = re.compile(r"^[\w\-]{1,64}$")
SAFE_FILE = re.compile(r"^[\w.\-]{1,128}$")
SPLIT_KEYS = ("train", "val", "test")


class DatasetError(ValueError):
    pass


class DatasetService:
    def __init__(self, state):
        self.state = state
        self._lock = threading.Lock()
        DATASETS_DIR.mkdir(exist_ok=True)
        self._prelabel_job = {"running": False, "done": 0, "total": 0,
                              "error": None}

    # ---------- paths ----------

    @staticmethod
    def _check_name(name: str) -> str:
        if not SAFE_NAME.match(name or ""):
            raise DatasetError("数据集名只允许字母/数字/下划线/连字符（≤64）")
        return name

    def _dir(self, name: str) -> Path:
        return DATASETS_DIR / self._check_name(name)

    def _yaml_file(self, name: str) -> Path:
        """Prefer project YAML, but accept common standard YOLO names."""
        d = self._dir(name)
        for candidate in ("dataset.yaml", "data.yaml", "data2.yaml"):
            p = d / candidate
            if p.exists():
                return p
        return d / "dataset.yaml"

    def _yaml_path(self, name: str) -> Path:
        return self._yaml_file(name)

    @staticmethod
    def _labels_dir_for_images(root: Path, images_dir: Path) -> Path:
        """Mirror the standard YOLO images/labels layout."""
        try:
            rel = images_dir.resolve().relative_to(root.resolve())
        except ValueError:
            return root / "labels" / images_dir.name
        parts = list(rel.parts)
        if "images" in parts:
            parts[parts.index("images")] = "labels"
        else:
            parts = ["labels", *parts]
        return root.joinpath(*parts)

    def _split_specs(self, name: str) -> list[tuple[str, Path, Path]]:
        """Return (split, images_dir, labels_dir) specs declared by YAML."""
        root = self._dir(name)
        doc = self._load_yaml(name)
        specs = []
        for split in SPLIT_KEYS:
            value = doc.get(split)
            if not value:
                continue
            images_dir = Path(str(value))
            if not images_dir.is_absolute():
                images_dir = root / images_dir
            labels_dir = self._labels_dir_for_images(root, images_dir)
            specs.append((split, images_dir, labels_dir))
        return specs

    def _ensure_split(self, name: str, split: str) -> tuple[Path, Path]:
        specs = {s: (i, l) for s, i, l in self._split_specs(name)}
        if split not in specs:
            raise DatasetError(f"数据集缺少 {split} split")
        images_dir, labels_dir = specs[split]
        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)
        return images_dir, labels_dir

    def _images_dir(self, name: str) -> Path:
        images_dir, _ = self._ensure_split(name, "train")
        return images_dir

    def _labels_dir(self, name: str) -> Path:
        _, labels_dir = self._ensure_split(name, "train")
        return labels_dir

    # ---------- yaml ----------

    def _load_yaml(self, name: str) -> dict:
        p = self._yaml_path(name)
        if not p.exists():
            raise DatasetError(f"数据集不存在: {name}")
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    @staticmethod
    def _names(doc: dict) -> list:
        raw = doc.get("names")
        if isinstance(raw, dict):
            return [raw[k] for k in sorted(raw, key=lambda x: int(x))]
        if isinstance(raw, list):
            return [str(x) for x in raw]
        return []

    def _write_yaml(self, name: str, names: list):
        doc = {
            # Portable YAML: omit absolute "path"; Ultralytics resolves
            # train/val relative to this dataset.yaml's directory.
            "train": "images/train",
            "val": "images/val",
            "test": "images/test",
            "nc": len(names),
            "names": {i: n for i, n in enumerate(names)},
        }
        self._yaml_path(name).write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

    # ---------- CRUD ----------

    def _stats(self, name: str) -> dict:
        stats = {"images": 0, "labeled": 0, "splits": {}}
        for split, images_dir, labels_dir in self._split_specs(name):
            images = 0
            labeled = 0
            if images_dir.exists():
                for f in images_dir.iterdir():
                    if f.suffix.lower() not in IMG_EXTS:
                        continue
                    images += 1
                    if (labels_dir / (f.stem + ".txt")).exists():
                        labeled += 1
            stats["splits"][split] = {"images": images, "labeled": labeled}
            stats["images"] += images
            stats["labeled"] += labeled
        return stats

    def list(self) -> list:
        out = []
        for d in sorted(DATASETS_DIR.iterdir()):
            if not d.is_dir() or not self._yaml_file(d.name).exists():
                continue
            try:
                doc = self._load_yaml(d.name)
                stats = self._stats(d.name)
                out.append({
                    "name": d.name,
                    "classes": self._names(doc),
                    "images": stats["images"],
                    "labeled": stats["labeled"],
                    "splits": stats["splits"],
                })
            except Exception:
                continue
        return out

    def create(self, name: str, classes: list) -> dict:
        name = self._check_name(name)
        d = self._dir(name)
        if self._yaml_file(name).exists():
            raise DatasetError(f"数据集已存在: {name}")
        clean = [c.strip() for c in classes if c and c.strip()]
        if not clean:
            raise DatasetError("至少需要一个类别")
        d.mkdir(parents=True, exist_ok=True)
        self._write_yaml(name, clean)
        self._ensure_split(name, "train")
        self._ensure_split(name, "val")
        self._ensure_split(name, "test")
        return {"name": name, "classes": clean}

    def delete(self, name: str):
        d = self._dir(name)
        if not d.exists():
            raise DatasetError(f"数据集不存在: {name}")
        shutil.rmtree(d, ignore_errors=True)

    def info(self, name: str) -> dict:
        doc = self._load_yaml(name)
        stats = self._stats(name)
        return {"name": name, "classes": self._names(doc),
                "images": stats["images"], "labeled": stats["labeled"],
                "splits": stats["splits"]}

    def set_classes(self, name: str, classes: list):
        doc = self._load_yaml(name)
        clean = [c.strip() for c in classes if c and c.strip()]
        if not clean:
            raise DatasetError("至少需要一个类别")
        # Label class ids are not remapped automatically; append/rename safely.
        self._write_yaml(name, clean)

    # ---------- images ----------

    def add_images(self, name: str, files: list, split: str = "train") -> int:
        """files: list of (filename, bytes); defaults to the train split."""
        added = 0
        for filename, content in files:
            ext = Path(filename).suffix.lower()
            if ext not in IMG_EXTS:
                continue
            if len(content) > 25 * 1024 * 1024:
                continue
            base = re.sub(r"[^\w.\-]", "_", Path(filename).stem)[:80] or "img"
            if split not in SPLIT_KEYS:
                raise DatasetError("非法 split")
            images_dir, labels_dir = self._ensure_split(name, split)
            dest = images_dir / f"{base}{ext}"
            i = 1
            while dest.exists():
                dest = images_dir / f"{base}_{i}{ext}"
                i += 1
            dest.write_bytes(content)
            added += 1
        return added

    def import_snapshots(self, name: str, date: str = None,
                         limit: int = 300) -> int:
        """Copy detection snapshots into the train split."""
        base = self.state.snapshots_dir()
        if not base.exists():
            return 0
        day_dirs = sorted([d for d in base.iterdir() if d.is_dir()
                           and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)],
                          reverse=True)
        if date:
            day_dirs = [d for d in day_dirs if d.name == date]
        count = 0
        files = []
        for day in day_dirs:
            for img in day.rglob("*.jpg"):
                files.append(img)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for img in files[: max(0, int(limit))]:
            with open(img, "rb") as fh:
                if self.add_images(name, [(img.name, fh.read())]):
                    count += 1
            if count >= int(limit):
                break
        return count

    def list_images(self, name: str, limit: int = 1000) -> list:
        out = []
        for split, images_dir, labels_dir in self._split_specs(name):
            if not images_dir.exists():
                continue
            files = sorted(
                [f for f in images_dir.iterdir()
                 if f.suffix.lower() in IMG_EXTS],
                key=lambda p: p.stat().st_mtime)
            for f in files:
                if len(out) >= int(limit):
                    return out
                lp = labels_dir / (f.stem + ".txt")
                n = 0
                if lp.exists():
                    n = sum(1 for line in lp.read_text().splitlines()
                            if line.strip())
                out.append({"file": f.name, "stem": f.stem, "split": split,
                            "labeled": lp.exists(), "boxes": n})
        return out

    def _locate_image(self, name: str, filename: str) -> tuple[str, Path, Path]:
        if not SAFE_FILE.match(str(filename or "")):
            raise DatasetError("非法文件名")
        for split, images_dir, labels_dir in self._split_specs(name):
            p = images_dir / filename
            if p.exists() and p.suffix.lower() in IMG_EXTS:
                return split, p, labels_dir / (p.stem + ".txt")
        raise DatasetError("图片不存在")

    def _label_path_for_stem(self, name: str, stem: str) -> Path:
        if not SAFE_FILE.match(stem or ""):
            raise DatasetError("非法文件名")
        for _, images_dir, labels_dir in self._split_specs(name):
            if not images_dir.exists():
                continue
            for f in images_dir.iterdir():
                if f.stem == stem and f.suffix.lower() in IMG_EXTS:
                    return labels_dir / (stem + ".txt")
        raise DatasetError("图片不存在")

    def image_path(self, name: str, filename: str) -> Path:
        _, p, _ = self._locate_image(name, filename)
        return p

    def delete_images(self, name: str, filenames: list) -> int:
        """Delete images and their label files; returns removed count."""
        if self._prelabel_job["running"]:
            raise DatasetError("AI 预标注进行中，请稍后再管理图片")
        n = 0
        for fn in filenames:
            try:
                _, p, lp = self._locate_image(name, fn)
            except DatasetError:
                continue
            p.unlink()
            if lp.exists():
                lp.unlink()
            n += 1
        return n

    # ---------- labels (YOLO txt) ----------

    def get_labels(self, name: str, stem: str) -> list:
        lp = self._label_path_for_stem(name, stem)
        boxes = []
        if lp.exists():
            for line in lp.read_text().splitlines():
                parts = line.split()
                if len(parts) != 5:
                    continue
                try:
                    boxes.append({"cls": int(float(parts[0])),
                                  "x": float(parts[1]), "y": float(parts[2]),
                                  "w": float(parts[3]), "h": float(parts[4])})
                except ValueError:
                    continue
        return boxes

    def save_labels(self, name: str, stem: str, boxes: list) -> int:
        lp = self._label_path_for_stem(name, stem)
        lines = []
        for b in boxes:
            try:
                cls = int(b["cls"])
                x, y, w, h = (float(b["x"]), float(b["y"]),
                              float(b["w"]), float(b["h"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                continue
            lines.append(f"{cls} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
        if lines:
            lp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif lp.exists():
            lp.unlink()  # empty annotation = remove the label file
        return len(lines)
    # ---------- bulk AI pre-labeling ----------

    def prelabel_status(self) -> dict:
        return dict(self._prelabel_job)

    def prelabel(self, name: str, model: str, conf: float = 0.4,
                 only_unlabeled: bool = True, limit: int = 200):
        if self._prelabel_job["running"]:
            raise DatasetError("已有预标注任务在运行")
        detector = self.state._get_standalone_detector()
        if detector is None or not detector.loaded_models:
            raise DatasetError("没有可用的检测模型")
        self._prelabel_job = {"running": True, "done": 0, "total": 0,
                              "error": None}

        def job():
            try:
                targets = []
                for info in self.list_images(name, limit=100000):
                    if only_unlabeled and info["labeled"]:
                        continue
                    targets.append(info)
                targets = targets[: max(0, int(limit))]
                self._prelabel_job["total"] = len(targets)
                overrides = []
                try:
                    if model and conf is not None:
                        detector.set_thresholds(model, confidence=conf)
                        overrides.append(model)
                    for info in targets:
                        if not self._prelabel_job["running"]:
                            break
                        p = self.image_path(name, info["file"])
                        import cv2

                        img = cv2.imread(str(p))
                        if img is None:
                            continue
                        dets = detector.detect_all(img, model_names=[model] if model else None)
                        boxes = self._dets_to_yolo(dets, img.shape)
                        self.save_labels(name, info["stem"], boxes)
                        self._prelabel_job["done"] += 1
                finally:
                    cfg = {m["name"]: m for m in
                           self.state.settings().get("model", {}).get("models", [])}
                    for mn in overrides:
                        val = cfg.get(mn, {}).get("confidence_override")
                        detector.set_thresholds(mn, confidence=val)
            except Exception as e:
                self._prelabel_job["error"] = str(e)
            finally:
                self._prelabel_job["running"] = False

        threading.Thread(target=job, name="prelabel", daemon=True).start()

    @staticmethod
    def _dets_to_yolo(dets, shape) -> list:
        h, w = shape[:2]
        boxes = []
        for d in dets:
            x1, y1, x2, y2 = d.bbox
            boxes.append({
                "cls": d.class_id,
                "x": min(max(((x1 + x2) / 2) / w, 0), 1),
                "y": min(max(((y1 + y2) / 2) / h, 0), 1),
                "w": min(max((x2 - x1) / w, 0), 1),
                "h": min(max((y2 - y1) / h, 0), 1),
            })
        return boxes





