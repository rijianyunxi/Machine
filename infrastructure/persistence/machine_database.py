"""SQLite infrastructure for the unified machine database.

The application intentionally uses one SQLite file for configuration and alerts.
This module owns connection pragmas and schema migrations; repositories own SQL
for individual aggregates.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "storage" / "machine.db"


class MachineDatabase:
    """Connection factory and transactional schema owner for ``machine.db``."""

    def __init__(self, path: str | Path | None = None, busy_timeout_ms: int = 5000):
        raw = Path(path) if path else DEFAULT_DB_PATH
        self.path = raw if raw.is_absolute() else PROJECT_ROOT / raw
        self.path = self.path.resolve()
        self.busy_timeout_ms = max(int(busy_timeout_ms), 100)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @staticmethod
    def _migration_definitions():
        return (
            (1, "unified_machine_schema", MachineDatabase._migration_v1),
            (2, "rule_category", MachineDatabase._migration_v2),
        )

    @staticmethod
    def _migration_checksum(name: str, migration) -> str:
        source = inspect.getsource(migration).encode("utf-8")
        return "sha256:" + hashlib.sha256((name + "\n").encode("utf-8") + source).hexdigest()

    def migrate(self) -> None:
        with self._schema_lock:
            with self.connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        applied_at INTEGER NOT NULL
                    )
                    """
                )
                applied = {
                    int(row[0]): (str(row[1]), str(row[2]))
                    for row in conn.execute(
                        "SELECT version, name, checksum FROM schema_migrations"
                    ).fetchall()
                }
                for version, name, migration in self._migration_definitions():
                    checksum = self._migration_checksum(name, migration)
                    if version in applied:
                        applied_name, applied_checksum = applied[version]
                        if applied_name != name:
                            raise sqlite3.DatabaseError(
                                f"迁移版本 {version} 名称不匹配: "
                                f"数据库={applied_name}, 内置={name}"
                            )
                        # Databases created by the first development iteration
                        # used a marker rather than a real checksum. Upgrade that
                        # marker once; all real checksum mismatches are fatal.
                        if applied_checksum.startswith("builtin:"):
                            conn.execute(
                                "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
                                (checksum, version),
                            )
                        elif applied_checksum != checksum:
                            raise sqlite3.DatabaseError(
                                f"迁移版本 {version} checksum 不匹配，拒绝启动"
                            )
                        continue
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        migration(conn)
                        conn.execute(
                            "INSERT INTO schema_migrations"
                            "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                            (version, name, checksum, int(time.time())),
                        )
                        conn.execute("COMMIT")
                    except Exception:
                        conn.execute("ROLLBACK")
                        raise

    @staticmethod
    def _migration_v2(conn: sqlite3.Connection) -> None:
        """Add fields introduced after the initial development schema."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(rules)")}
        if "category" not in columns:
            conn.execute(
                "ALTER TABLE rules ADD COLUMN category TEXT NOT NULL DEFAULT 'ppe'"
            )

    @staticmethod
    def _migration_v1(conn: sqlite3.Connection) -> None:
        # Do not use executescript here: sqlite3.executescript() implicitly
        # commits the active transaction, which would make a later migration
        # failure leave a partially-created schema behind. The v1 schema has
        # no semicolons inside string literals, so executing each statement
        # separately keeps the caller's BEGIN/ROLLBACK boundary intact.
        schema = """
            CREATE TABLE IF NOT EXISTS config_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                global_revision INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO config_meta(id, global_revision, updated_at)
            VALUES (1, 1, strftime('%s', 'now'));

            CREATE TABLE IF NOT EXISTS settings_sections (
                section TEXT PRIMARY KEY,
                value_json TEXT NOT NULL CHECK (json_valid(value_json)),
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL,
                updated_by TEXT
            );

            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                model_type TEXT NOT NULL DEFAULT 'yolo',
                classes_json TEXT NOT NULL DEFAULT '{}'
                    CHECK (json_valid(classes_json)),
                sha256 TEXT,
                file_size INTEGER,
                validation_status TEXT NOT NULL DEFAULT 'unknown',
                validation_error TEXT,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                confidence_override REAL,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'rtsp',
                source_uri TEXT NOT NULL,
                config_json TEXT NOT NULL DEFAULT '{}'
                    CHECK (json_valid(config_json)),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                revision INTEGER NOT NULL DEFAULT 1,
                deleted_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rule_templates (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                executor_type TEXT NOT NULL,
                executor_version INTEGER NOT NULL DEFAULT 1,
                schema_version INTEGER NOT NULL DEFAULT 1,
                params_schema_json TEXT NOT NULL DEFAULT '{}'
                    CHECK (json_valid(params_schema_json)),
                graph_schema_json TEXT
                    CHECK (graph_schema_json IS NULL OR json_valid(graph_schema_json)),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                revision INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_code TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                params_json TEXT NOT NULL DEFAULT '{}'
                    CHECK (json_valid(params_json)),
                graph_json TEXT
                    CHECK (graph_json IS NULL OR json_valid(graph_json)),
                severity INTEGER NOT NULL DEFAULT 2,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                schema_version INTEGER NOT NULL DEFAULT 1,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (template_code) REFERENCES rule_templates(code)
                    ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS rule_models (
                rule_id INTEGER NOT NULL,
                model_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'default',
                sort_order INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (rule_id, model_id, role),
                FOREIGN KEY (rule_id) REFERENCES rules(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS camera_rules (
                camera_id TEXT NOT NULL,
                rule_id INTEGER NOT NULL,
                params_override_json TEXT NOT NULL DEFAULT '{}'
                    CHECK (json_valid(params_override_json)),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                revision INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (camera_id, rule_id),
                FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE,
                FOREIGN KEY (rule_id) REFERENCES rules(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS config_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                before_json TEXT CHECK (before_json IS NULL OR json_valid(before_json)),
                after_json TEXT CHECK (after_json IS NULL OR json_valid(after_json)),
                actor TEXT,
                revision INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id TEXT NOT NULL,
                camera_name TEXT,
                rule_id INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                description TEXT,
                confidence REAL,
                severity INTEGER DEFAULT 2,
                snapshot_path TEXT,
                snapshot_created_at REAL,
                snapshot_cleaned_at REAL,
                timestamp REAL NOT NULL,
                datetime_str TEXT,
                status TEXT DEFAULT 'new',
                created_at REAL DEFAULT (strftime('%s', 'now')),
                extra TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_camera_time
                ON alerts(camera_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_alerts_rule_time
                ON alerts(rule_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_alerts_status
                ON alerts(status);
            CREATE INDEX IF NOT EXISTS idx_cameras_enabled
                ON cameras(enabled, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_audit_object
                ON config_audit_log(object_type, object_id, created_at);
            """
        for statement in schema.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)

    def current_revision(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT global_revision FROM config_meta WHERE id = 1"
            ).fetchone()
            return int(row[0]) if row else 1

    def validate(self) -> dict:
        """Validate SQLite integrity, foreign keys, and JSON columns."""
        with self.connection() as conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = [dict(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
            json_checks = {
                "settings_sections": conn.execute(
                    "SELECT COUNT(*) FROM settings_sections WHERE NOT json_valid(value_json)"
                ).fetchone()[0],
                "models": conn.execute(
                    "SELECT COUNT(*) FROM models WHERE NOT json_valid(classes_json)"
                ).fetchone()[0],
                "cameras": conn.execute(
                    "SELECT COUNT(*) FROM cameras WHERE NOT json_valid(config_json)"
                ).fetchone()[0],
                "rule_templates": conn.execute(
                    "SELECT COUNT(*) FROM rule_templates WHERE NOT json_valid(params_schema_json)"
                ).fetchone()[0],
                "rules": conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE NOT json_valid(params_json)"
                ).fetchone()[0],
                "camera_rules": conn.execute(
                    "SELECT COUNT(*) FROM camera_rules WHERE NOT json_valid(params_override_json)"
                ).fetchone()[0],
                "config_audit_log": conn.execute(
                    "SELECT COUNT(*) FROM config_audit_log "
                    "WHERE (before_json IS NOT NULL AND NOT json_valid(before_json)) "
                    "OR (after_json IS NOT NULL AND NOT json_valid(after_json))"
                ).fetchone()[0],
            }
            migrations = [dict(row) for row in conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()]
        ok = integrity == "ok" and not foreign_keys and not any(json_checks.values())
        return {
            "ok": ok,
            "integrity_check": integrity,
            "foreign_key_errors": foreign_keys,
            "json_errors": json_checks,
            "migrations": migrations,
        }

    def assert_valid(self) -> dict:
        report = self.validate()
        if not report["ok"]:
            raise sqlite3.DatabaseError(f"machine.db 校验失败: {report}")
        return report

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent backup using SQLite's online backup API."""
        target = Path(destination)
        if not target.is_absolute():
            target = PROJECT_ROOT / target
        target = target.resolve()
        if target == self.path:
            raise ValueError("备份目标不能与源数据库相同")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        backup = sqlite3.connect(str(target))
        try:
            with self.connection() as source:
                source.backup(backup)
        finally:
            backup.close()
        MachineDatabase(target, self.busy_timeout_ms).assert_valid()
        return target

    @classmethod
    def restore_from(cls, backup: str | Path, destination: str | Path,
                     busy_timeout_ms: int = 5000) -> Path:
        """Restore a validated backup into a new database path atomically."""
        source_path = Path(backup).resolve()
        target = Path(destination)
        if not target.is_absolute():
            target = PROJECT_ROOT / target
        target = target.resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"备份文件不存在: {source_path}")
        if source_path == target:
            raise ValueError("恢复源不能与目标数据库相同")
        source = cls(source_path, busy_timeout_ms)
        source.assert_valid()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".restore.tmp")
        if temp.exists():
            temp.unlink()
        try:
            target_conn = sqlite3.connect(str(temp))
            try:
                with source.connection() as source_conn:
                    source_conn.backup(target_conn)
            finally:
                target_conn.close()
            cls(temp, busy_timeout_ms).assert_valid()
            os.replace(str(temp), str(target))
        finally:
            if temp.exists():
                temp.unlink()
        return target

    @staticmethod
    def bump_revision(conn: sqlite3.Connection) -> int:
        now = int(time.time())
        conn.execute(
            "UPDATE config_meta SET global_revision = global_revision + 1, "
            "updated_at = ? WHERE id = 1",
            (now,),
        )
        row = conn.execute(
            "SELECT global_revision FROM config_meta WHERE id = 1"
        ).fetchone()
        return int(row[0])