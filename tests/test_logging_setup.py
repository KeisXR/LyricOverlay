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


def test_file_logging_failure_with_captured_stderr_does_not_recurse(
    tmp_path, monkeypatch, capsys
):
    # Packaged builds run capture_legacy_prints() before configure_logging(),
    # so the fallback handler must not wrap the capture stream: that made the
    # warning below recurse into the same handler until RecursionError.
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        logging_setup.capture_legacy_prints()
        state = logging_setup.configure_logging(
            log_dir=occupied / "logs", console=True
        )
        _flush_logger()
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    assert state.fallback
    assert state.log_file is None
    assert "File logging unavailable" in capsys.readouterr().err


def test_handle_error_write_into_capture_stream_does_not_recurse(
    tmp_path, monkeypatch, capsys
):
    # delay=True lets the log file open fail on the first emit; handleError
    # then writes the traceback to sys.stderr, which is the capture stream.
    class FailingHandler(logging.Handler):
        def emit(self, record):
            try:
                raise OSError("disk full")
            except OSError:
                self.handleError(record)

    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    logging_setup.configure_logging(log_dir=tmp_path, console=False)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.addHandler(FailingHandler())
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        logging_setup.capture_legacy_prints()
        logger.warning("delayed log file open failed")
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        logging_setup.configure_logging(log_dir=tmp_path, console=False)

    assert "disk full" in capsys.readouterr().err


def test_capture_legacy_prints_only_changes_frozen_process(
    tmp_path, monkeypatch
):
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        logging_setup.configure_logging(log_dir=tmp_path, console=False)
        logging_setup.capture_legacy_prints()
        assert isinstance(sys.stdout, logging_setup.LoggingStream)
        assert isinstance(sys.stderr, logging_setup.LoggingStream)
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr


def test_captured_metadata_is_debug_only(tmp_path):
    state = logging_setup.configure_logging(
        log_dir=tmp_path, console=False, level=logging.INFO
    )
    stream = logging_setup.LoggingStream(
        logging_setup.get_logger("stdout"), logging.INFO
    )
    stream.write('[MPRIS] meta: title="Song" artist="Artist"\n')
    stream.write("[MPRIS] Failed to subscribe\n")
    _flush_logger()

    content = state.log_file.read_text(encoding="utf-8")
    assert "Song" not in content
    assert "Artist" not in content
    assert "Failed to subscribe" in content
