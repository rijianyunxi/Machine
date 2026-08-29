"""
YAML config read/write with ruamel round-trip (comments preserved) + backup.

All panel config writes go through ConfigService so every mutation:
  1. validates against a small schema
  2. backs up the previous file (config/<name>.yaml.bak, rolling 10)
  3. writes back with comments intact
Runtime hot-apply lives in RuntimeState (which owns the live objects).
"""

import shutil
import threading
from pathlib import Path

from ruamel.yaml import YAML

MAX_BACKUPS = 10


class ConfigService:
    def __init__(self, config_dir):
        self.config_dir = Path(config_dir)
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        self._lock = threading.Lock()

    # ---------- generic helpers ----------

    def _path(self, name: str) -> Path:
        return self.config_dir / name

    def load(self, name: str) -> dict:
        with self._lock:
            data = self._yaml.load(self._path(name).open("r", encoding="utf-8"))
            return data if isinstance(data, dict) else {}

    def save(self, name: str, data: dict):
        """Backup current file, then write the full document back."""
        path = self._path(name)
        if path.exists():
            self._rotate_backup(path)
        with self._lock:
            self._yaml.dump(data, path.open("w", encoding="utf-8"))

    @staticmethod
    def _rotate_backup(path: Path):
        for i in range(MAX_BACKUPS - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".bak{i}" if i > 1 else ".bak")
            dst = path.with_suffix(path.suffix + f".bak{i + 1}")
            if src.exists():
                shutil.move(str(src), str(dst))
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    def update_section(self, name: str, section: str, values: dict) -> dict:
        """Merge ``values`` into settings[name][section], write, return new section."""
        doc = self.load(name)
        sec = doc.get(section)
        if not isinstance(sec, dict):
            sec = {}
            doc[section] = sec
        sec.update(values)
        self.save(name, doc)
        return dict(sec)

    # ---------- cameras ----------

    def get_cameras(self) -> list:
        return list(self.load("cameras.yaml").get("cameras", []))

    def save_cameras(self, cameras: list):
        """Rewrite the cameras list. When the current document is a CommentedMap
        the comment header above `cameras:` is preserved; the entries themselves
        are regenerated."""
        doc = self.load("cameras.yaml")
        from ruamel.yaml.comments import CommentedMap, CommentedSeq

        entries = CommentedSeq()
        for c in cameras:
            m = CommentedMap()
            for k in ("id", "name", "rtsp_url", "enabled", "rules"):
                if k in c and c[k] not in (None, ""):
                    m[k] = c[k]
            entries.append(m)
        doc["cameras"] = entries
        self.save("cameras.yaml", doc)
