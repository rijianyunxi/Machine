"""Alert persistence adapter backed by the unified ``storage/machine.db``."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.analyzer import Violation
from utils.logger import get_logger

from .machine_database import MachineDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AlertDatabase:
    """Thread-safe alert access using the shared machine database."""

    def __init__(self, settings: dict, database: MachineDatabase | None = None):
        self._logger = get_logger("database")
        db_cfg = settings.get("database", {}) if isinstance(settings, dict) else {}
        path = db_cfg.get("path", "storage/machine.db")
        timeout = db_cfg.get("busy_timeout_ms", 5000)
        self._database = database or MachineDatabase(path, timeout)
        self._db_path = str(self._database.path)
        self._lock = threading.RLock()
        self._logger.info("Unified database ready: %s", self._db_path)

    def _connect(self):
        return self._database.connect()

    def insert_alert(
        self, violation: Violation, snapshot_path: Optional[str] = None
    ) -> Optional[int]:
        try:
            dt_str = datetime.fromtimestamp(violation.timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            extra_str = json.dumps(violation.extra or {}, ensure_ascii=False)
            snapshot_created_at = time.time() if snapshot_path else None
            with self._lock, self._database.transaction() as conn:
                camera = conn.execute(
                    "SELECT name FROM cameras WHERE id = ?", (violation.camera_id,)
                ).fetchone()
                camera_name = camera[0] if camera else violation.camera_id
                cursor = conn.execute(
                    """
                    INSERT INTO alerts
                        (camera_id, camera_name, rule_id, rule_name, description,
                         confidence, severity, snapshot_path, snapshot_created_at,
                         timestamp, datetime_str, status, extra)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
                    """,
                    (
                        violation.camera_id, camera_name, violation.rule_id,
                        violation.rule_name, violation.description,
                        violation.confidence, violation.severity, snapshot_path,
                        snapshot_created_at, violation.timestamp, dt_str, extra_str,
                    ),
                )
                alert_id = cursor.lastrowid
            self._logger.info(
                "Alert #%s saved: [%s] R%02d %s",
                alert_id, violation.camera_id, violation.rule_id, violation.rule_name,
            )
            return alert_id
        except Exception as exc:
            self._logger.error("Database insert error: %s", exc)
            return None

    def get_alert_count(self, status: Optional[str] = None) -> int:
        try:
            with self._lock, self._database.connection() as conn:
                if status:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM alerts WHERE status = ?", (status,)
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()
                return int(row[0])
        except Exception as exc:
            self._logger.error("Database count error: %s", exc)
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
            with self._lock, self._database.connection() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM alerts {where}", args
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM alerts {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    args + [int(limit), int(offset)],
                ).fetchall()
                items = [self._with_snapshot_status(dict(row)) for row in rows]
            return {"items": items, "total": int(total)}
        except Exception as exc:
            self._logger.error("Database query error: %s", exc)
            return {"items": [], "total": 0}

    @staticmethod
    def _with_snapshot_status(item: dict) -> dict:
        """Expose a stable snapshot state without trusting the current config.

        ``available`` means the recorded file exists, ``cleaned`` means the
        retention job intentionally removed it, and ``missing`` means the
        record points to a file that disappeared unexpectedly.
        """
        snapshot_path = item.get("snapshot_path")
        if not snapshot_path:
            item["snapshot_status"] = "none"
            return item
        if item.get("snapshot_cleaned_at") is not None:
            item["snapshot_status"] = "cleaned"
            return item
        path = Path(str(snapshot_path)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        item["snapshot_status"] = "available" if path.is_file() else "missing"
        return item

    def mark_snapshots_cleaned(self, directories) -> int:
        """Mark alerts whose files were removed by snapshot retention."""
        roots = []
        for directory in directories or []:
            try:
                roots.append(Path(directory).resolve())
            except (TypeError, OSError):
                continue
        if not roots:
            return 0
        try:
            with self._lock, self._database.transaction() as conn:
                rows = conn.execute(
                    "SELECT id, snapshot_path FROM alerts "
                    "WHERE snapshot_path IS NOT NULL AND snapshot_cleaned_at IS NULL"
                ).fetchall()
                ids = []
                for row in rows:
                    try:
                        candidate = Path(str(row[1])).expanduser().resolve()
                    except (TypeError, OSError):
                        continue
                    if any(candidate == root or root in candidate.parents for root in roots):
                        ids.append(int(row[0]))
                if not ids:
                    return 0
                now = time.time()
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"UPDATE alerts SET snapshot_cleaned_at = ? WHERE id IN ({placeholders})",
                    [now, *ids],
                )
                return int(cursor.rowcount)
        except Exception as exc:
            self._logger.error("Database snapshot status update error: %s", exc)
            return 0

    def update_alert_status(
        self, alert_id: int, status: str, note: Optional[str] = None
    ) -> bool:
        valid = {"new", "confirmed", "false_positive", "resolved"}
        if status not in valid:
            raise ValueError(f"非法状态: {status}，可选 {sorted(valid)}")
        try:
            with self._lock, self._database.transaction() as conn:
                if note is not None:
                    row = conn.execute(
                        "SELECT extra FROM alerts WHERE id = ?", (alert_id,)
                    ).fetchone()
                    extra = {}
                    if row and row[0]:
                        try:
                            extra = json.loads(row[0])
                        except (TypeError, json.JSONDecodeError):
                            extra = {}
                    extra["note"] = note
                    cursor = conn.execute(
                        "UPDATE alerts SET status = ?, extra = ? WHERE id = ?",
                        (status, json.dumps(extra, ensure_ascii=False), alert_id),
                    )
                else:
                    cursor = conn.execute(
                        "UPDATE alerts SET status = ? WHERE id = ?",
                        (status, alert_id),
                    )
                return cursor.rowcount > 0
        except Exception as exc:
            self._logger.error("Database status update error: %s", exc)
            return False

    def get_alert_summary(self, days: int = 7) -> dict:
        since = datetime.now().timestamp() - days * 86400
        try:
            with self._lock, self._database.connection() as conn:
                rows = [dict(r) for r in conn.execute(
                    """
                    SELECT date(timestamp, 'unixepoch', 'localtime') AS day,
                           rule_id, rule_name, camera_id, status, COUNT(*) AS n
                    FROM alerts
                    WHERE timestamp >= ?
                    GROUP BY day, rule_id, camera_id, status
                    """,
                    (since,),
                ).fetchall()]
            by_day, by_rule = {}, {}
            for row in rows:
                by_day.setdefault(row["day"], {
                    "total": 0, "confirmed": 0, "false_positive": 0,
                })
                by_day[row["day"]]["total"] += row["n"]
                if row["status"] in ("confirmed", "false_positive"):
                    by_day[row["day"]][row["status"]] += row["n"]
                key = row["rule_id"]
                entry = by_rule.setdefault(key, {
                    "rule_id": key, "rule_name": row["rule_name"], "total": 0,
                    "confirmed": 0, "false_positive": 0, "cameras": {},
                })
                entry["total"] += row["n"]
                if row["status"] in ("confirmed", "false_positive"):
                    entry[row["status"]] += row["n"]
                entry["cameras"][row["camera_id"]] = (
                    entry["cameras"].get(row["camera_id"], 0) + row["n"]
                )
            for entry in by_rule.values():
                reviewed = entry["confirmed"] + entry["false_positive"]
                entry["false_positive_rate"] = (
                    round(entry["false_positive"] / reviewed, 3) if reviewed else None
                )
            return {"days": days, "by_day": by_day, "by_rule": by_rule}
        except Exception as exc:
            self._logger.error("Database summary error: %s", exc)
            return {"days": days, "by_day": {}, "by_rule": {}}

    def delete_older_than(self, cutoff_ts: float, batch_size: int = 5000) -> int:
        """Delete in short write transactions so config writes remain responsive."""
        total = 0
        try:
            while True:
                with self._lock, self._database.transaction() as conn:
                    rows = conn.execute(
                        "SELECT id FROM alerts WHERE timestamp < ? ORDER BY id LIMIT ?",
                        (cutoff_ts, int(batch_size)),
                    ).fetchall()
                    if not rows:
                        break
                    ids = [row[0] for row in rows]
                    placeholders = ",".join("?" for _ in ids)
                    cursor = conn.execute(
                        f"DELETE FROM alerts WHERE id IN ({placeholders})", ids
                    )
                    total += cursor.rowcount
                if len(rows) < batch_size:
                    break
            if total:
                self._logger.info("Retention: deleted %s old alerts", total)
            return total
        except Exception as exc:
            self._logger.error("Database retention error: %s", exc)
            return total