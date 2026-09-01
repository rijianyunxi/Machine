"""Repository for configuration data stored in the unified machine database."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

from .machine_database import MachineDatabase
from utils.passwords import hash_password, is_password_hash


class ConfigRepository:
    """Persistence boundary for settings, cameras, models and rule data."""

    def __init__(self, database: MachineDatabase):
        self.database = database

    def current_revision(self) -> int:
        return self.database.current_revision()

    def get_settings(self) -> dict:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT section, value_json FROM settings_sections ORDER BY section"
            ).fetchall()
        result = {}
        for row in rows:
            try:
                value = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                value = {}
            result[row[0]] = value if isinstance(value, dict) else {}
        # The model registry is normalized as a table, while the detector still
        # consumes the legacy settings shape. Rebuild that shape from the table.
        models = self.get_models()
        if models:
            result.setdefault("model", {})["models"] = models
        result.setdefault("database", {})["path"] = "storage/machine.db"
        return result

    def read_snapshot_data(self) -> tuple[int, dict, list[dict], list, dict[str, dict]]:
        """Read all runtime configuration from one SQLite read transaction."""
        from rules.definitions import RuleDefinition

        with self.database.connection() as conn:
            conn.execute("BEGIN")
            try:
                revision_row = conn.execute(
                    "SELECT global_revision FROM config_meta WHERE id = 1"
                ).fetchone()
                revision = int(revision_row[0]) if revision_row else 1
                setting_rows = conn.execute(
                    "SELECT section, value_json FROM settings_sections ORDER BY section"
                ).fetchall()
                model_rows = conn.execute(
                    "SELECT name, file_path, enabled, confidence_override, revision FROM models ORDER BY id"
                ).fetchall()
                camera_rows = conn.execute(
                    "SELECT id, name, source_uri, config_json, enabled, revision, deleted_at "
                    "FROM cameras WHERE deleted_at IS NULL ORDER BY created_at, id"
                ).fetchall()
                camera_rule_rows = conn.execute(
                    "SELECT camera_id, rule_id, params_override_json FROM camera_rules "
                    "WHERE enabled = 1 ORDER BY camera_id, rule_id"
                ).fetchall()
                template_rows = conn.execute(
                    "SELECT code, name, executor_type, params_schema_json, revision "
                    "FROM rule_templates ORDER BY code"
                ).fetchall()
                rule_rows = conn.execute(
                    "SELECT id, name, description, category, template_code, params_json, "
                    "graph_json, severity, enabled, revision FROM rules ORDER BY id"
                ).fetchall()
                rule_model_rows = conn.execute(
                    "SELECT rm.rule_id, m.name FROM rule_models rm "
                    "JOIN models m ON m.id = rm.model_id "
                    "ORDER BY rm.rule_id, rm.sort_order, m.name"
                ).fetchall()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        models = []
        for row in model_rows:
            item = {"name": row[0], "path": row[1], "enabled": bool(row[2])}
            if row[3] is not None:
                item["confidence_override"] = row[3]
            item["revision"] = int(row[4])
            models.append(item)

        settings = {}
        for row in setting_rows:
            try:
                value = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                value = {}
            settings[str(row[0])] = value if isinstance(value, dict) else {}
        if models:
            settings.setdefault("model", {})["models"] = copy.deepcopy(models)
        settings.setdefault("database", {})["path"] = "storage/machine.db"

        rules_by_camera: dict[str, list[int]] = {}
        overrides_by_camera: dict[str, dict[str, dict]] = {}
        for row in camera_rule_rows:
            camera_id, rule_id = str(row[0]), int(row[1])
            rules_by_camera.setdefault(camera_id, []).append(rule_id)
            try:
                override = json.loads(row[2]) or {}
            except (TypeError, json.JSONDecodeError):
                override = {}
            if isinstance(override, dict) and override:
                overrides_by_camera.setdefault(camera_id, {})[str(rule_id)] = override

        cameras = []
        for row in camera_rows:
            try:
                extra = json.loads(row[3]) or {}
            except (TypeError, json.JSONDecodeError):
                extra = {}
            item = {"id": row[0], "name": row[1], "rtsp_url": row[2],
                    "enabled": bool(row[4]), "revision": int(row[5])}
            if isinstance(extra, dict):
                item.update(extra)
            item.update({"id": row[0], "name": row[1], "rtsp_url": row[2],
                         "enabled": bool(row[4]), "revision": int(row[5]),
                         "rules": rules_by_camera.get(str(row[0]), []),
                         "rule_overrides": overrides_by_camera.get(str(row[0]), {})})
            cameras.append(item)

        templates = {}
        for row in template_rows:
            try:
                params = json.loads(row[3]) or []
            except (TypeError, json.JSONDecodeError):
                params = []
            templates[str(row[0])] = {"label": str(row[1]), "logic": str(row[2]),
                                      "params": params if isinstance(params, list) else [],
                                      "revision": int(row[4])}

        models_by_rule: dict[int, list[str]] = {}
        for row in rule_model_rows:
            models_by_rule.setdefault(int(row[0]), []).append(str(row[1]))
        rules = []
        for row in rule_rows:
            try:
                params = json.loads(row[5]) or {}
            except (TypeError, json.JSONDecodeError):
                params = {}
            try:
                graph = json.loads(row[6]) if row[6] else {}
            except (TypeError, json.JSONDecodeError):
                graph = {}
            rules.append(RuleDefinition(
                id=int(row[0]), name=str(row[1]), description=str(row[2] or ""),
                category=str(row[3] or "ppe"), template=str(row[4]),
                models=models_by_rule.get(int(row[0]), []),
                params=params if isinstance(params, dict) else {},
                graph=graph if isinstance(graph, dict) else {},
                severity=int(row[7] if row[7] is not None else 2),
                enabled=bool(row[8]), revision=int(row[9])))
        return revision, settings, cameras, rules, templates

    def get_section(self, section: str) -> dict:
        return self.get_settings().get(section, {})

    def export_public_config(self) -> dict:
        settings = copy.deepcopy(self.get_settings())
        for section, keys in (("llm", ("api_key",)), ("panel", ("password",))):
            values = settings.get(section)
            if not isinstance(values, dict):
                continue
            for key in keys:
                if key in values:
                    values[f"{key}_configured"] = bool(values.get(key))
                    values.pop(key, None)
        cameras = []
        for camera in self.get_cameras():
            item = copy.deepcopy(camera)
            item["rtsp_url"] = self._redact_camera(item).get("rtsp_url", "")
            cameras.append(item)
        return {
            "format": "machine-public-config",
            "format_version": 1,
            "database_revision": self.current_revision(),
            "settings": settings,
            "models": copy.deepcopy(self.get_models()),
            "cameras": cameras,
            "templates": copy.deepcopy(self.get_templates()),
            "rules": [self._rule_payload(rule) for rule in self.get_rules()],
        }

    def get_section_revisions(self) -> dict[str, int]:
        """Return per-section optimistic-concurrency revisions."""
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT section, revision FROM settings_sections"
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def update_section(
        self,
        section: str,
        values: dict,
        *,
        expected_revision: int | None = None,
        actor: str = "panel",
    ) -> tuple[dict, int]:
        if not isinstance(values, dict):
            raise ValueError("配置内容必须是对象")
        with self.database.transaction() as conn:
            current = conn.execute(
                "SELECT value_json, revision FROM settings_sections WHERE section = ?",
                (section,),
            ).fetchone()
            current_value = {}
            object_revision = 1
            if current:
                try:
                    current_value = json.loads(current[0]) or {}
                except json.JSONDecodeError:
                    current_value = {}
                object_revision = int(current[1])
            if expected_revision is not None and object_revision != int(expected_revision):
                raise RevisionConflict(section, object_revision)
            merged = copy.deepcopy(current_value)
            merged.update(copy.deepcopy(values))
            now = int(time.time())
            new_object_revision = object_revision + 1 if current else 1
            conn.execute(
                """
                INSERT INTO settings_sections
                    (section, value_json, revision, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(section) DO UPDATE SET
                    value_json = excluded.value_json,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (section, _json(merged), new_object_revision, now, actor),
            )
            revision = self.database.bump_revision(conn)
            self._audit(conn, "settings", section, current_value, merged, actor, revision)
        return merged, revision

    def ensure_settings_defaults(self, defaults: dict, actor: str = "system") -> dict:
        """Insert missing defaults without overwriting existing values."""
        if not defaults:
            return self.get_settings()
        with self.database.transaction() as conn:
            changed = False
            now = int(time.time())
            for section, values in defaults.items():
                if not isinstance(values, dict):
                    continue
                row = conn.execute(
                    "SELECT value_json, revision FROM settings_sections WHERE section = ?",
                    (section,),
                ).fetchone()
                current = {}
                object_revision = 1
                if row:
                    try:
                        current = json.loads(row[0]) or {}
                    except json.JSONDecodeError:
                        current = {}
                    object_revision = int(row[1])
                merged = copy.deepcopy(current)
                for key, value in values.items():
                    if key not in merged:
                        merged[key] = copy.deepcopy(value)
                        changed = True
                if merged != current:
                    conn.execute(
                        """
                        INSERT INTO settings_sections
                            (section, value_json, revision, updated_at, updated_by)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(section) DO UPDATE SET
                            value_json = excluded.value_json,
                            revision = excluded.revision,
                            updated_at = excluded.updated_at,
                            updated_by = excluded.updated_by
                        """,
                        (section, _json(merged), object_revision + (1 if row else 0), now, actor),
                    )
            if changed:
                self.database.bump_revision(conn)
        return self.get_settings()

    def get_cameras(self, include_deleted: bool = False) -> list[dict]:
        query = (
            "SELECT id, name, source_uri, config_json, enabled, revision, deleted_at "
            "FROM cameras"
        )
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY created_at, id"
        with self.database.connection() as conn:
            rows = conn.execute(query).fetchall()
            rule_rows = conn.execute(
                "SELECT camera_id, rule_id, params_override_json FROM camera_rules "
                "WHERE enabled = 1 ORDER BY camera_id, rule_id"
            ).fetchall()
        rules_by_camera: dict[str, list[int]] = {}
        overrides_by_camera: dict[str, dict[str, dict]] = {}
        for rule_row in rule_rows:
            camera_id = str(rule_row[0])
            rule_id = int(rule_row[1])
            rules_by_camera.setdefault(camera_id, []).append(rule_id)
            try:
                override = json.loads(rule_row[2]) or {}
            except (TypeError, json.JSONDecodeError):
                override = {}
            if isinstance(override, dict) and override:
                overrides_by_camera.setdefault(camera_id, {})[str(rule_id)] = override
        result = []
        for row in rows:
            try:
                extra = json.loads(row[3]) or {}
            except (TypeError, json.JSONDecodeError):
                extra = {}
            item = {
                "id": row[0],
                "name": row[1],
                "rtsp_url": row[2],
                "enabled": bool(row[4]),
                "revision": int(row[5]),
            }
            item.update(extra)
            item["id"] = row[0]
            item["name"] = row[1]
            item["rtsp_url"] = row[2]
            item["enabled"] = bool(row[4])
            item["revision"] = int(row[5])
            # The relational association is authoritative; do not trust a
            # stale compatibility copy in config_json.
            camera_key = str(row[0])
            item["rules"] = rules_by_camera.get(camera_key, [])
            item["rule_overrides"] = overrides_by_camera.get(camera_key, {})
            if row[6] is not None:
                item["deleted_at"] = row[6]
            result.append(item)
        return result

    def save_cameras(self, cameras: list[dict], actor: str = "panel", *,
                     expected_revisions: dict[str, int] | None = None) -> int:
        """Replace active camera definitions and associations atomically."""
        incoming: dict[str, dict] = {}
        for camera in cameras:
            cid = str(camera.get("id", "")).strip()
            if not cid:
                raise ValueError("监控 ID 不能为空")
            if cid in incoming:
                raise ValueError(f"监控 ID 重复: {cid}")
            incoming[cid] = copy.deepcopy(camera)
        existing = {c["id"]: c for c in self.get_cameras(include_deleted=True)}
        expected_revisions = {str(k): int(v) for k, v in (expected_revisions or {}).items()}
        for entry in incoming.values():
            if entry.get("expected_revision") is not None:
                expected_revisions[str(entry["id"])] = int(entry["expected_revision"])
        audit_entries = []
        with self.database.transaction() as conn:
            now = int(time.time())
            changed = False
            # Check optimistic-concurrency tokens while holding the write lock.
            for cid, expected in expected_revisions.items():
                row = conn.execute("SELECT revision FROM cameras WHERE id = ?", (cid,)).fetchone()
                current_revision = int(row[0]) if row else 0
                if current_revision != expected:
                    raise RevisionConflict(f"camera:{cid}", current_revision)
            for cid, entry in incoming.items():
                source_uri = str(entry.get("rtsp_url", "")).strip()
                if not source_uri:
                    raise ValueError(f"监控 {cid} 的 rtsp_url 不能为空")
                rules = []
                for rule_id in entry.get("rules", []) or []:
                    try:
                        rid = int(rule_id)
                    except (TypeError, ValueError):
                        raise ValueError(f"监控 {cid} 的规则 ID 无效: {rule_id}")
                    if rid not in rules:
                        rules.append(rid)
                raw_overrides = entry.get("rule_overrides", {}) or {}
                if not isinstance(raw_overrides, dict):
                    raise ValueError(f"摄像头 {cid} 的 rule_overrides 必须是对象")
                rule_specs = {}
                if rules:
                    placeholders = ",".join("?" for _ in rules)
                    rows = conn.execute(
                        "SELECT r.id, r.template_code, t.name, t.executor_type, "
                        "t.params_schema_json FROM rules r "
                        "JOIN rule_templates t ON t.code = r.template_code "
                        f"WHERE r.id IN ({placeholders})",
                        tuple(rules),
                    ).fetchall()
                    rule_specs = {int(row[0]): {
                        "label": str(row[2]), "logic": str(row[3]),
                        "params": json.loads(row[4]) if row[4] else [],
                    } for row in rows}
                    missing_rules = [rid for rid in rules if rid not in rule_specs]
                    if missing_rules:
                        raise ValueError(f"摄像头 {cid} 引用了不存在的规则: {missing_rules}")
                rule_overrides = {}
                for raw_rule_id, override in raw_overrides.items():
                    try:
                        override_id = int(raw_rule_id)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"摄像头 {cid} 的覆盖规则 ID 无效: {raw_rule_id}") from exc
                    if override_id not in rules:
                        raise ValueError(f"摄像头 {cid} 的覆盖规则未分配: {override_id}")
                    if not isinstance(override, dict):
                        raise ValueError(f"摄像头 {cid} 的规则 {override_id} 覆盖必须是对象")
                    from rules.definitions import validate_rule_params
                    normalized_override = validate_rule_params(rule_specs[override_id], override)
                    # Persist only explicitly overridden keys so future template
                    # default changes remain effective for untouched parameters.
                    rule_overrides[str(override_id)] = {
                        key: copy.deepcopy(normalized_override[key]) for key in override
                    }
                extra = {
                    k: copy.deepcopy(v)
                    for k, v in entry.items()
                    if k not in {"id", "name", "rtsp_url", "enabled", "rules", "rule_overrides", "deleted_at", "expected_revision"}
                }
                old = existing.get(cid)
                old_rules = list(old.get("rules", [])) if old else []
                old_overrides = copy.deepcopy(old.get("rule_overrides", {})) if old else {}
                same = (
                    old is not None
                    and old.get("rtsp_url") == source_uri
                    and old.get("name") == (entry.get("name") or cid)
                    and bool(old.get("enabled", True)) == bool(entry.get("enabled", True))
                    and {k: old.get(k) for k in extra} == extra
                    and old_rules == rules
                    and old_overrides == rule_overrides
                    and old.get("deleted_at") is None
                )
                if same:
                    continue
                old_json = self._redact_camera(old) if old else None
                conn.execute(
                    """
                    INSERT INTO cameras
                        (id, name, source_type, source_uri, config_json, enabled,
                         revision, deleted_at, created_at, updated_at)
                    VALUES (?, ?, 'rtsp', ?, ?, ?, 1, NULL, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name, source_uri = excluded.source_uri,
                        config_json = excluded.config_json, enabled = excluded.enabled,
                        revision = cameras.revision + 1, deleted_at = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (cid, str(entry.get("name") or cid), source_uri, _json(extra),
                     int(bool(entry.get("enabled", True))), now, now),
                )
                conn.execute("DELETE FROM camera_rules WHERE camera_id = ?", (cid,))
                try:
                    for rid in rules:
                        conn.execute(
                            "INSERT INTO camera_rules"
                            "(camera_id, rule_id, params_override_json, created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?)",
                            (cid, rid, _json(rule_overrides.get(str(rid), {})), now, now),
                        )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"监控 {cid} 引用了不存在的规则: {rules}") from exc
                changed = True
                new_value = dict(entry)
                new_value["id"] = cid
                new_value["name"] = str(entry.get("name") or cid)
                new_value["rtsp_url"] = source_uri
                new_value["rules"] = rules
                new_value["enabled"] = bool(entry.get("enabled", True))
                audit_entries.append((cid, old_json, self._redact_camera(new_value)))
            for cid, old in existing.items():
                if old.get("deleted_at") is not None or cid in incoming:
                    continue
                conn.execute(
                    "UPDATE cameras SET enabled = 0, deleted_at = ?, "
                    "revision = revision + 1, updated_at = ? WHERE id = ?",
                    (now, now, cid),
                )
                conn.execute("DELETE FROM camera_rules WHERE camera_id = ?", (cid,))
                changed = True
                audit_entries.append((cid, self._redact_camera(old), None))
            if changed:
                revision = self.database.bump_revision(conn)
                for cid, before, after in audit_entries:
                    self._audit(conn, "camera", cid, before, after, actor, revision)
            else:
                revision = self.database.current_revision()
        return revision

    @staticmethod
    def _redact_camera(camera: dict | None) -> dict | None:
        if camera is None:
            return None
        value = copy.deepcopy(camera)
        value["rtsp_url"] = re.sub(
            r"(://[^:/@]+:)[^@]+(@)", r"\1****\2", str(value.get("rtsp_url", ""))
        )
        return value

    def get_models(self) -> list[dict]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT name, file_path, enabled, confidence_override, revision "
                "FROM models ORDER BY id"
            ).fetchall()
        result = []
        for row in rows:
            item = {
                "name": row[0], "path": row[1], "enabled": bool(row[2]),
            }
            if row[3] is not None:
                item["confidence_override"] = row[3]
            item["revision"] = int(row[4])
            result.append(item)
        return result

    def create_model(self, entry: dict, *, actor: str = "panel") -> dict:
        """Create one model without replacing unrelated registry entries."""
        name = str(entry.get("name", "")).strip()
        path = str(entry.get("path", "")).strip()
        if not name or not path:
            raise ValueError("模型必须包含 name 和 path")
        with self.database.transaction() as conn:
            if conn.execute("SELECT 1 FROM models WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"模型名称已存在: {name}")
            now = int(time.time())
            conn.execute(
                "INSERT INTO models(name, file_path, model_type, enabled, confidence_override, created_at, updated_at) "
                "VALUES (?, ?, 'yolo', ?, ?, ?, ?)",
                (name, path, int(bool(entry.get("enabled", True))),
                 entry.get("confidence_override"), now, now),
            )
            revision = self.database.bump_revision(conn)
            self._audit(conn, "model", name, None, entry, actor, revision)
        return next(m for m in self.get_models() if m["name"] == name)

    def update_model(self, name: str, fields: dict, *, actor: str = "panel",
                     expected_revision: int | None = None) -> dict:
        name = str(name).strip()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT name, file_path, enabled, confidence_override, revision FROM models WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                raise ValueError(f"模型未注册: {name}")
            current_revision = int(row[4])
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict(f"model:{name}", current_revision)
            before = {"name": row[0], "path": row[1], "enabled": bool(row[2]),
                      "confidence_override": row[3], "revision": current_revision}
            enabled = bool(fields.get("enabled", row[2]))
            confidence = fields.get("confidence_override", row[3])
            if confidence is not None:
                confidence = float(confidence)
                if not 0 <= confidence <= 1:
                    raise ValueError("confidence_override 必须在 0~1 之间")
            now = int(time.time())
            conn.execute(
                "UPDATE models SET enabled = ?, confidence_override = ?, revision = revision + 1, updated_at = ? WHERE name = ?",
                (int(enabled), confidence, now, name),
            )
            revision = self.database.bump_revision(conn)
            after = {**before, "enabled": enabled, "confidence_override": confidence,
                     "revision": current_revision + 1}
            self._audit(conn, "model", name, before, after, actor, revision)
        return next(m for m in self.get_models() if m["name"] == name)

    def delete_model(self, name: str, *, actor: str = "panel",
                     expected_revision: int | None = None) -> None:
        name = str(name).strip()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT name, file_path, enabled, confidence_override, revision FROM models WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                raise ValueError(f"模型未注册: {name}")
            current_revision = int(row[4])
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict(f"model:{name}", current_revision)
            before = {"name": row[0], "path": row[1], "enabled": bool(row[2]),
                      "confidence_override": row[3], "revision": current_revision}
            try:
                conn.execute("DELETE FROM models WHERE name = ?", (name,))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"模型仍被规则使用，无法注销: {name}") from exc
            revision = self.database.bump_revision(conn)
            self._audit(conn, "model", name, before, None, actor, revision)

    # ------------------------------------------------------------------
    # Rule templates and rules
    # ------------------------------------------------------------------

    def get_templates(self) -> dict[str, dict]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT code, name, description, executor_type, params_schema_json, revision "
                "FROM rule_templates ORDER BY code"
            ).fetchall()
        result = {}
        for row in rows:
            try:
                params = json.loads(row[4]) or []
            except (TypeError, json.JSONDecodeError):
                params = []
            result[str(row[0])] = {
                "label": str(row[1]),
                "logic": str(row[3]),
                "params": params if isinstance(params, list) else [],
                "revision": int(row[5]),
            }
        return result

    @staticmethod
    def _validate_template(code: str, spec: dict) -> dict:
        from rules.definitions import validate_template

        normalized = {
            "label": spec.get("label"),
            "logic": spec.get("logic"),
            "params": spec.get("params") or [],
        }
        return validate_template(str(code).strip(), normalized)

    @staticmethod
    def _validate_template_compatibility(conn, code: str, template_spec: dict) -> None:
        """Reject template changes that would invalidate stored runtime data.

        Templates are shared by rules and camera-level parameter overrides.  A
        schema update therefore cannot be treated as an isolated row update:
        validate every dependent rule and every override against the proposed
        schema while still inside the caller's transaction.
        """
        from core.rules_graph import validate_graph
        from rules.definitions import validate_rule_params

        rule_rows = conn.execute(
            "SELECT id, params_json, graph_json FROM rules WHERE template_code = ?",
            (code,),
        ).fetchall()
        spec = {"label": template_spec["label"], "logic": template_spec["logic"],
                "params": template_spec["params"]}
        for row in rule_rows:
            try:
                params = json.loads(row[1]) if row[1] else {}
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"规则 {row[0]} 的 params 数据损坏，无法更新模板") from exc
            try:
                validate_rule_params(spec, params)
            except ValueError as exc:
                raise ValueError(f"模板更新会使规则 {row[0]} 的参数失效: {exc}") from exc

            try:
                graph = json.loads(row[2]) if row[2] else {}
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"规则 {row[0]} 的 graph 数据损坏，无法更新模板") from exc
            if spec["logic"] == "graph":
                if not graph:
                    raise ValueError(f"模板更新会使规则 {row[0]} 缺少 graph")
                try:
                    validate_graph(graph)
                except ValueError as exc:
                    raise ValueError(f"模板更新会使规则 {row[0]} 的 graph 失效: {exc}") from exc
            elif graph:
                raise ValueError(f"模板更新会使规则 {row[0]} 携带非法 graph")

        override_rows = conn.execute(
            "SELECT cr.camera_id, cr.rule_id, cr.params_override_json "
            "FROM camera_rules cr JOIN rules r ON r.id = cr.rule_id "
            "WHERE r.template_code = ? AND cr.enabled = 1",
            (code,),
        ).fetchall()
        rule_params = {}
        for row in rule_rows:
            try:
                rule_params[int(row[0])] = json.loads(row[1]) if row[1] else {}
            except (TypeError, json.JSONDecodeError):
                rule_params[int(row[0])] = {}
        for camera_id, rule_id, raw_override in override_rows:
            try:
                override = json.loads(raw_override) if raw_override else {}
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"摄像头 {camera_id} 的规则 {rule_id} 覆盖数据损坏") from exc
            if not isinstance(override, dict):
                raise ValueError(f"摄像头 {camera_id} 的规则 {rule_id} 覆盖必须是对象")
            try:
                base = validate_rule_params(spec, rule_params[int(rule_id)])
                normalized_override = validate_rule_params(spec, override)
                merged = copy.deepcopy(base)
                for key in override:
                    merged[key] = normalized_override[key]
                validate_rule_params(spec, merged)
            except ValueError as exc:
                raise ValueError(
                    f"模板更新会使摄像头 {camera_id} 的规则 {rule_id} 覆盖失效: {exc}"
                ) from exc

    def create_template(self, code: str, spec: dict, *, actor: str = "panel") -> dict:
        code = str(code or "").strip()
        normalized = self._validate_template(code, spec)
        with self.database.transaction() as conn:
            if conn.execute("SELECT 1 FROM rule_templates WHERE code = ?", (code,)).fetchone():
                raise ValueError(f"模板已存在: {code}")
            now = int(time.time())
            conn.execute(
                "INSERT INTO rule_templates(code, name, description, executor_type, "
                "params_schema_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (code, normalized["label"], "", normalized["logic"],
                 _json(normalized["params"]), now, now),
            )
            revision = self.database.bump_revision(conn)
            self._audit(conn, "template", code, None, normalized, actor, revision)
        return normalized

    def update_template(self, code: str, spec: dict, *, actor: str = "panel",
                        expected_revision: int | None = None) -> dict:
        code = str(code or "").strip()
        normalized = self._validate_template(code, spec)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT name, executor_type, params_schema_json, revision FROM rule_templates WHERE code = ?",
                (code,),
            ).fetchone()
            if row is None:
                raise ValueError(f"模板不存在: {code}")
            current_revision = int(row[3])
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict(f"template:{code}", current_revision)
            before = {
                "label": row[0], "logic": row[1],
                "params": json.loads(row[2]) if row[2] else [],
                "revision": current_revision,
            }
            self._validate_template_compatibility(conn, code, normalized)
            now = int(time.time())
            conn.execute(
                "UPDATE rule_templates SET name = ?, executor_type = ?, "
                "params_schema_json = ?, revision = revision + 1, updated_at = ? WHERE code = ?",
                (normalized["label"], normalized["logic"],
                 _json(normalized["params"]), now, code),
            )
            after = {**normalized, "revision": current_revision + 1}
            revision = self.database.bump_revision(conn)
            self._audit(conn, "template", code, before, after, actor, revision)
        return after

    def delete_template(self, code: str, *, actor: str = "panel",
                        expected_revision: int | None = None) -> None:
        code = str(code or "").strip()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT name, executor_type, params_schema_json, revision FROM rule_templates WHERE code = ?",
                (code,),
            ).fetchone()
            if row is None:
                raise ValueError(f"模板不存在: {code}")
            current_revision = int(row[3])
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict(f"template:{code}", current_revision)
            before = {
                "label": row[0], "logic": row[1],
                "params": json.loads(row[2]) if row[2] else [],
                "revision": current_revision,
            }
            try:
                conn.execute("DELETE FROM rule_templates WHERE code = ?", (code,))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"模板仍被规则使用，无法删除: {code}") from exc
            revision = self.database.bump_revision(conn)
            self._audit(conn, "template", code, before, None, actor, revision)

    def get_rules(self) -> list:
        from rules.definitions import RuleDefinition

        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, name, description, category, template_code, params_json, "
                "graph_json, severity, enabled, revision FROM rules ORDER BY id"
            ).fetchall()
            model_rows = conn.execute(
                "SELECT rm.rule_id, m.name FROM rule_models rm "
                "JOIN models m ON m.id = rm.model_id ORDER BY rm.rule_id, rm.sort_order, m.name"
            ).fetchall()
        models_by_rule: dict[int, list[str]] = {}
        for row in model_rows:
            models_by_rule.setdefault(int(row[0]), []).append(str(row[1]))
        result = []
        for row in rows:
            try:
                params = json.loads(row[5]) or {}
            except (TypeError, json.JSONDecodeError):
                params = {}
            try:
                graph = json.loads(row[6]) if row[6] else {}
            except (TypeError, json.JSONDecodeError):
                graph = {}
            result.append(RuleDefinition(
                id=int(row[0]), name=str(row[1]), description=str(row[2] or ""),
                category=str(row[3] or "ppe"), template=str(row[4]),
                models=models_by_rule.get(int(row[0]), []),
                params=params if isinstance(params, dict) else {},
                graph=graph if isinstance(graph, dict) else {},
                severity=int(row[7] if row[7] is not None else 2),
                enabled=bool(row[8]),
                revision=int(row[9]),
            ))
        return result


    def get_rule_by_id(self, rule_id: int):
        return next((rule for rule in self.get_rules() if rule.id == int(rule_id)), None)

    def next_rule_id(self) -> int:
        used = {rule.id for rule in self.get_rules()}
        candidate = 1
        while candidate in used:
            candidate += 1
        return candidate

    @staticmethod
    def _rule_payload(rule) -> dict:
        return {
            "id": int(rule.id), "name": str(rule.name),
            "description": str(rule.description or ""),
            "category": str(getattr(rule, "category", "ppe") or "ppe"),
            "template": str(rule.template), "models": [str(m) for m in rule.models],
            "params": copy.deepcopy(rule.params or {}),
            "graph": copy.deepcopy(rule.graph or {}),
            "severity": int(rule.severity), "enabled": bool(rule.enabled),
            "revision": int(getattr(rule, "revision", 1)),
        }

    def _normalize_rule(self, data: dict, *, templates: dict[str, dict] | None = None,
                        models: set[str] | None = None):
        from rules.definitions import RuleDefinition, validate_rule_params
        from core.rules_graph import validate_graph

        if not isinstance(data, dict):
            raise ValueError("规则数据必须是对象")
        templates = templates if templates is not None else self.get_templates()
        template = str(data.get("template", "")).strip()
        template_spec = templates.get(template)
        if template_spec is None:
            raise ValueError(f"未知模板: {template}")
        model_names = [str(name).strip() for name in (data.get("models") or [])]
        if len(model_names) != len(set(model_names)):
            raise ValueError("绑定模型不能重复")
        available = models if models is not None else {item["name"] for item in self.get_models()}
        missing = [name for name in model_names if name not in available]
        if missing:
            raise ValueError(f"规则引用了不存在的模型: {', '.join(missing)}")
        try:
            rule_id = int(data["id"])
            severity = int(data.get("severity", 2))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("规则 id 和 severity 必须是数字") from exc
        params = validate_rule_params(template_spec, data.get("params", {}) or {})
        raw_graph = data.get("graph", {}) or {}
        if not isinstance(raw_graph, dict):
            raise ValueError("规则 graph 必须是对象")
        if str(template_spec.get("logic", "")) == "graph":
            if not raw_graph:
                raise ValueError("graph 模板必须提供非空 graph")
            graph = validate_graph(raw_graph)
        else:
            if raw_graph:
                raise ValueError("只有 graph 模板允许配置 graph")
            graph = {}
        if severity < 0 or severity > 5:
            raise ValueError("规则 severity 必须在 0 到 5 之间")
        return RuleDefinition(
            id=rule_id, name=str(data.get("name") or f"rule_{rule_id}"),
            description=str(data.get("description", "")),
            category=str(data.get("category", "ppe") or "ppe"),
            template=template, models=model_names, params=params, graph=graph,
            severity=severity, enabled=bool(data.get("enabled", True)))

    def add_rule(self, data: dict, *, actor: str = "panel"):
        rule = self._normalize_rule(data)
        with self.database.transaction() as conn:
            if conn.execute("SELECT 1 FROM rules WHERE id = ?", (rule.id,)).fetchone():
                raise ValueError(f"规则 ID 已存在: {rule.id}")
            now = int(time.time())
            conn.execute(
                "INSERT INTO rules(id, template_code, name, description, category, params_json, "
                "graph_json, severity, enabled, schema_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (rule.id, rule.template, rule.name, rule.description, rule.category,
                 _json(rule.params), _json(rule.graph) if rule.graph else None,
                 rule.severity, int(rule.enabled), now, now),
            )
            self._replace_rule_models(conn, rule)
            revision = self.database.bump_revision(conn)
            self._audit(conn, "rule", rule.id, None, self._rule_payload(rule), actor, revision)
        return rule, revision

    def update_rule(self, rule_id: int, fields: dict, *, actor: str = "panel",
                    expected_revision: int | None = None):
        current = self.get_rule_by_id(rule_id)
        if current is None:
            raise ValueError(f"规则不存在: {rule_id}")
        merged = self._rule_payload(current)
        merged.update({key: copy.deepcopy(value) for key, value in fields.items()
                       if key in {"name", "description", "category", "template", "models",
                                  "params", "graph", "severity", "enabled"}})
        merged["id"] = int(rule_id)
        rule = self._normalize_rule(merged)
        before = self._rule_payload(current)
        with self.database.transaction() as conn:
            row = conn.execute("SELECT revision FROM rules WHERE id = ?", (int(rule_id),)).fetchone()
            current_revision = int(row[0]) if row else 0
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict(f"rule:{rule_id}", current_revision)
            now = int(time.time())
            conn.execute(
                "UPDATE rules SET template_code = ?, name = ?, description = ?, category = ?, "
                "params_json = ?, graph_json = ?, severity = ?, enabled = ?, "
                "revision = revision + 1, updated_at = ? WHERE id = ?",
                (rule.template, rule.name, rule.description, rule.category, _json(rule.params),
                 _json(rule.graph) if rule.graph else None, rule.severity, int(rule.enabled),
                 now, int(rule_id)),
            )
            self._replace_rule_models(conn, rule)
            revision = self.database.bump_revision(conn)
            self._audit(conn, "rule", rule.id, before, self._rule_payload(rule), actor, revision)
        return rule, revision

    @staticmethod
    def _replace_rule_models(conn, rule) -> None:
        conn.execute("DELETE FROM rule_models WHERE rule_id = ?", (int(rule.id),))
        model_ids = {}
        for row in conn.execute("SELECT id, name FROM models").fetchall():
            model_ids[str(row[1])] = int(row[0])
        for order, name in enumerate(rule.models):
            model_id = model_ids.get(name)
            if model_id is None:
                raise ValueError(f"规则 {rule.id} 引用了不存在的模型: {name}")
            conn.execute(
                "INSERT INTO rule_models(rule_id, model_id, role, sort_order) VALUES (?, ?, 'default', ?)",
                (int(rule.id), model_id, order),
            )

    def delete_rule(self, rule_id: int, *, actor: str = "panel",
                    expected_revision: int | None = None) -> None:
        rule = self.get_rule_by_id(rule_id)
        if rule is None:
            raise ValueError(f"规则不存在: {rule_id}")
        with self.database.transaction() as conn:
            row = conn.execute("SELECT revision FROM rules WHERE id = ?", (int(rule_id),)).fetchone()
            current_revision = int(row[0]) if row else 0
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict(f"rule:{rule_id}", current_revision)
            camera_ref = conn.execute(
                "SELECT 1 FROM camera_rules WHERE rule_id = ? LIMIT 1", (int(rule_id),)
            ).fetchone()
            if camera_ref is not None:
                raise ValueError(f"规则仍被相机使用，无法删除: {rule_id}")
            try:
                conn.execute("DELETE FROM rules WHERE id = ?", (int(rule_id),))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"规则仍被相机使用，无法删除: {rule_id}") from exc
            revision = self.database.bump_revision(conn)
            self._audit(conn, "rule", rule.id, self._rule_payload(rule), None, actor, revision)

    def replace_models(self, models: list[dict], actor: str = "panel", *,
                       expected_revision: int | None = None) -> tuple[list[dict], int]:
        with self.database.transaction() as conn:
            now = int(time.time())
            old_rows = conn.execute("SELECT name FROM models").fetchall()
            old_names = {r[0] for r in old_rows}
            new_names = set()
            for entry in models:
                name = str(entry.get("name", "")).strip()
                path = str(entry.get("path", "")).strip()
                if not name or not path:
                    raise ValueError("模型必须包含 name 和 path")
                if name in new_names:
                    raise ValueError(f"模型名称重复: {name}")
                new_names.add(name)
                conn.execute(
                    """
                    INSERT INTO models
                        (name, file_path, model_type, enabled, confidence_override,
                         created_at, updated_at)
                    VALUES (?, ?, 'yolo', ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        file_path = excluded.file_path,
                        enabled = excluded.enabled,
                        confidence_override = excluded.confidence_override,
                        revision = models.revision + 1,
                        updated_at = excluded.updated_at
                    """,
                    (name, path, int(bool(entry.get("enabled", True))),
                     entry.get("confidence_override"), now, now),
                )
            for removed in old_names - new_names:
                try:
                    conn.execute("DELETE FROM models WHERE name = ?", (removed,))
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"模型仍被规则引用，无法注销: {removed}") from exc
            settings_row = conn.execute(
                "SELECT value_json, revision FROM settings_sections WHERE section = 'model'"
            ).fetchone()
            model_section = json.loads(settings_row[0]) if settings_row else {}
            current_revision = int(settings_row[1]) if settings_row else 0
            if expected_revision is not None and current_revision != int(expected_revision):
                raise RevisionConflict("settings:model", current_revision)
            model_section["models"] = copy.deepcopy(models)
            object_revision = int(settings_row[1]) + 1 if settings_row else 1
            conn.execute(
                """
                INSERT INTO settings_sections(section, value_json, revision, updated_at, updated_by)
                VALUES ('model', ?, ?, ?, ?)
                ON CONFLICT(section) DO UPDATE SET value_json=excluded.value_json,
                    revision=excluded.revision, updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (_json(model_section), object_revision, now, actor),
            )
            revision = self.database.bump_revision(conn)
        return self.get_models(), revision

    def import_yaml(self, config_dir: str | Path, *, reset: bool = False, actor: str = "import") -> int:
        """Import the four legacy YAML documents into a fresh machine.db.

        YAML is an explicit migration format, not a runtime data source.  The
        complete document set is normalized and validated before opening the
        write transaction so malformed camera associations cannot leave a
        partially imported database behind.
        """
        from rules.definitions import validate_rule_params

        config_dir = Path(config_dir)
        settings = _load_yaml(config_dir / "settings.yaml")
        cameras_doc = _load_yaml(config_dir / "cameras.yaml")
        templates_doc = _load_yaml(config_dir / "rule_templates.yaml")
        rules_doc = _load_yaml(config_dir / "rules.yaml")

        raw_cameras = cameras_doc.get("cameras", [])
        raw_templates = templates_doc.get("templates", {})
        raw_rules = rules_doc.get("rules", [])
        if raw_cameras is None:
            raw_cameras = []
        if raw_rules is None:
            raw_rules = []
        if raw_templates is None:
            raw_templates = {}
        if not isinstance(raw_cameras, list):
            raise ValueError("cameras.yaml 的 cameras 必须是列表")
        if not isinstance(raw_templates, dict):
            raise ValueError("rule_templates.yaml 的 templates 必须是对象")
        if not isinstance(raw_rules, list):
            raise ValueError("rules.yaml 的 rules 必须是列表")
        if not isinstance(settings, dict):
            raise ValueError("settings.yaml 必须是对象")

        settings = copy.deepcopy(settings)
        settings.setdefault("database", {})["path"] = "storage/machine.db"
        panel = settings.get("panel")
        if (isinstance(panel, dict) and "password" in panel
                and not is_password_hash(panel["password"])):
            panel["password"] = hash_password(str(panel["password"]))

        normalized_templates: dict[str, dict] = {}
        for code, spec in raw_templates.items():
            if not isinstance(spec, dict):
                raise ValueError(f"模板定义必须是对象: {code}")
            normalized_code = str(code).strip()
            if normalized_code in normalized_templates:
                raise ValueError(f"模板 code 重复: {normalized_code}")
            normalized_templates[normalized_code] = self._validate_template(normalized_code, spec)

        model_section = settings.get("model", {})
        if model_section is None:
            model_section = {}
        if not isinstance(model_section, dict):
            raise ValueError("settings.yaml 的 model 必须是对象")
        model_entries = model_section.get("models", [])
        if model_entries is None:
            model_entries = []
        if not isinstance(model_entries, list):
            raise ValueError("settings.yaml 的 model.models 必须是列表")
        model_names: set[str] = set()
        normalized_models = []
        for model in model_entries:
            if not isinstance(model, dict):
                raise ValueError("模型定义必须是对象")
            name = str(model.get("name", "")).strip()
            path_value = str(model.get("path", "")).strip()
            if not name or not path_value or name in model_names:
                raise ValueError(f"模型 name/path 无效或重复: {name}")
            confidence = model.get("confidence_override")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"模型 {name} 的 confidence_override 必须是数字") from exc
                if not 0 <= confidence <= 1:
                    raise ValueError(f"模型 {name} 的 confidence_override 必须在 0~1 之间")
            model_names.add(name)
            normalized_models.append({
                "name": name,
                "path": path_value,
                "enabled": bool(model.get("enabled", True)),
                **({"confidence_override": confidence} if confidence is not None else {}),
            })

        normalized_rules = []
        rules_by_id = {}
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                raise ValueError("规则定义必须是对象")
            try:
                rule_id = int(raw_rule["id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("规则 id 必须是数字") from exc
            if rule_id in rules_by_id:
                raise ValueError(f"规则 ID 重复: {rule_id}")
            normalized_rule = self._normalize_rule(
                raw_rule, templates=normalized_templates, models=model_names
            )
            rules_by_id[rule_id] = normalized_rule
            normalized_rules.append(normalized_rule)

        # Normalize camera associations before opening the write transaction.
        # This includes rule existence and partial camera-level overrides; the
        # latter are stored sparsely so template default changes remain useful.
        normalized_cameras = []
        camera_ids: set[str] = set()
        for camera in raw_cameras:
            if not isinstance(camera, dict):
                raise ValueError("摄像头定义必须是对象")
            cid = str(camera.get("id", "")).strip()
            source_uri = str(camera.get("rtsp_url", "")).strip()
            if not cid or not source_uri:
                raise ValueError(f"摄像头缺少 id 或 rtsp_url: {camera}")
            if cid in camera_ids:
                raise ValueError(f"摄像头 ID 重复: {cid}")
            camera_ids.add(cid)

            raw_rule_ids = camera.get("rules", [])
            if raw_rule_ids is None:
                raw_rule_ids = []
            if not isinstance(raw_rule_ids, list):
                raise ValueError(f"摄像头 {cid} 的 rules 必须是列表")
            rule_ids = []
            for raw_rule_id in raw_rule_ids:
                if isinstance(raw_rule_id, bool):
                    raise ValueError(f"摄像头 {cid} 的规则 ID 无效: {raw_rule_id}")
                try:
                    rule_id = int(raw_rule_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"摄像头 {cid} 的规则 ID 无效: {raw_rule_id}") from exc
                if rule_id in rule_ids:
                    raise ValueError(f"摄像头 {cid} 的规则 ID 重复: {rule_id}")
                if rule_id not in rules_by_id:
                    raise ValueError(f"摄像头 {cid} 引用了不存在的规则: {rule_id}")
                rule_ids.append(rule_id)

            raw_overrides = camera.get("rule_overrides", {})
            if raw_overrides is None:
                raw_overrides = {}
            if not isinstance(raw_overrides, dict):
                raise ValueError(f"摄像头 {cid} 的 rule_overrides 必须是对象")
            rule_overrides = {}
            for raw_rule_id, override in raw_overrides.items():
                if isinstance(raw_rule_id, bool):
                    raise ValueError(f"摄像头 {cid} 的覆盖规则 ID 无效: {raw_rule_id}")
                try:
                    override_id = int(raw_rule_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"摄像头 {cid} 的覆盖规则 ID 无效: {raw_rule_id}") from exc
                if override_id not in rule_ids:
                    raise ValueError(f"摄像头 {cid} 的覆盖规则未分配: {override_id}")
                if not isinstance(override, dict):
                    raise ValueError(f"摄像头 {cid} 的规则 {override_id} 覆盖必须是对象")
                template_spec = normalized_templates[rules_by_id[override_id].template]
                normalized_override = validate_rule_params(template_spec, override)
                rule_overrides[str(override_id)] = {
                    key: copy.deepcopy(normalized_override[key]) for key in override
                }

            normalized_cameras.append({
                "raw": copy.deepcopy(camera),
                "id": cid,
                "source_uri": source_uri,
                "name": str(camera.get("name") or cid),
                "enabled": bool(camera.get("enabled", True)),
                "rules": rule_ids,
                "rule_overrides": rule_overrides,
            })

        with self.database.transaction() as conn:
            if not reset:
                configured = conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM settings_sections) "
                    "OR EXISTS(SELECT 1 FROM cameras WHERE deleted_at IS NULL)"
                ).fetchone()[0]
                if configured:
                    raise ValueError("machine.db 已有配置，导入前请使用 reset 或删除开发数据库")
            if reset:
                for table in (
                    "config_audit_log", "camera_rules", "rule_models", "rules",
                    "rule_templates", "cameras", "models", "settings_sections",
                ):
                    conn.execute(f"DELETE FROM {table}")
                conn.execute(
                    "UPDATE config_meta SET global_revision = 1, updated_at = ? WHERE id = 1",
                    (int(time.time()),),
                )
            now = int(time.time())
            for section, value in settings.items():
                if isinstance(value, dict):
                    conn.execute(
                        """
                        INSERT INTO settings_sections(section, value_json, revision, updated_at, updated_by)
                        VALUES (?, ?, 1, ?, ?)
                        ON CONFLICT(section) DO UPDATE SET value_json=excluded.value_json,
                            revision=excluded.revision, updated_at=excluded.updated_at,
                            updated_by=excluded.updated_by
                        """,
                        (section, _json(value), now, actor),
                    )
            for model in normalized_models:
                conn.execute(
                    """
                    INSERT INTO models(name, file_path, model_type, enabled,
                                       confidence_override, created_at, updated_at)
                    VALUES (?, ?, 'yolo', ?, ?, ?, ?)
                    """,
                    (model["name"], model["path"], int(model.get("enabled", True)),
                     model.get("confidence_override"), now, now),
                )
            for code, spec in normalized_templates.items():
                conn.execute(
                    """
                    INSERT INTO rule_templates
                        (code, name, description, executor_type, params_schema_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(code), spec["label"], "", spec["logic"],
                     _json(spec["params"]), now, now),
                )
            model_ids = {
                row[0]: row[1]
                for row in conn.execute("SELECT name, id FROM models").fetchall()
            }
            for rule in normalized_rules:
                conn.execute(
                    """
                    INSERT INTO rules
                        (id, template_code, name, description, category, params_json, graph_json,
                         severity, enabled, schema_version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (rule.id, rule.template, rule.name, rule.description, rule.category,
                     _json(rule.params), _json(rule.graph) if rule.graph else None,
                     rule.severity, int(rule.enabled), now, now),
                )
                for order, model_name in enumerate(rule.models):
                    model_id = model_ids.get(model_name)
                    if model_id is None:
                        raise ValueError(f"规则 {rule.id} 引用了不存在的模型: {model_name}")
                    conn.execute(
                        "INSERT INTO rule_models(rule_id, model_id, role, sort_order) VALUES (?, ?, 'default', ?)",
                        (rule.id, model_id, order),
                    )
            for camera in normalized_cameras:
                extra = {
                    key: copy.deepcopy(value)
                    for key, value in camera["raw"].items()
                    if key not in {"id", "name", "rtsp_url", "enabled", "rules", "rule_overrides"}
                }
                conn.execute(
                    """
                    INSERT INTO cameras
                        (id, name, source_type, source_uri, config_json, enabled,
                         created_at, updated_at)
                    VALUES (?, ?, 'rtsp', ?, ?, ?, ?, ?)
                    """,
                    (camera["id"], camera["name"], camera["source_uri"], _json(extra),
                     int(camera["enabled"]), now, now),
                )
                for rule_id in camera["rules"]:
                    conn.execute(
                        """
                        INSERT INTO camera_rules
                            (camera_id, rule_id, params_override_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (camera["id"], rule_id,
                         _json(camera["rule_overrides"].get(str(rule_id), {})), now, now),
                    )
            revision = self.database.bump_revision(conn)
            conn.execute(
                "UPDATE config_audit_log SET revision = ? WHERE revision IS NULL",
                (revision,),
            )
        return revision

    def _audit(self, conn, object_type, object_id, before, after, actor, revision):
        conn.execute(
            """
            INSERT INTO config_audit_log
                (object_type, object_id, operation, before_json, after_json,
                 actor, revision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (object_type, str(object_id), "update" if before else "create",
             _json(_redact_audit_value(before)) if before is not None else None,
             _json(_redact_audit_value(after)) if after is not None else None,
             actor, revision, int(time.time())),
        )


def _redact_audit_value(value: Any) -> Any:
    """Remove secrets and URI credentials before values enter the audit log."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            name = str(key)
            lowered = name.lower()
            if lowered in {"api_key", "password"}:
                redacted[name] = {"configured": bool(item)}
            elif lowered in {"rtsp_url", "source_uri"}:
                redacted[name] = re.sub(
                    r"(://[^:/@]+:)[^@]+(@)", r"\1****\2", str(item or "")
                )
            else:
                redacted[name] = _redact_audit_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_audit_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_audit_value(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是 YAML 对象: {path}")
    return data


class RevisionConflict(ValueError):
    def __init__(self, object_name: str, current_revision: int):
        super().__init__(f"配置已被其他请求更新，请刷新后重试（{object_name} revision={current_revision}）")
        self.object_name = object_name
        self.current_revision = current_revision