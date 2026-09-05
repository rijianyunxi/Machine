"""
Online training orchestration.

Launches training as a SUBPROCESS (a crash never takes the panel/detection
down), writes a small args JSON, and tracks progress by parsing the
results.csv ultralytics writes after every epoch.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from infrastructure.storage_paths import (
    MODELS_DIR,
    canonical_model_reference,
    ensure_storage_dirs,
    resolve_model_path,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = PROJECT_ROOT / "runs" / "panel"
WORKER = Path(__file__).resolve().parent / "train_worker.py"
SAFE_RUN_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _inside(root: Path, candidate: Path, *, strict: bool = False) -> Path:
    """Resolve *candidate* and require it to remain below *root*."""
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=strict)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError("训练产物路径越界") from exc
    return resolved_candidate


def _contains_symlink(root: Path, candidate: Path) -> bool:
    """Whether any raw path component below *root* is a symbolic link."""
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            return True
    return False


class TrainService:
    def __init__(self, state):
        self.state = state
        ensure_storage_dirs()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._meta: dict = {}

    # ---------- start / stop ----------

    @staticmethod
    def _validate_job_name(name: str) -> str:
        name = (name or "").strip()
        if not SAFE_RUN_NAME.fullmatch(name):
            raise RuntimeError("任务名只允许字母、数字、下划线和连字符（≤64）")
        return name

    @staticmethod
    def _validate_model_name(model_name: str) -> str:
        model_name = (model_name or "").strip()
        if not SAFE_RUN_NAME.fullmatch(model_name):
            raise RuntimeError("模型名只允许字母、数字、下划线和连字符（≤64）")
        return model_name

    def start(self, dataset: str, base_model: str, epochs: int = 100,
              imgsz: int = 640, batch: int = 16, device: str = "auto",
              name: str = "") -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise RuntimeError("已有训练任务在运行，请先停止")
            name = self._validate_job_name(
                (name or f"{dataset}_{epochs}ep").strip() or "panel_run"
            )

            ds_yaml = self.state.datasets.yaml_path(dataset)
            if not ds_yaml.is_file():
                raise RuntimeError(f"数据集不存在: {dataset}")
            resolved_model = resolve_model_path(base_model)
            if resolved_model.is_file():
                model_path = str(resolved_model)
            elif Path(base_model).is_absolute() or "/" in base_model or "\\" in base_model:
                raise RuntimeError(f"基础模型不存在: {base_model}")
            else:
                # Ultralytics accepts official model names and downloads them
                # when necessary (for example yolov8n.pt).
                model_path = base_model

            args = {
                "data": str(ds_yaml),
                "model": model_path,
                "epochs": int(epochs),
                "imgsz": int(imgsz),
                "batch": int(batch),
                "device": device,
                "project": str(RUNS_ROOT),
                "name": name,
            }
            args_file = RUNS_ROOT / f"{name}_args.json"
            args_file.parent.mkdir(parents=True, exist_ok=True)
            args_file.write_text(json.dumps(args), encoding="utf-8")

            log_file = args_file.parent / f"{name}.log"
            log_fh = open(log_file, "w", encoding="utf-8")
            self._proc = subprocess.Popen(
                [sys.executable, str(WORKER), str(args_file)],
                stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT))
            self._meta = {"name": name, "dataset": dataset, "epochs": int(epochs),
                          "log": str(log_file),
                          "run_dir": str(RUNS_ROOT / name),
                          "started": True}
            return {"name": name, "pid": self._proc.pid}

    def stop(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                return True
            return False

    # ---------- status ----------

    def status(self) -> dict:
        meta = dict(self._meta)
        if not meta:
            return {"state": "idle"}
        running = self._proc is not None and self._proc.poll() is None
        rc = self._proc.poll() if self._proc is not None else None
        out = {
            "state": "running" if running else (
                "completed" if rc == 0 else "failed" if rc is not None else "idle"),
            "name": meta.get("name"),
            "dataset": meta.get("dataset"),
            "epochs_total": meta.get("epochs"),
        }

        # progress from results.csv (one row per epoch)
        csv_path = Path(meta.get("run_dir", "")) / "results.csv"
        if csv_path.exists():
            try:
                with open(csv_path, newline="", encoding="utf-8") as fh:
                    rows = list(csv.DictReader(fh))
                if rows:
                    last = rows[-1]
                    out["epoch"] = len(rows)
                    out["mAP50"] = _f(last.get("metrics/mAP50(B)"))
                    out["mAP50_95"] = _f(last.get("metrics/mAP50-95(B)"))
                    out["precision"] = _f(last.get("metrics/precision(B)"))
                    out["recall"] = _f(last.get("metrics/recall(B)"))
            except Exception:
                pass

        # log tail
        log = Path(meta.get("log", ""))
        if log.exists():
            try:
                out["log_tail"] = log.read_text(
                    encoding="utf-8", errors="replace").splitlines()[-12:]
            except Exception:
                pass

        # best weights
        best = Path(meta.get("run_dir", "")) / "weights" / "best.pt"
        out["best_path"] = str(best) if best.exists() else None
        out["registered"] = (best.exists() and
                             canonical_model_reference(best.name) in
                             [canonical_model_reference(m.get("path", ""))
                              for m in self.state.settings()
                              .get("model", {}).get("models", [])])
        return out

    def runs(self) -> list:
        base = RUNS_ROOT
        if not base.exists():
            return []
        out = []
        for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime,
                        reverse=True):
            best = d / "weights" / "best.pt"
            if not best.exists():
                continue
            out.append({"name": d.name,
                        "best": str(best),
                        "size_mb": round(best.stat().st_size / 1048576, 1),
                        "mtime": int(best.stat().st_mtime)})
        return out

    def register_best(self, name: str, model_name: str) -> dict:
        """Atomically register a completed run without crossing storage roots."""
        name = self._validate_job_name(name)
        model_name = self._validate_model_name(model_name)

        with self._lock:
            RUNS_ROOT.mkdir(parents=True, exist_ok=True)
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            run_root = RUNS_ROOT.resolve(strict=True)
            models_root = MODELS_DIR.resolve(strict=True)

            # Resolve every component before copying.  This rejects path
            # traversal and symlink escapes even if a valid-looking run name
            # happens to point at an unexpected location.
            raw_run_dir = RUNS_ROOT / name
            raw_run_best = raw_run_dir / "weights" / "best.pt"
            try:
                run_dir = _inside(run_root, raw_run_dir, strict=True)
                resolved_best = _inside(run_root, raw_run_best, strict=True)
            except FileNotFoundError as exc:
                raise RuntimeError("该任务还没有 best.pt（未完成或已失败）") from exc
            if (_contains_symlink(RUNS_ROOT, raw_run_best)
                    or not resolved_best.is_file()):
                raise RuntimeError("训练产物 best.pt 无效")

            dest = _inside(models_root, MODELS_DIR / f"{model_name}.pt")
            if dest.exists() or dest.is_symlink():
                raise RuntimeError(f"模型文件已存在: {dest.name}")

            temp_path = None
            published = False
            try:
                fd, temp_name = tempfile.mkstemp(
                    prefix=f".{model_name}.", suffix=".tmp", dir=models_root
                )
                temp_path = Path(temp_name)
                with os.fdopen(fd, "wb") as temp_file, resolved_best.open("rb") as source:
                    shutil.copyfileobj(source, temp_file)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, dest)
                published = True
                self.state.register_model(model_name, dest.name, enabled=False)
            except Exception:
                if published:
                    dest.unlink(missing_ok=True)
                raise
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
        return {"file": dest.name, "model": model_name}


def _f(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None
