"""
YAML config read/write with ruamel round-trip (comments preserved) + backup.

All panel config writes go through ConfigService so every mutation:
  1. validates against a small schema
  2. backs up the previous file (config/<name>.yaml.bak, rolling 10)
  3. writes back with comments intact
Runtime hot-apply lives in RuntimeState (which owns the live objects).
"""

import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML

MAX_BACKUPS = 10


class ConfigService:
    def __init__(self, config_dir):
        self.config_dir = Path(config_dir)
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        # A settings update is a read-modify-write transaction. RLock lets
        # the helpers share one lock without deadlocking while keeping each
        # document mutation serialized in this process.
        self._lock = threading.RLock()

    # ---------- generic helpers ----------

    def _path(self, name: str) -> Path:
        return self.config_dir / name

    def load(self, name: str) -> dict:
        with self._lock:
            return self._load_unlocked(name)

    def _load_unlocked(self, name: str) -> dict:
        with self._path(name).open("r", encoding="utf-8") as stream:
            data = self._yaml.load(stream)
        return data if isinstance(data, dict) else {}

    def save(self, name: str, data: dict):
        """Backup current file, then write the full document back."""
        with self._lock:
            self._save_unlocked(name, data)

    def _save_unlocked(self, name: str, data: dict):
        path = self._path(name)
        if path.exists():
            self._rotate_backup(path)

        # Dump to a sibling temp file and replace the target only after the
        # complete YAML document is on disk, so a crash cannot leave a partial
        # settings.yaml that appears to reset sections.
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as stream:
                self._yaml.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    @staticmethod
    def _rotate_backup(path: Path):
        for i in range(MAX_BACKUPS - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".bak{i}" if i > 1 else ".bak")
            dst = path.with_suffix(path.suffix + f".bak{i + 1}")
            if src.exists():
                shutil.move(str(src), str(dst))
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    def update_document(self, name: str, mutate: Callable[[dict], Any]):
        """Atomically load, mutate, backup, and save one YAML document.

        Without this transaction boundary, two panel requests can both load
        the same old settings document and the later save silently discard
        the first request (including the LLM section).
        """
        with self._lock:
            doc = self._load_unlocked(name)
            result = mutate(doc)
            self._save_unlocked(name, doc)
            return result

    def update_section(self, name: str, section: str, values: dict) -> dict:
        """Merge ``values`` into settings[name][section], write, return new section."""
        def mutate(doc):
            sec = doc.get(section)
            if not isinstance(sec, dict):
                sec = {}
                doc[section] = sec
            sec.update(values)
            return dict(sec)

        return self.update_document(name, mutate)

    # ---------- cameras ----------

    def get_cameras(self) -> list:
        return list(self.load("cameras.yaml").get("cameras", []))

    def save_cameras(self, cameras: list):
        """Rewrite the cameras list. When the current document is a CommentedMap
        the comment header above `cameras:` is preserved; the entries themselves
        are regenerated."""
        from ruamel.yaml.comments import CommentedMap, CommentedSeq

        def mutate(doc):
            entries = CommentedSeq()
            for c in cameras:
                m = CommentedMap()
                for k in ("id", "name", "rtsp_url", "enabled", "rules"):
                    if k in c and c[k] not in (None, ""):
                        m[k] = c[k]
                entries.append(m)
            doc["cameras"] = entries

        self.update_document("cameras.yaml", mutate)
