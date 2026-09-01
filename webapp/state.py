"""
RuntimeState - thread-safe facade between the web panel and the system.

The panel never touches MachineVisionSystem internals directly; everything
goes through this facade so access stays consistent whether the panel runs
embedded in the main process (system is set) or standalone read-only
(system is None, config/YAML/DB only).

Config writes flow: validate -> ConfigService (backup + write) -> hot-apply
to live objects -> report what needs a restart.
"""

import copy
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from ruamel.yaml.comments import CommentedMap

from rules.rules_engine import get_rules_store
from utils.logger import get_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Panel-writable settings schema: section -> key -> (type, default)
# Used for validation AND by the settings page to render forms.
SETTINGS_SCHEMA = {
    "capture": {
        "label": "抓帧参数",
        "keys": {
            "target_fps": ("float", 2.0, "目标处理帧率"),
            "reconnect_delay": ("float", 5.0, "重连间隔（秒）"),
            "max_failures": ("int", 5, "连续失败次数阈值"),
            "read_timeout": ("float", 10.0, "连接/读取超时（秒）"),
            "warmup_frames": ("int", 5, "重连后丢弃帧数"),
            "stall_timeout": ("float", 15.0, "无帧卡死判定（秒）"),
        },
        "restart_required": True,
    },
    "snapshot": {
        "label": "快照参数",
        "keys": {
            "save_dir": ("str", "storage/snapshots", "快照保存目录"),
            "jpeg_quality": ("int", 90, "JPEG 质量"),
            "annotate": ("bool", True, "绘制标注"),
            "box_thickness": ("int", 2, "边框线宽"),
            "font_scale": ("float", 0.6, "字体大小"),
            "retention_days": ("int", 30, "快照保留天数"),
        },
        "restart_required": False,
    },
    "alert": {
        "label": "告警参数",
        "keys": {"cooldown_seconds": ("int", 30, "同相机同规则冷却（秒）")},
        "restart_required": False,
    },
    "logging": {
        "label": "日志参数",
        "keys": {
            "level": ("str", "INFO", "日志级别"),
            "file": ("str", "storage/logs/machine_vision.log", "日志文件（改后需重启）"),
            "max_size_mb": ("int", 50, "单文件上限 MB（需重启）"),
            "backup_count": ("int", 5, "轮转份数（需重启）"),
        },
        "restart_required": False,
    },
    "database": {
        "label": "数据库",
        "keys": {
            "path": ("str", "storage/alerts.db", "数据库路径（需重启）"),
            "retention_days": ("int", 180, "告警保留天数"),
        },
        "restart_required": False,
    },
    "panel": {
        "label": "面板",
        "keys": {
            "host": ("str", "0.0.0.0", "监听地址"),
            "port": ("int", 8000, "监听端口"),
            "auth_enabled": ("bool", True, "启用登录认证"),
            "username": ("str", "admin", "认证用户名"),
            "password": ("str", "admin", "认证密码"),
        },
        "restart_required": True,
    },
    "llm": {
        "label": "大模型 (LLM)",
        "keys": {
            "enabled": ("bool", False, "启用 LLM 辅助标注"),
            "base_url": ("str", "https://api.openai.com/v1",
                         "API 地址（OpenAI 兼容）"),
            "api_key": ("str", "", "API Key"),
            "model": ("str", "gpt-4o-mini", "模型名（需支持图片输入）"),
        },
        "restart_required": False,
    },
}

ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _coerce(value, typ):
    if typ == "float":
        return float(value)
    if typ == "int":
        return int(float(value))
    if typ == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    return str(value)


class RuntimeState:
    def __init__(self, config_dir: str, system=None):
        self.config_dir = Path(config_dir).resolve()
        self.system = system  # MachineVisionSystem when embedded, else None
        self._logger = get_logger("panel.state")

        from webapp.config_service import ConfigService

        self.config = ConfigService(self.config_dir)
        self.rules_store = get_rules_store(str(self.config_dir))

        # Model file validation cache: filename -> {status, classes, error, ...}
        self._model_validations: dict = {}
        self._validating: set = set()
        self._load_jobs: dict = {}  # model name -> {status, error}

        # Pending-restart flags, e.g. {"capture": True}
        self._pending_restart: dict = {}
        self._pending_lock = threading.Lock()

        # Standalone mode: build own DB handle + lazy detector for the test bench
        self._db = None
        self._standalone_detector = None
        self._detector_lock = threading.Lock()

        self._ensure_panel_settings()

    # ------------------------------------------------------------------
    # Components (work in both embedded and standalone mode)
    # ------------------------------------------------------------------

    @property
    def db(self):
        if self._db is None:
            from infrastructure.persistence import AlertDatabase

            self._db = AlertDatabase(self.settings())
        return self._db

    def settings(self) -> dict:
        """Live settings: the system's copy when embedded, else from YAML."""
        if self.system is not None:
            return self.system._settings
        return self.config.load("settings.yaml")

    def _ensure_panel_settings(self):
        """Make sure settings.yaml has the panel + retention defaults."""
        current = self.config.load("settings.yaml")
        needs_defaults = any(
            not isinstance(current.get(section), dict)
            or any(key not in (current.get(section) or {})
                   for key in spec["keys"])
            for section, spec in SETTINGS_SCHEMA.items()
        )
        if not needs_defaults:
            return

        def ensure(doc):
            for section, spec in SETTINGS_SCHEMA.items():
                sec = doc.setdefault(section, {})
                if not isinstance(sec, dict):
                    doc[section] = sec = {}
                for key, (typ, default, _desc) in spec["keys"].items():
                    sec.setdefault(key, default)
            return {s: dict(doc[s]) for s in SETTINGS_SCHEMA if s in doc}

        synced = self.config.update_document("settings.yaml", ensure)
        if self.system is not None:
            self.system._settings.update(synced)

    # ------------------------------------------------------------------
    # System info / stats
    # ------------------------------------------------------------------

    def system_info(self) -> dict:
        info = {
            "mode": "embedded" if self.system is not None else "standalone(只读+测试台)",
            "device": None,
            "models": self.models_status(),
            "rules": [
                {"id": r.id, "name": r.name, "enabled": r.enabled}
                for r in self.rules_store.get_all()
            ],
        }
        if self.system is not None:
            info["device"] = self.system._detector.device
            info["uptime"] = round(time.time() - self.system._stats["start_time"], 0)
            info["stats"] = dict(self.system._stats)
        return info

    def stats(self) -> dict:
        if self.system is None:
            return {"frames_processed": 0, "violations_detected": 0,
                    "snapshots_saved": 0, "uptime": 0, "avg_fps": 0,
                    "standalone": True}
        s = self.system._stats
        uptime = time.time() - s["start_time"]
        return {
            "frames_processed": s["frames_processed"],
            "violations_detected": s["violations_detected"],
            "snapshots_saved": s["snapshots_saved"],
            "uptime": round(uptime),
            "avg_fps": round(s["frames_processed"] / uptime, 2) if uptime > 0 else 0,
        }

    def alert_trend(self, days: int = 7) -> list:
        """[{day, total, confirmed, pending, false_positive}] for the dashboard
        chart, zero-filled. pending = not yet reviewed (total - confirmed - fp)."""
        summary = self.db.get_alert_summary(days=days)
        by_day = summary["by_day"]
        out = []
        for i in range(days - 1, -1, -1):
            day = datetime.now().date().toordinal() - i
            d = datetime.fromordinal(day).strftime("%Y-%m-%d")
            e = by_day.get(d, {})
            total = e.get("total", 0)
            confirmed = e.get("confirmed", 0)
            fp = e.get("false_positive", 0)
            out.append({
                "day": d, "total": total, "confirmed": confirmed,
                "pending": max(total - confirmed - fp, 0), "false_positive": fp,
            })
        return out

    # ------------------------------------------------------------------
    # Cameras
    # ------------------------------------------------------------------

    def cameras_status(self) -> list:
        """YAML entries merged with live stream status."""
        manager = self.system._camera_manager if self.system else None
        live = {cid: s.get_panel_status() for cid, s in manager._cameras.items()} \
            if manager else {}
        out = []
        for cfg in self.config.get_cameras():
            cid = cfg.get("id")
            entry = {
                "id": cid,
                "name": cfg.get("name", cid),
                # masked for display; edit form leaves it empty to keep as-is
                "url": self._mask_password(cfg.get("rtsp_url", "")),
                "rules": cfg.get("rules", []),
                "enabled": bool(cfg.get("enabled", True)),
                "in_manager": cid in live,
            }
            if cid in live:
                st = live[cid]
                st.pop("url", None)
                entry.update(st)
            else:
                entry.update({"connected": False, "thread_alive": False,
                              "failures": 0, "frames_captured": 0,
                              "frame_age": None, "source_fps": 0,
                              "is_network": str(cfg.get("rtsp_url", "")).lower()
                              .startswith(("rtsp://", "http://", "https://"))})
            out.append(entry)
        return out

    @staticmethod
    def _mask_password(url: str) -> str:
        return re.sub(r"(://[^:/@]+:)[^@]+(@)", r"\1****\2", url or "")

    @staticmethod
    def _merge_keep_password(new_url: str, old_url: str) -> str:
        """Replace the `:__KEEP__@` placeholder with the stored password, so
        the panel can re-assemble URLs without ever receiving the plaintext."""
        marker = ":__KEEP__@"
        if marker not in new_url or not old_url:
            return new_url
        head, sep, rest = old_url.partition("://")
        at = rest.rfind("@")
        colon = rest.find(":")
        if not sep or at == -1 or colon == -1 or colon > at:
            return new_url
        old_pwd = rest[colon + 1:at]
        i = new_url.find(marker)
        return new_url[:i + 1] + old_pwd + "@" + new_url[i + len(marker):]

    def add_camera(self, data: dict) -> dict:
        cameras = self.config.get_cameras()
        cid = str(data["id"]).strip()
        if not cid:
            raise ValueError("监控 ID 不能为空")
        if any(c.get("id") == cid for c in cameras):
            raise ValueError(f"监控 ID 已存在: {cid}")
        if "__KEEP__@" in str(data.get("rtsp_url", "")):
            raise ValueError("新增监控不能使用“保持原密码”占位，请填写密码")
        if not str(data.get("rtsp_url", "")).strip():
            raise ValueError("rtsp_url 不能为空")
        entry = {
            "id": cid,
            "name": str(data.get("name") or cid),
            "rtsp_url": str(data["rtsp_url"]).strip(),
            "enabled": bool(data.get("enabled", True)),
            "rules": [int(r) for r in data.get("rules", [])],
        }
        cameras.append(entry)
        self.config.save_cameras(cameras)
        self._apply_camera_to_manager(entry)
        return entry

    def update_camera(self, camera_id: str, data: dict) -> dict:
        cameras = self.config.get_cameras()
        target = next((c for c in cameras if c.get("id") == camera_id), None)
        if target is None:
            raise ValueError(f"监控不存在: {camera_id}")

        old_url = target.get("rtsp_url", "")
        url_changed = ("rtsp_url" in data
                       and str(data["rtsp_url"]).strip() != old_url)
        if "name" in data:
            target["name"] = str(data["name"])
        if "rtsp_url" in data and str(data["rtsp_url"]).strip():
            new_url = str(data["rtsp_url"]).strip()
            # 分段组装模式下密码留空 = 保持原密码
            if "__KEEP__@" in new_url and old_url:
                url_changed = self._merge_keep_password(new_url, old_url) != old_url
                new_url = self._merge_keep_password(new_url, old_url)
            target["rtsp_url"] = new_url
        if "rules" in data:
            target["rules"] = [int(r) for r in data["rules"]]
        if "enabled" in data:
            target["enabled"] = bool(data["enabled"])

        self.config.save_cameras(cameras)
        self._apply_camera_to_manager(target, url_changed=url_changed)
        return target

    def delete_camera(self, camera_id: str):
        cameras = self.config.get_cameras()
        remaining = [c for c in cameras if c.get("id") != camera_id]
        if len(remaining) == len(cameras):
            raise ValueError(f"相机不存在: {camera_id}")
        self.config.save_cameras(remaining)
        if self.system is not None:
            self.system._camera_manager.remove_camera(camera_id)

    def restart_camera(self, camera_id: str):
        if self.system is None:
            raise RuntimeError("standalone 模式无法重启相机流")
        manager = self.system._camera_manager
        stream = manager._cameras.get(camera_id)
        if stream is None:
            raise ValueError(f"相机未在运行: {camera_id}")
        manager.remove_camera(camera_id)
        for cfg in self.config.get_cameras():
            if cfg.get("id") == camera_id:
                self._apply_camera_to_manager(cfg, url_changed=True)
                break

    def _apply_camera_to_manager(self, entry: dict, url_changed: bool = False):
        """Mirror a YAML camera entry into the live CameraManager (hot apply)."""
        if self.system is None:
            return
        manager = self.system._camera_manager
        from core.capture import CameraConfig

        cid = entry["id"]
        running = manager._cameras.get(cid)
        cfg_changed = (
            running is None
            or url_changed
            or running.config.name != entry.get("name", cid)
            or list(running.config.rules) != list(entry.get("rules", []))
        )
        if not entry.get("enabled", True):
            if running is not None:
                manager.remove_camera(cid)
            return
        if cfg_changed:
            if running is not None:
                manager.remove_camera(cid)
            manager.add_camera(
                CameraConfig(
                    id=cid,
                    name=entry.get("name", cid),
                    rtsp_url=entry["rtsp_url"],
                    enabled=True,
                    rules=list(entry.get("rules", [])),
                )
            )

    # ------------------------------------------------------------------
    # Settings: read / validate / hot-apply
    # ------------------------------------------------------------------

    def get_settings(self) -> dict:
        """Schema-shaped settings for the settings page."""
        raw = self.config.load("settings.yaml")
        out = {}
        for section, spec in SETTINGS_SCHEMA.items():
            sec = raw.get(section, {}) or {}
            out[section] = {
                "label": spec["label"],
                "restart_required": spec["restart_required"],
                "keys": [
                    {
                        "key": key,
                        "type": typ,
                        "desc": desc,
                        "value": sec.get(key, default),
                    }
                    for key, (typ, default, desc) in spec["keys"].items()
                ],
            }
        return out

    def update_settings(self, section: str, values: dict) -> dict:
        if section not in SETTINGS_SCHEMA:
            raise ValueError(f"未知配置段: {section}")
        spec = SETTINGS_SCHEMA[section]
        clean = {}
        for key, value in values.items():
            if key not in spec["keys"]:
                continue
            typ, default, _ = spec["keys"][key]
            try:
                clean[key] = _coerce(value, typ)
            except (TypeError, ValueError):
                raise ValueError(f"{section}.{key} 类型错误，期望 {typ}")
            if section == "logging" and key == "level" \
                    and str(clean[key]).upper() not in ALLOWED_LOG_LEVELS:
                raise ValueError(f"日志级别需为 {sorted(ALLOWED_LOG_LEVELS)}")

        self.config.update_section("settings.yaml", section, clean)
        restart_required = spec["restart_required"] and bool(clean)
        self._hot_apply_settings(section, clean)

        # keep the system's in-memory copy in sync
        if self.system is not None:
            self.system._settings.setdefault(section, {}).update(clean)
        if not restart_required:
            with self._pending_lock:
                self._pending_restart.pop(section, None)
        else:
            with self._pending_lock:
                self._pending_restart[section] = True
        return {
            "applied": bool(clean),
            "restart_required": restart_required,
            "values": clean,
        }

    def _hot_apply_settings(self, section: str, values: dict):
        if self.system is None or not values:
            return
        sysm = self.system
        if section == "alert" and "cooldown_seconds" in values:
            sysm._analyzer._cooldown = int(values["cooldown_seconds"])
        elif section == "snapshot":
            sm = sysm._snapshot_manager
            if "jpeg_quality" in values:
                sm._jpeg_quality = int(values["jpeg_quality"])
            if "annotate" in values:
                sm._annotate = bool(values["annotate"])
            if "box_thickness" in values:
                sm._box_thickness = int(values["box_thickness"])
            if "font_scale" in values:
                sm._font_scale = float(values["font_scale"])
            if "save_dir" in values and values["save_dir"]:
                sm._save_dir = values["save_dir"]
                os.makedirs(sm._save_dir, exist_ok=True)
        elif section == "logging" and "level" in values:
            import logging

            logging.getLogger().setLevel(str(values["level"]).upper())
        elif section == "capture":
            cap = sysm._camera_manager
            mapping = {
                "target_fps": "_target_fps",
                "reconnect_delay": "_reconnect_delay",
                "max_failures": "_max_failures",
                "read_timeout": "_read_timeout",
                "warmup_frames": "_warmup_frames",
                "stall_timeout": "_stall_timeout",
            }
            for key, attr in mapping.items():
                if key in values:
                    setattr(cap, attr, values[key])
        elif section == "model":
            for m in values.get("models", []) or []:
                self._hot_apply_model_entry(m)

    def _hot_apply_model_entry(self, entry: dict):
        """Sync one model entry (thresholds/enabled) to the live registry."""
        detector = self.system._detector
        name = entry.get("name")
        if not name:
            return
        if "confidence_override" in entry or "confidence_threshold" in entry:
            detector.set_thresholds(
                name, confidence=entry.get(
                    "confidence_override", entry.get("confidence_threshold")
                )
            )
        enabled = entry.get("enabled", True)
        path = entry.get("path", "")
        if enabled and not detector.is_loaded(name) and path \
                and self._model_path(name, path)[0]:
            threading.Thread(
                target=self._load_model_job, args=(name, path),
                name=f"model-load-{name}", daemon=True,
            ).start()
        elif not enabled and detector.is_loaded(name):
            detector.unload_model(name)

    def _load_model_job(self, name: str, path: str):
        self._load_jobs[name] = {"status": "loading"}
        ok = self.system._detector.load_model(name, path)
        self._load_jobs[name] = {
            "status": "loaded" if ok else "error",
            "error": None if ok else self.system._detector._load_errors.get(name),
        }

    def pending_restart(self) -> dict:
        with self._pending_lock:
            return dict(self._pending_restart)

    # ------------------------------------------------------------------
    # Models: files / validation / registry
    # ------------------------------------------------------------------

    def _models_dir(self) -> Path:
        d = PROJECT_ROOT / "models"
        d.mkdir(exist_ok=True)
        return d

    def _model_path(self, name: str, rel_path: str):
        """Resolve a configured model path. Returns (absolute_or_None, display)."""
        cand = Path(rel_path)
        if not cand.is_absolute():
            cand = PROJECT_ROOT / rel_path
        return (cand if cand.exists() else None), str(rel_path)

    def models_status(self) -> list:
        """Registered model instances with runtime status."""
        entries = self.settings().get("model", {}).get("models", [])
        if self.system is not None:
            detector = self.system._detector
        else:
            try:
                detector = self._get_standalone_detector()
            except Exception:
                # standalone without a usable model config: degrade to plain
                # registry info instead of failing every status endpoint
                detector = None
        out = []
        for m in entries:
            name = m.get("name", "unnamed")
            exists, display = self._model_path(name, m.get("path", ""))
            status = {
                "name": name,
                "path": display,
                "file_exists": exists is not None,
                "config_enabled": bool(m.get("enabled", True)),
                "confidence_override": m.get("confidence_override"),
                "load_status": self._load_jobs.get(name, {}).get("status"),
            }
            if detector is not None and detector.is_loaded(name):
                for d in detector.get_status():
                    if d["name"] == name:
                        status.update({
                            "loaded": True,
                            "device": d["device"],
                            "confidence": d["confidence"],
                            "iou": d["iou"],
                            "img_size": d["img_size"],
                            "classes": d["classes"],
                        })
                        break
                else:
                    status["loaded"] = False
            else:
                status.update({"loaded": False, "device": None, "classes": {}})
            out.append(status)
        return out

    def model_files(self) -> list:
        """*.pt files under models/ + registration/validation state."""
        registered = {m.get("path"): m.get("name")
                      for m in self.settings().get("model", {}).get("models", [])}
        files = []
        for f in sorted(self._models_dir().glob("*.pt")):
            rel = f"models/{f.name}"
            val = self._model_validations.get(f.name, {})
            files.append({
                "file": f.name,
                "path": rel,
                "size_mb": round(f.stat().st_size / 1048576, 1),
                "registered_as": registered.get(rel),
                "validation": val or {"status": "未校验"},
            })
        return files

    def upload_model(self, filename: str, content: bytes) -> str:
        if not filename.lower().endswith(".pt"):
            raise ValueError("只允许 .pt 模型文件")
        if len(content) > 200 * 1024 * 1024:
            raise ValueError("文件超过 200MB 上限")
        safe = re.sub(r"[^\w.\-]", "_", Path(filename).name)
        dest = self._models_dir() / safe
        i = 1
        while dest.exists():
            dest = self._models_dir() / f"{Path(safe).stem}_{i}.pt"
            i += 1
        dest.write_bytes(content)
        self.validate_model(dest.name)
        return dest.name

    def validate_model(self, filename: str):
        """Validate a .pt in a background thread (load + extract class table)."""
        path = self._models_dir() / filename
        if not path.exists():
            raise ValueError("文件不存在")
        if filename in self._validating:
            return
        self._validating.add(filename)
        self._model_validations[filename] = {"status": "校验中"}

        def job():
            try:
                from ultralytics import YOLO

                model = YOLO(str(path))
                self._model_validations[filename] = {
                    "status": "有效",
                    "task": getattr(model, "task", "?"),
                    "classes": dict(model.names or {}),
                    "imgsz": list(model.overrides.get("imgsz", []) or []),
                }
            except Exception as e:
                self._model_validations[filename] = {"status": "无效", "error": str(e)}
            finally:
                self._validating.discard(filename)

        threading.Thread(target=job, name=f"validate-{filename}", daemon=True).start()

    def delete_model_file(self, filename: str):
        registered = {m.get("path") for m in
                      self.settings().get("model", {}).get("models", [])}
        if f"models/{filename}" in registered:
            raise ValueError("该文件已被注册的模型引用，请先注销模型")
        path = self._models_dir() / filename
        if path.exists():
            path.unlink()
        self._model_validations.pop(filename, None)

    def register_model(self, name: str, file: str, enabled: bool = False,
                       confidence_override=None):
        if not re.fullmatch(r"[\w\-]+", name or ""):
            raise ValueError("模型名称只允许字母/数字/下划线/连字符")
        def mutate(doc):
            models = doc.setdefault("model", {}).setdefault("models", [])
            if any(m.get("name") == name for m in models):
                raise ValueError(f"模型名称已存在: {name}")
            entry = CommentedMap()
            entry["name"] = name
            entry["path"] = f"models/{file}"
            entry["enabled"] = bool(enabled)
            if confidence_override is not None:
                entry["confidence_override"] = float(confidence_override)
            models.append(entry)
            return dict(entry), models

        entry, models = self.config.update_document("settings.yaml", mutate)
        if self.system is not None:
            self.system._settings.setdefault("model", {})["models"] = models
        if enabled:
            self._hot_apply_model_entry(entry)
        return entry

    def update_model(self, name: str, data: dict):
        def mutate(doc):
            models = doc.get("model", {}).get("models", [])
            target = next((m for m in models if m.get("name") == name), None)
            if target is None:
                raise ValueError(f"模型未注册: {name}")
            if "confidence_override" in data and data["confidence_override"] is not None:
                target["confidence_override"] = float(data["confidence_override"])
            if "enabled" in data:
                target["enabled"] = bool(data["enabled"])
            return dict(target), models

        target, models = self.config.update_document("settings.yaml", mutate)
        if self.system is not None:
            self.system._settings.setdefault("model", {})["models"] = models
        self._hot_apply_model_entry(target)
        return target

    def reload_model(self, name: str):
        if self.system is None:
            raise RuntimeError("standalone 模式不支持热加载")
        target = next((m for m in self.settings().get("model", {}).get("models", [])
                       if m.get("name") == name), None)
        if target is None:
            raise ValueError(f"模型未注册: {name}")
        self.system._detector.unload_model(name)
        exists, _ = self._model_path(name, target.get("path", ""))
        if not exists:
            raise ValueError(f"模型文件不存在: {target.get('path')}")
        threading.Thread(
            target=self._load_model_job, args=(name, target["path"]),
            name=f"model-load-{name}", daemon=True,
        ).start()

    def unregister_model(self, name: str):
        def mutate(doc):
            models = doc.get("model", {}).get("models", [])
            remaining = [m for m in models if m.get("name") != name]
            if len(remaining) == len(models):
                raise ValueError(f"模型未注册: {name}")
            doc["model"]["models"] = remaining
            return remaining

        remaining = self.config.update_document("settings.yaml", mutate)
        if self.system is not None:
            self.system._settings.setdefault("model", {})["models"] = remaining
            self.system._detector.unload_model(name)

    # ------------------------------------------------------------------
    # Rules CRUD (rules.yaml via shared RulesStore)
    # ------------------------------------------------------------------

    def rules_list(self) -> list:
        rules = self.rules_store.get_all()
        camera_usage = {}
        for cam in self.config.get_cameras():
            for rid in cam.get("rules", []) or []:
                camera_usage.setdefault(int(rid), []).append(cam.get("id"))
        model_classes = {}
        for m in self.models_status():
            model_classes[m["name"]] = [
                str(v).lower() for v in (m.get("classes") or {}).values()
            ]
        # param names whose classes must exist in the bound models
        from_model_keys = {
            p["name"] for spec in self.template_specs().values()
            for p in spec["params"] if p.get("from_model")
        }
        out = []
        for r in rules:
            entry = {
                "id": r.id, "name": r.name, "description": r.description,
                "template": r.template, "models": r.models, "params": r.params,
                "severity": r.severity, "enabled": r.enabled,
                "cameras": camera_usage.get(r.id, []),
                # 画布规则的节点图（老规则为 None），编辑时回填画布编辑器
                "graph": r.graph or None,
            }
            warnings = []
            bound_classes = set()
            for model_name in r.models:
                classes = model_classes.get(model_name)
                if classes is None:
                    if self.system is not None or \
                            self._standalone_detector is not None:
                        warnings.append(f"模型未加载: {model_name}")
                    continue
                bound_classes.update(classes)
            if r.models and bound_classes:
                used = set()
                for key, val in (r.params or {}).items():
                    if key in from_model_keys and isinstance(val, list):
                        used.update(str(x).lower() for x in val)
                missing = [c for c in used if c not in bound_classes]
                if missing:
                    warnings.append(
                        f"绑定模型缺少类别: {', '.join(missing)}"
                    )
            entry["warnings"] = warnings
            out.append(entry)
        return out

    def add_rule(self, data: dict) -> dict:
        from rules.rules_engine import RuleDefinition

        template = data.get("template")
        if template not in self.template_specs():
            raise ValueError(f"未知模板: {template}")
        rule = RuleDefinition(
            id=int(data.get("id") or self.rules_store.next_free_id()),
            name=str(data.get("name") or f"rule_{data.get('id')}"),
            description=str(data.get("description", "")),
            template=template,
            models=[str(m) for m in data.get("models", [])],
            params=dict(data.get("params", {}) or {}),
            graph=dict(data.get("graph", {}) or {}),
            severity=int(data.get("severity", 2)),
            enabled=bool(data.get("enabled", True)),
        )
        self.rules_store.add(rule)
        return {"id": rule.id}

    def update_rule(self, rule_id: int, fields: dict) -> dict:
        self.rules_store.update(rule_id, fields)
        return {"id": rule_id}

    def delete_rule(self, rule_id: int):
        usage = [c["id"] for c in self.config.get_cameras()
                 if rule_id in (c.get("rules", []) or [])]
        if usage:
            raise ValueError(f"规则仍被相机使用: {', '.join(usage)}，请先移除引用")
        self.rules_store.delete(rule_id)

    def template_specs(self) -> dict:
        from rules.rules_engine import get_template_store

        return get_template_store(str(self.config_dir)).specs()

    def node_types(self) -> dict:
        """可视化画布节点注册表（画布编辑器据此生成交互与参数表单）。"""
        from core.rules_graph import NODE_TYPES

        return copy.deepcopy(NODE_TYPES)

    def template_logics(self) -> dict:
        from rules.rules_engine import get_template_logics

        return get_template_logics()

    def create_template(self, data: dict) -> dict:
        from rules.rules_engine import get_template_store

        name = str(data.get("name") or "").strip()
        get_template_store(str(self.config_dir)).add(
            name,
            {"label": data.get("label"), "logic": data.get("logic"),
             "params": data.get("params") or []},
        )
        return {"name": name}

    def update_template(self, name: str, data: dict) -> dict:
        from rules.rules_engine import get_template_store

        get_template_store(str(self.config_dir)).update(
            name,
            {"label": data.get("label"), "logic": data.get("logic"),
             "params": data.get("params") or []},
        )
        return {"name": name}

    def delete_template(self, name: str):
        from rules.rules_engine import get_template_store

        usage = [f"R{r.id}" for r in self.rules_store.get_all()
                 if r.template == name]
        if usage:
            raise ValueError(f"模板仍被规则使用: {', '.join(usage)}，请先删除或改绑对应规则")
        get_template_store(str(self.config_dir)).delete(name)

    # ------------------------------------------------------------------
    # Snapshots / storage
    # ------------------------------------------------------------------

    def snapshots_dir(self) -> Path:
        save_dir = self.settings().get("snapshot", {}).get("save_dir",
                                                          "storage/snapshots")
        d = Path(save_dir)
        if not d.is_absolute():
            d = PROJECT_ROOT / d
        return d

    @staticmethod
    def _snapshot_day_dirs(base: Path) -> list:
        """Day directories only — skips derived caches like .thumbs/."""
        if not base.exists():
            return []
        return sorted([d for d in base.iterdir() if d.is_dir()
                       and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)],
                      reverse=True)

    def _flat_rule_label(self, img_name: str, id2name: dict) -> str:
        """Legacy flat layout keeps no rule folder; the rule id is encoded
        in the file name (CAM*_R01_HHMMSS.jpg). Map it back to rule name."""
        m = re.match(r".*_R(\d+)_", img_name)
        if m:
            rid = int(m.group(1))
            return id2name.get(rid, f"R{m.group(1).zfill(2)}")
        return "未分类"

    def list_snapshots(self, date=None, from_date=None, to_date=None, rule=None,
                       camera=None, limit=200, offset=0) -> dict:
        """Snapshot listing filtered by day range / rule / camera.

        A day holds rule subdirectories (current layout) or flat jpgs
        (legacy writer, rule encoded in the file name). Returns per-day
        summaries plus one flat, mtime-desc page so paging works across
        a multi-day range."""
        base = self.snapshots_dir()
        id2name = None
        days = []
        files = []
        total_size = 0
        for day_dir in self._snapshot_day_dirs(base):
            if date and day_dir.name != date:
                continue
            if from_date and day_dir.name < from_date:
                continue
            if to_date and day_dir.name > to_date:
                continue
            day_files = []

            def add(img, rule_label, rel):
                nonlocal total_size
                if camera and not img.name.startswith(f"{camera}_"):
                    return
                st = img.stat()
                day_files.append({
                    "url": f"/snapshots/{rel}",
                    "thumb": f"/api/snapshots/thumb?p={quote(rel)}&w=420",
                    "name": img.name,
                    "camera": img.name.split("_R")[0],
                    "rule_dir": rule_label,
                    "date": day_dir.name,
                    "size_kb": round(st.st_size / 1024),
                    "mtime": int(st.st_mtime),
                })
                total_size += st.st_size

            for rule_dir in sorted(day_dir.iterdir()):
                if not rule_dir.is_dir():
                    continue
                if rule and rule_dir.name != rule:
                    continue
                for img in rule_dir.glob("*.jpg"):
                    add(img, rule_dir.name, f"{day_dir.name}/{rule_dir.name}/{img.name}")
            for img in day_dir.glob("*.jpg"):
                if id2name is None:
                    id2name = {r.id: r.name for r in self.rules_store.get_all()}
                label = self._flat_rule_label(img.name, id2name)
                if rule and label != rule:
                    continue
                add(img, label, f"{day_dir.name}/{img.name}")
            if day_files:
                days.append({
                    "date": day_dir.name,
                    "count": len(day_files),
                    "size_mb": round(sum(f["size_kb"] for f in day_files) / 1024, 1),
                })
                files.extend(day_files)
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return {
            "dates": days,
            "files": files[offset:offset + limit],
            "total": len(files),
            "total_size_mb": round(total_size / 1048576, 1),
            "offset": offset,
            "limit": limit,
        }

    def storage_usage(self) -> dict:
        base = self.snapshots_dir()
        per_day = []
        total = 0
        for day_dir in self._snapshot_day_dirs(base):
            size = sum(f.stat().st_size for f in day_dir.rglob("*.jpg"))
            count = len(list(day_dir.rglob("*.jpg")))
            total += size
            per_day.append({"date": day_dir.name, "count": count,
                            "size_mb": round(size / 1048576, 1)})
        usage = {"snapshots_total_mb": round(total / 1048576, 1),
                 "per_day": per_day[:30]}
        # shutil.disk_usage works consistently on Windows and POSIX.
        disk = shutil.disk_usage(PROJECT_ROOT)
        usage["disk_total_gb"] = round(disk.total / 1073741824, 1)
        usage["disk_free_gb"] = round(disk.free / 1073741824, 1)
        used_pct = disk.used / disk.total if disk.total else 0
        usage["disk_used_pct"] = round(used_pct * 100, 1)
        usage["watermark"] = ("red" if used_pct > 0.9
                              else "yellow" if used_pct > 0.8 else "ok")
        return usage

    def cleanup_snapshots(self, before_date: str) -> int:
        """Delete snapshot day-directories strictly before before_date."""
        base = self.snapshots_dir()
        deleted = 0
        if not base.exists():
            return 0
        import shutil

        for day_dir in self._snapshot_day_dirs(base):
            if day_dir.name < before_date:
                shutil.rmtree(day_dir, ignore_errors=True)
                # drop matching thumbnail-cache copies
                thumbs = base / ".thumbs"
                if thumbs.is_dir():
                    for wdir in thumbs.iterdir():
                        if wdir.is_dir():
                            shutil.rmtree(wdir / day_dir.name, ignore_errors=True)
                deleted += 1
        return deleted

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def tail_logs(self, tail: int = 500, level: str = None) -> list:
        log_file = self.settings().get("logging", {}).get("file",
                                                          "storage/logs/machine_vision.log")
        path = Path(log_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = lines[-int(tail):]
        if level:
            lines = [l for l in lines if f"[{level.upper()}]" in l]
        return lines

    # ------------------------------------------------------------------
    # Detection test bench
    # ------------------------------------------------------------------

    def _get_standalone_detector(self):
        if self.system is not None:
            return self.system._detector
        if self._standalone_detector is None:
            with self._detector_lock:
                if self._standalone_detector is None:
                    from core.detector import MultiDetector

                    self._standalone_detector = MultiDetector(self.settings())
        return self._standalone_detector

    def run_detection_test(
        self, image_bgr, model_names=None, conf=None, iou=None
    ) -> dict:
        """Single-flight image detection through the live model registry."""
        return self._test_service().run(image_bgr, model_names, conf, iou)

    def _test_service(self):
        if getattr(self, "_detect_test", None) is None:
            from webapp.detect_service import DetectTestService

            self._detect_test = DetectTestService(self)
        return self._detect_test

    def recent_test_results(self) -> list:
        return self._test_service().recent()

    # ------------------------------------------------------------------
    # Datasets / training (lazy singletons)
    # ------------------------------------------------------------------

    @property
    def datasets(self):
        if getattr(self, "_dataset_service", None) is None:
            from webapp.dataset_service import DatasetService

            self._dataset_service = DatasetService(self)
        return self._dataset_service

    @property
    def trainer(self):
        if getattr(self, "_train_service", None) is None:
            from webapp.train_service import TrainService

            self._train_service = TrainService(self)
        return self._train_service

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def run_retention(self) -> dict:
        from webapp.retention import run_retention

        return run_retention(self)
