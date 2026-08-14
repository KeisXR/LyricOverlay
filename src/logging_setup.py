"""Application-wide structured logging with safe fallbacks."""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


LOGGER_NAME = "lyricaod"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|pairing[_-]?token|authorization|password)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_LRC_PATTERN = re.compile(r"(?:^|\n)\s*\[\d{1,3}:\d{1,2}(?:\.\d+)?\].+")


@dataclass(frozen=True)
class LoggingState:
    log_dir: Path
    log_file: Path | None
    fallback: bool
    error: str = ""


class PrivacyFilter(logging.Filter):
    """Redact common credentials and accidental timestamped lyric lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        message = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", message)
        message = _BEARER_PATTERN.sub("Bearer <redacted>", message)
        if _LRC_PATTERN.search(message):
            message = _LRC_PATTERN.sub("\n<lyrics-redacted>", message)
        record.msg = message
        record.args = ()
        return True


def default_log_dir() -> Path:
    if sys.platform == "win32":
        base = Path(
            os.environ.get(
                "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
            )
        )
    else:
        base = Path(
            os.environ.get(
                "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
            )
        )
    return base / "lyricaod" / "logs"


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def configure_logging(
    *,
    level: int = logging.INFO,
    log_dir: str | Path | None = None,
    console: bool | None = None,
) -> LoggingState:
    """Configure the ``lyricaod`` logger and return its active paths.

    File logging failures never prevent application startup. In that case a
    stderr handler is installed when possible; otherwise a ``NullHandler`` is
    used so recoverable logging failures cannot become runtime failures.
    """
    target_dir = Path(log_dir) if log_dir is not None else default_log_dir()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    privacy = PrivacyFilter()
    formatter = _formatter()
    log_file: Path | None = None
    error = ""
    fallback = False

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        log_file = target_dir / "lyricaod.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(privacy)
        logger.addHandler(file_handler)
    except Exception as exc:
        fallback = True
        error = str(exc)
        log_file = None

    if console is None:
        console = not bool(getattr(sys, "frozen", False))
    if console or not logger.handlers:
        try:
            stream_handler = logging.StreamHandler(sys.stderr)
            stream_handler.setLevel(level)
            stream_handler.setFormatter(formatter)
            stream_handler.addFilter(privacy)
            logger.addHandler(stream_handler)
        except Exception:
            fallback = True

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
        fallback = True

    if error:
        logger.warning("File logging unavailable; using fallback handler: %s", error)
    else:
        logger.debug("Logging initialized at %s", log_file)
    return LoggingState(target_dir, log_file, fallback, error)


def get_logger(name: str) -> logging.Logger:
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
