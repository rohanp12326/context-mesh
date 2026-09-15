"""Centralized application logging configuration for ContextMesh.

Provides rotating file logging and console logging with automatic
sensitive data (API keys, passwords, tokens, emails) scrubbing.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from observability.redaction import redact_sensitive_info

# Default log configuration
DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
DEFAULT_LOG_FILE = os.path.join(DEFAULT_LOG_DIR, "context_mesh.log")
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class RedactingFormatter(logging.Formatter):
    """Logging formatter that scrubs sensitive credentials and PII from log output."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_info(original)


_LOGGING_INITIALIZED = False


def setup_logging(
    log_file: Optional[str] = None,
    log_level: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Initialize root logger with both console and rotating file handlers."""
    global _LOGGING_INITIALIZED
    if _LOGGING_INITIALIZED:
        return

    # Determine file path and level from parameters or environment
    file_path = log_file or os.getenv("LOG_FILE_PATH", DEFAULT_LOG_FILE)
    level_name = log_level or os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # Ensure log directory exists
    log_dir = os.path.dirname(file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear pre-existing handlers to prevent duplicated logs across reruns/tests
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    formatter = RedactingFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # 1. Console Handler (stdout)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. Rotating File Handler
    try:
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        root_logger.warning(f"Failed to initialize file logging at '{file_path}': {e}")

    _LOGGING_INITIALIZED = True
    logging.getLogger("context_mesh").info(
        f"ContextMesh logging initialized. Level: {level_name} | File: {file_path}"
    )


def get_logger(name: str = "context_mesh") -> logging.Logger:
    """Retrieve or create a namespaced logger."""
    if not _LOGGING_INITIALIZED:
        setup_logging()
    return logging.getLogger(name)


def get_log_file_path() -> str:
    """Return the current path to the primary log file."""
    return os.getenv("LOG_FILE_PATH", DEFAULT_LOG_FILE)


def read_latest_logs(max_lines: int = 100) -> str:
    """Read the last N lines from the active log file."""
    path = get_log_file_path()
    if not os.path.exists(path):
        return "No log file found yet."
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return "".join(lines[-max_lines:])
    except Exception as e:
        return f"Error reading log file: {e}"
