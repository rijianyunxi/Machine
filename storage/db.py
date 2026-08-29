"""
SQLite database module for alert storage.

Stores violation alerts with metadata including:
- Camera ID, rule ID, rule name
- Confidence, severity, timestamp
- Snapshot file path
- Alert status (new, acknowledged, resolved)
"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from core.analyzer import Violation
from utils.logger import get_logger


class AlertDatabase:
    """SQLite-based alert storage with thread-safe access."""

    def __init__(self, settings: dict):
        self._logger = get_logger("database")

        db_cfg = settings.get("database", {})
        self._db_path = db_cfg.get("path", "storage/alerts.db")
        self._busy_timeout_ms = db_cfg.get("busy_timeout_ms", 5000)

        # Ensure directory exists
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._lock = threading.Lock()
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with WAL mode and busy timeout."""
        conn = sqlite3.connect(
            self._db_path, timeout=self._busy_timeout_ms / 1000.0
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return conn

    def _init_database(self):
        """Initialize database schema."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        camera_id TEXT NOT NULL,
                        rule_id INTEGER NOT NULL,
                        rule_name TEXT NOT NULL,
                        description TEXT,
                        confidence REAL,
                        severity INTEGER DEFAULT 2,
                        snapshot_path TEXT,
                        timestamp REAL NOT NULL,
                        datetime_str TEXT,
                        status TEXT DEFAULT 'new',
                        created_at REAL DEFAULT (strftime('%s', 'now')),
                        extra TEXT
                    )
                    """
                )

                # Index for common queries
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_camera_time "
                    "ON alerts(camera_id, timestamp)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_rule_time "
                    "ON alerts(rule_id, timestamp)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_alerts_status "
                    "ON alerts(status)"
                )

                conn.commit()
            finally:
                conn.close()
            self._logger.info(f"Database initialized: {self._db_path}")

    def insert_alert(
        self, violation: Violation, snapshot_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Insert a new alert record.

        Args:
            violation: The detected violation.
            snapshot_path: Path to the saved snapshot image.

        Returns:
            Alert ID, or None on error.
        """
        try:
            dt_str = datetime.fromtimestamp(violation.timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            extra_str = json.dumps(violation.extra) if violation.extra else ""

            with self._lock:
                conn = self._connect()
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO alerts
                            (camera_id, rule_id, rule_name, description,
                             confidence, severity, snapshot_path, timestamp,
                             datetime_str, status, extra)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
                        """,
                        (
                            violation.camera_id,
                            violation.rule_id,
                            violation.rule_name,
                            violation.description,
                            violation.confidence,
                            violation.severity,
                            snapshot_path,
                            violation.timestamp,
                            dt_str,
                            extra_str,
                        ),
                    )
                    conn.commit()
                    alert_id = cursor.lastrowid
                finally:
                    conn.close()

            self._logger.info(
                f"Alert #{alert_id} saved: [{violation.camera_id}] "
                f"R{violation.rule_id:02d} {violation.rule_name}"
            )
            return alert_id

        except Exception as e:
            self._logger.error(f"Database insert error: {e}")
            return None

    def get_alert_count(self, status: Optional[str] = None) -> int:
        """Get total alert count with optional status filter."""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cursor = conn.cursor()
                    if status:
                        cursor.execute(
                            "SELECT COUNT(*) FROM alerts WHERE status = ?",
                            (status,),
                        )
                    else:
                        cursor.execute("SELECT COUNT(*) FROM alerts")
                    count = cursor.fetchone()[0]
                finally:
                    conn.close()
                return count
        except Exception as e:
            self._logger.error(f"Database count error: {e}")
            return 0

    def get_alerts(
        self,
        camera: Optional[str] = None,
        rule_id: Optional[int] = None,
        status: Optional[str] = None,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Paged alert query for the panel. Returns {items, total}."""
        clauses, args = [], []
        if camera:
            clauses.append("camera_id = ?")
            args.append(camera)
        if rule_id is not None:
            clauses.append("rule_id = ?")
            args.append(rule_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        if from_ts is not None:
            clauses.append("timestamp >= ?")
            args.append(from_ts)
        if to_ts is not None:
            clauses.append("timestamp <= ?")
            args.append(to_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT COUNT(*) FROM alerts {where}", args)
                    total = cursor.fetchone()[0]
                    cursor.execute(
                        f"SELECT * FROM alerts {where} "
                        f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                        args + [int(limit), int(offset)],
                    )
                    items = [dict(r) for r in cursor.fetchall()]
                finally:
                    conn.close()
            return {"items": items, "total": total}
        except Exception as e:
            self._logger.error(f"Database query error: {e}")
            return {"items": [], "total": 0}

    def update_alert_status(
        self, alert_id: int, status: str, note: Optional[str] = None
    ) -> bool:
        """Set alert workflow status (new/confirmed/false_positive/resolved)."""
        valid = {"new", "confirmed", "false_positive", "resolved"}
        if status not in valid:
            raise ValueError(f"非法状态: {status}，可选 {sorted(valid)}")
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cursor = conn.cursor()
                    if note is not None:
                        cursor.execute(
                            "SELECT extra FROM alerts WHERE id = ?", (alert_id,)
                        )
                        row = cursor.fetchone()
                        extra = {}
                        if row and row[0]:
                            try:
                                extra = json.loads(row[0])
                            except Exception:
                                extra = {}
                        extra["note"] = note
                        cursor.execute(
                            "UPDATE alerts SET status = ?, extra = ? WHERE id = ?",
                            (status, json.dumps(extra, ensure_ascii=False), alert_id),
                        )
                    else:
                        cursor.execute(
                            "UPDATE alerts SET status = ? WHERE id = ?",
                            (status, alert_id),
                        )
                    conn.commit()
                    return cursor.rowcount > 0
                finally:
                    conn.close()
        except Exception as e:
            self._logger.error(f"Database status update error: {e}")
            return False

    def get_alert_summary(self, days: int = 7) -> dict:
        """Aggregated counts per day/rule/camera including feedback split."""
        since = datetime.now().timestamp() - days * 86400
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT date(timestamp, 'unixepoch', 'localtime') AS day,
                               rule_id, rule_name, camera_id, status, COUNT(*) AS n
                        FROM alerts
                        WHERE timestamp >= ?
                        GROUP BY day, rule_id, camera_id, status
                        """,
                        (since,),
                    )
                    rows = [dict(r) for r in cursor.fetchall()]
                finally:
                    conn.close()

            by_day = {}
            by_rule = {}
            for r in rows:
                by_day.setdefault(r["day"], {"total": 0, "confirmed": 0,
                                             "false_positive": 0})
                by_day[r["day"]]["total"] += r["n"]
                if r["status"] in ("confirmed", "false_positive"):
                    by_day[r["day"]][r["status"]] += r["n"]

                key = r["rule_id"]
                entry = by_rule.setdefault(
                    key,
                    {"rule_id": key, "rule_name": r["rule_name"], "total": 0,
                     "confirmed": 0, "false_positive": 0, "cameras": {}},
                )
                entry["total"] += r["n"]
                if r["status"] in ("confirmed", "false_positive"):
                    entry[r["status"]] += r["n"]
                entry["cameras"][r["camera_id"]] = (
                    entry["cameras"].get(r["camera_id"], 0) + r["n"]
                )

            for entry in by_rule.values():
                reviewed = entry["confirmed"] + entry["false_positive"]
                entry["false_positive_rate"] = (
                    round(entry["false_positive"] / reviewed, 3) if reviewed else None
                )
            return {"days": days, "by_day": by_day, "by_rule": by_rule}
        except Exception as e:
            self._logger.error(f"Database summary error: {e}")
            return {"days": days, "by_day": {}, "by_rule": {}}

    def delete_older_than(self, cutoff_ts: float) -> int:
        """Retention helper: delete alerts older than cutoff. Returns deleted count."""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM alerts WHERE timestamp < ?", (cutoff_ts,))
                    conn.commit()
                    deleted = cursor.rowcount
                finally:
                    conn.close()
            if deleted:
                self._logger.info(f"Retention: deleted {deleted} alerts older than cutoff")
            return deleted
        except Exception as e:
            self._logger.error(f"Database retention error: {e}")
            return 0
