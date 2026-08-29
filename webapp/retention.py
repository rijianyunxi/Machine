"""
Retention: snapshot / alert-DB / test-result pruning.

Runs once at startup (to catch up after downtime) and then daily at 03:00,
plus on demand from the panel.
"""

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("panel.retention")


def run_retention(state) -> dict:
    settings = state.settings()
    result = {}

    # Snapshots: delete day dirs older than retention_days
    snap_days = int(settings.get("snapshot", {}).get("retention_days", 30))
    cutoff_date = (datetime.now() - timedelta(days=snap_days)).strftime("%Y-%m-%d")
    result["snapshots_deleted_dirs"] = state.cleanup_snapshots(cutoff_date)
    result["snapshots_cutoff"] = cutoff_date

    # Alerts DB
    db_days = int(settings.get("database", {}).get("retention_days", 180))
    cutoff_ts = time.time() - db_days * 86400
    result["alerts_deleted"] = state.db.delete_older_than(cutoff_ts)

    # Test results: keep newest 50
    results_dir = Path(__file__).resolve().parent.parent / "storage" / "test_results"
    if results_dir.exists():
        files = sorted(results_dir.glob("test_*.jpg"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        removed = 0
        for old in files[50:]:
            old.unlink(missing_ok=True)
            removed += 1
        result["test_results_removed"] = removed

    logger.info(f"Retention done: {result}")
    return result


def start_retention_thread(state):
    """Startup pass + daily 03:00."""

    def loop():
        try:
            run_retention(state)
        except Exception as e:
            logger.error(f"Startup retention failed: {e}")

        while True:
            now = datetime.now()
            nxt = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0,
                                                    microsecond=0)
            time.sleep(max((nxt - now).total_seconds(), 60))
            try:
                run_retention(state)
            except Exception as e:
                logger.error(f"Scheduled retention failed: {e}")

    threading.Thread(target=loop, name="panel-retention", daemon=True).start()
