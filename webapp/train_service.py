"""
Online training orchestration.

Launches training as a SUBPROCESS (a crash never takes the panel/detection
down), writes a small args JSON, and tracks progress by parsing the
results.csv ultralytics writes after every epoch.
"""

import csv
import json
import subprocess
import sys
import threading
from pathlib import Path

from infrastructure.storage_paths import (
    MODELS_DIR,
    canonical_model_reference,
    ensure_storage_dirs,
    resolve_model_path,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "train_worker.py"


class TrainService:
    def __init__(self, state):
        self.state = state
        ensure_storage_dirs()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._meta: dict = {}

    # ---------- start / stop ----------

    def start(self, dataset: str, base_model: str, epochs: int = 100,
              imgsz: int = 640, batch: int = 16, device: str = "auto",
              name: str = "") -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise RuntimeError("已有训练任务在运行，请先停止")
            name = (name or f"{dataset}_{epochs}ep").strip() or "panel_run"
            if not Path(name).name == name:
                raise RuntimeError("非法任务名")

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
                "project": str(PROJECT_ROOT / "runs" / "panel"),
                "name": name,
            }
            args_file = PROJECT_ROOT / "runs" / "panel" / f"{name}_args.json"
            args_file.parent.mkdir(parents=True, exist_ok=True)
            args_file.write_text(json.dumps(args), encoding="utf-8")

            log_file = args_file.parent / f"{name}.log"
            log_fh = open(log_file, "w", encoding="utf-8")
            self._proc = subprocess.Popen(
                [sys.executable, str(WORKER), str(args_file)],
                stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT))
            self._meta = {"name": name, "dataset": dataset, "epochs": int(epochs),
                          "log": str(log_file),
                          "run_dir": str(PROJECT_ROOT / "runs" / "panel" / name),
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
        base = PROJECT_ROOT / "runs" / "panel"
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
        """Copy best.pt of a finished run into storage/models and register it."""
        run_best = PROJECT_ROOT / "runs" / "panel" / name / "weights" / "best.pt"
        if not run_best.exists():
            raise RuntimeError("该任务还没有 best.pt（未完成或已失败）")
        dest = MODELS_DIR / f"{model_name}.pt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(run_best.read_bytes())
        self.state.register_model(model_name, dest.name, enabled=False)
        return {"file": dest.name, "model": model_name}


def _f(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None
