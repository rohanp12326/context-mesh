"""Unit tests for centralized logging and credential redaction."""

import os
import pytest
from observability.logging import setup_logging, get_logger, read_latest_logs, get_log_file_path


def test_logging_writes_to_file(tmp_path):
    log_file = str(tmp_path / "test_app.log")
    os.environ["LOG_FILE_PATH"] = log_file

    # Reset logging initialized flag for isolated test
    import observability.logging as ob_log
    ob_log._LOGGING_INITIALIZED = False

    setup_logging(log_file=log_file, log_level="DEBUG")
    logger = get_logger("test.runner")

    test_message = "ContextMesh test log execution message"
    logger.info(test_message)

    assert os.path.exists(log_file)
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert test_message in content


def test_logging_redacts_sensitive_tokens(tmp_path):
    log_file = str(tmp_path / "test_redaction.log")

    import observability.logging as ob_log
    ob_log._LOGGING_INITIALIZED = False

    setup_logging(log_file=log_file, log_level="DEBUG")
    logger = get_logger("test.security")

    secret_key = "Bearer secret_jwt_token_123456"
    logger.info(f"Authenticating with {secret_key}")

    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()

    assert "secret_jwt_token_123456" not in content
    assert "[REDACTED_TOKEN]" in content


def test_read_latest_logs(tmp_path):
    log_file = str(tmp_path / "test_read.log")
    os.environ["LOG_FILE_PATH"] = log_file

    import observability.logging as ob_log
    ob_log._LOGGING_INITIALIZED = False

    setup_logging(log_file=log_file, log_level="INFO")
    logger = get_logger("test.reader")

    for i in range(10):
        logger.info(f"Log line entry number {i}")

    logs = read_latest_logs(max_lines=5)
    assert "Log line entry number 9" in logs
    assert "Log line entry number 8" in logs
