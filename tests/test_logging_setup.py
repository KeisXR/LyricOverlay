import logging
import sys

sys.path.insert(0, "src")

import logging_setup


def _flush_logger():
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    for handler in logger.handlers:
        handler.flush()
    return logger


def test_rotating_log_respects_size_and_backup_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "MAX_LOG_BYTES", 256)
    monkeypatch.setattr(logging_setup, "BACKUP_COUNT", 2)
    state = logging_setup.configure_logging(
        log_dir=tmp_path, console=False, level=logging.DEBUG
    )
    logger = logging_setup.get_logger("rotation")
    for index in range(200):
        logger.info("line %03d %s", index, "x" * 40)
    _flush_logger()

    files = sorted(tmp_path.glob("lyricaod.log*"))
    assert state.log_file in files
    assert len(files) <= 3


def test_privacy_filter_redacts_credentials_and_lyrics(tmp_path):
    state = logging_setup.configure_logging(
        log_dir=tmp_path, console=False, level=logging.DEBUG
    )
    logger = logging_setup.get_logger("privacy")
    logger.warning(
        "api_key=secret pairing-token:abc Authorization=BearerToken "
        "Bearer actual.jwt.value\n[00:01.00]copyrighted lyric line"
    )
    _flush_logger()

    content = state.log_file.read_text(encoding="utf-8")
    assert "secret" not in content
    assert "abc" not in content
    assert "actual.jwt.value" not in content
    assert "copyrighted lyric line" not in content
    assert "<redacted>" in content
    assert "<lyrics-redacted>" in content


def test_file_logging_failure_uses_fallback_handler(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")

    state = logging_setup.configure_logging(
        log_dir=occupied / "logs", console=False
    )

    assert state.fallback
    assert state.log_file is None
    assert logging.getLogger(logging_setup.LOGGER_NAME).handlers


def test_capture_legacy_prints_only_changes_frozen_process(monkeypatch):
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        logging_setup.configure_logging(console=False)
        logging_setup.capture_legacy_prints()
        assert isinstance(sys.stdout, logging_setup.LoggingStream)
        assert isinstance(sys.stderr, logging_setup.LoggingStream)
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
