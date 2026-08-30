"""
Dataset management for online annotation and training.

Layout (YOLO format, single "all" split — demo-friendly; val == train):
  datasets/<name>/
    dataset.yaml          # path / train / val / names
    images/all/*.jpg
    labels/all/<stem>.txt # "cls cx cy w h" normalized per line
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


class DatasetError(ValueError):
    pass


class DatasetService:
    def __init__(self, state):
        self.state = state
        self._lock = threading.Lock()
        DATASETS_DIR.mkdir(exist_ok=True)
        # bulk AI pre-label progress (single job at a time)
        self._prelabel_job = {"running": False, "done": 0, "total": 0, "error": None}

    # ---------- paths ----------

    @staticmethod
    def _check_name(name: str) -> str:
        if not SAFE_NAME.match(name or ""):
            raise DatasetError("数据集名只允许字母/数字/下划线/连字符（≤64）")
        return name

    def _dir(self, name: str) -> Path:
        return DATASETS_DIR / self._check_name(name)

    def _images_dir(self, name: str) -> Path:
        d = self._dir(name) / "images" / "all"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _labels_dir(self, name: str) -> Path:
        d = self._dir(name) / "labels" / "all"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _yaml_path(self, name: str) -> Path:
        return self._dir(name) / "dataset.yaml"

    # ---------- yaml ----------

    def _load_yaml(self, name: str) -> dict:
        p = self._yaml_path(name)
        if not p.exists():
            raise DatasetError(f"数据集不存在: {name}")
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def _write_yaml(self, name: str, names: list):
        doc = {
            "path": str(self._dir(name)),
            "train": "images/all",
            "val": "images/all",   # demo: val == train
            "names": {i: n for i, n in enumerate(names)},
        }
        self._yaml_path(name).write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

    # ---------- CRUD ----------

    def list(self) -> list:
        out = []
        for d in sorted(DATASETS_DIR.iterdir()):
            if not (d / "dataset.yaml").exists():
                continue
            try:
                doc = self._load_yaml(d.name)
                classes = [doc.get("names", {}).get(k) for k in sorted(
                    doc.get("names", {}).keys())]
                imgs = list((d / "images" / "all").glob("*")) if \
                    (d / "images" / "all").exists() else []
                labeled = sum(
                    1 for f in imgs
                    if (d / "labels" / "all" / (f.stem + ".txt")).exists())
                out.append({
                    "name": d.name,
                    "classes": [c for c in classes if c],
                    "images": len(imgs),
                    "labeled": labeled,
                })
            except Exception:
                continue
        return out

    def create(self, name: str, classes: list) -> dict:
        name = self._check_name(name)
        d = self._dir(name)
        if self._yaml_path(name).exists():
            raise DatasetError(f"数据集已存在: {name}")
        d.mkdir(parents=True, exist_ok=True)
        self._images_dir(name)
        self._labels_dir(name)
        clean = [c.strip() for c in classes if c and c.strip()]
        if not clean:
            raise DatasetError("至少需要一个类别")
        self._write_yaml(name, clean)
        return {"name": name, "classes": clean}

    def delete(self, name: str):
        d = self._dir(name)
        if not d.exists():
            raise DatasetError(f"数据集不存在: {name}")
        shutil.rmtree(d, ignore_errors=True)

    def info(self, name: str) -> dict:
        doc = self._load_yaml(name)
        names = doc.get("names", {})
        classes = [names[k] for k in sorted(names.keys())]
        imgs = sorted([f for f in self._images_dir(name).iterdir()
                       if f.suffix.lower() in IMG_EXTS])
        labeled = sum(1 for f in imgs
                      if (self._labels_dir(name) / (f.stem + ".txt")).exists())
        return {"name": name, "classes": classes,
                "images": len(imgs), "labeled": labeled}

    def set_classes(self, name: str, classes: list):
        doc = self._load_yaml(name)
        clean = [c.strip() for c in classes if c and c.strip()]
        if not clean:
            raise DatasetError("至少需要一个类别")
        # remap label files if class count shrinks/reorders is NOT attempted —
        # appending/renaming is safe, reordering is on the user
        self._write_yaml(name, clean)

    # ---------- images ----------

    def add_images(self, name: str, files: list) -> int:
        """files: list of (filename, bytes)."""
        added = 0
        for filename, content in files:
            ext = Path(filename).suffix.lower()
            if ext not in IMG_EXTS:
                continue
            if len(content) > 25 * 1024 * 1024:
                continue
            base = re.sub(r"[^\w.\-]", "_", Path(filename).stem)[:80] or "img"
            dest = self._images_dir(name) / f"{base}{ext}"
            i = 1
            while dest.exists():
                dest = self._images_dir(name) / f"{base}_{i}{ext}"
                i += 1
            dest.write_bytes(content)
            added += 1
        return added

    def import_snapshots(self, name: str, date: str = None,
                         limit: int = 300) -> int:
        """Copy detection snapshots into the dataset as training material."""
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
        labels = self._labels_dir(name)
        for f in sorted(self._images_dir(name).iterdir(),
                        key=lambda p: p.stat().st_mtime):
            if f.suffix.lower() not in IMG_EXTS:
                continue
            lp = labels / (f.stem + ".txt")
            n = 0
            if lp.exists():
                n = sum(1 for line in lp.read_text().splitlines() if line.strip())
            out.append({"file": f.name, "stem": f.stem,
                        "labeled": lp.exists(), "boxes": n})
            if len(out) >= int(limit):
                break
        return out

    def image_path(self, name: str, filename: str) -> Path:
        if not SAFE_FILE.match(filename or ""):
            raise DatasetError("非法文件名")
        p = (self._images_dir(name) / filename)
        if not p.exists():
            raise DatasetError("图片不存在")
        return p

    def delete_images(self, name: str, filenames: list) -> int:
        """Delete images and their label files; returns the removed count."""
        if self._prelabel_job["running"]:
            raise DatasetError("AI 预标注进行中，请稍后再管理图片")
        imgs = self._images_dir(name)
        labels = self._labels_dir(name)
        n = 0
        for fn in filenames:
            if not SAFE_FILE.match(str(fn or "")):
                continue
            p = imgs / fn
            if not p.exists() or p.suffix.lower() not in IMG_EXTS:
                continue
            p.unlink()
            lp = labels / (p.stem + ".txt")
            if lp.exists():
                lp.unlink()
            n += 1
        return n

    # ---------- labels (YOLO txt) ----------

    def get_labels(self, name: str, stem: str) -> list:
        if not SAFE_FILE.match(stem or ""):
            raise DatasetError("非法文件名")
        lp = self._labels_dir(name) / f"{stem}.txt"
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
        if not SAFE_FILE.match(stem or ""):
            raise DatasetError("非法文件名")
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
        lp = self._labels_dir(name) / f"{stem}.txt"
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
