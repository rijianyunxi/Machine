"""
Logging utilities for the machine vision system.

The root logger is configured exactly once (idempotent) with a console handler
and an optional rotating file handler. Every named logger (machine_vision,
camera.X, detector.X, ...) propagates to the root, so all output uses the same
format without duplicate handlers.
"""

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

_configured = False
_configure_lock = threading.Lock()


def setup_logger(
    name: str = "machine_vision",
    level: str = "INFO",
    log_file: str = "",
    max_size_mb: int = 50,
    backup_count: int = 5,
) -> logging.Logger:
    """
    Configure root logging once and return the named logger.

    Args:
        name: Logger name.
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to log file. Empty string for console only.
        max_size_mb: Maximum log file size in MB before rotation.
        backup_count: Number of rotated backup files to keep.

    Returns:
        Configured logging.Logger instance.
    """
    level_int = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    global _configured
    with _configure_lock:
        if not _configured:
            root = logging.getLogger()
            root.setLevel(level_int)

            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            root.addHandler(console_handler)

            if log_file:
                log_dir = os.path.dirname(log_file)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)

                file_handler = RotatingFileHandler(
                    log_file,
                    maxBytes=max_size_mb * 1024 * 1024,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                root.addHandler(file_handler)

            _configured = True

    logger = logging.getLogger(name)
    logger.setLevel(level_int)
    logger.propagate = True
    return logger


def get_logger(name: str = "machine_vision") -> logging.Logger:
    """Get an existing logger by name (messages propagate to root handlers)."""
    return logging.getLogger(name)
