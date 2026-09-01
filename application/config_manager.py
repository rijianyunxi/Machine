"""Application service coordinating repository writes and runtime snapshots."""

from __future__ import annotations

import threading
from typing import Callable

from .config_snapshot import ConfigSnapshot
from infrastructure.persistence.config_repository import ConfigRepository


class ConfigManager:
    def __init__(self, repository: ConfigRepository):
        self.repository = repository
        self._lock = threading.RLock()
        self._listeners: list[Callable[[ConfigSnapshot], None]] = []
        self._snapshot = self._build_snapshot()

    def _build_snapshot(self) -> ConfigSnapshot:
        revision, settings, cameras, rules, templates = (
            self.repository.read_snapshot_data()
        )
        return ConfigSnapshot.build(revision, settings, cameras, rules, templates)

    @property
    def snapshot(self) -> ConfigSnapshot:
        with self._lock:
            return self._snapshot


    def subscribe(self, callback: Callable[[ConfigSnapshot], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def refresh(self) -> ConfigSnapshot:
        with self._lock:
            snapshot = self._build_snapshot()
            self._snapshot = snapshot
            listeners = list(self._listeners)
        for callback in listeners:
            callback(snapshot)
        return snapshot

    def refresh_if_changed(self) -> ConfigSnapshot:
        """Refresh once when another process committed a newer revision."""
        current_revision = self.repository.current_revision()
        with self._lock:
            if current_revision == self._snapshot.revision:
                return self._snapshot
        return self.refresh()


    def ensure_defaults(self, defaults: dict) -> ConfigSnapshot:
        self.repository.ensure_settings_defaults(defaults)
        return self.refresh()

    def update_settings(self, section: str, values: dict, expected_revision: int | None = None):
        result = self.repository.update_section(
            section, values, expected_revision=expected_revision
        )
        snapshot = self.refresh()
        return result[0], snapshot

    def save_cameras(self, cameras: list[dict], *, expected_revisions: dict[str, int] | None = None) -> ConfigSnapshot:
        self.repository.save_cameras(cameras, expected_revisions=expected_revisions)
        return self.refresh()

    def replace_models(self, models: list[dict], expected_revision: int | None = None):
        result, revision = self.repository.replace_models(models, expected_revision=expected_revision)
        snapshot = self.refresh()
        return result, revision, snapshot

    def create_model(self, model: dict):
        result = self.repository.create_model(model)
        snapshot = self.refresh()
        return result, snapshot

    def update_model(self, name: str, fields: dict, expected_revision: int | None = None):
        result = self.repository.update_model(name, fields, expected_revision=expected_revision)
        snapshot = self.refresh()
        return result, snapshot

    def delete_model(self, name: str, expected_revision: int | None = None):
        self.repository.delete_model(name, expected_revision=expected_revision)
        return self.refresh()

    def add_rule(self, rule: dict):
        result, revision = self.repository.add_rule(rule)
        snapshot = self.refresh()
        return result, revision, snapshot

    def update_rule(self, rule_id: int, fields: dict, expected_revision: int | None = None):
        result, revision = self.repository.update_rule(rule_id, fields, expected_revision=expected_revision)
        snapshot = self.refresh()
        return result, revision, snapshot

    def delete_rule(self, rule_id: int, expected_revision: int | None = None):
        self.repository.delete_rule(rule_id, expected_revision=expected_revision)
        return self.refresh()

    def create_template(self, code: str, spec: dict):
        result = self.repository.create_template(code, spec)
        snapshot = self.refresh()
        return result, snapshot

    def update_template(self, code: str, spec: dict, expected_revision: int | None = None):
        result = self.repository.update_template(code, spec, expected_revision=expected_revision)
        snapshot = self.refresh()
        return result, snapshot

    def delete_template(self, code: str, expected_revision: int | None = None):
        self.repository.delete_template(code, expected_revision=expected_revision)
        return self.refresh()
