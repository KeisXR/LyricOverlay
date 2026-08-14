"""Non-interactive diagnostics and packaged-artifact self tests."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path

from logging_setup import LoggingState, configure_logging, default_log_dir, get_logger


logger = get_logger(__name__)
SOURCE_VERSION = "0.1.1+source"


def application_version() -> str:
    try:
        return importlib.metadata.version("lyricaod")
    except importlib.metadata.PackageNotFoundError:
        return SOURCE_VERSION


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(
            os.environ.get(
                "APPDATA", str(Path.home() / "AppData" / "Roaming")
            )
        )
    else:
        base = Path(
            os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        )
    return base / "lyricaod"


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(
            os.environ.get(
                "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
            )
        )
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "lyricaod"


def _module_status(name: str) -> dict:
    try:
        specification = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError) as exc:
        return {"available": False, "error": str(exc)}
    if specification is None:
        return {"available": False, "error": "not found"}
    try:
        version = importlib.metadata.version(name.split(".")[0])
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return {"available": True, "version": version}


def collect_diagnostics(
    *,
    settings=None,
    logging_state: LoggingState | None = None,
) -> dict:
    enabled = {}
    if settings is not None:
        enabled = {
            "lrclib": bool(settings.get("sources.lrclib.enabled", True)),
            "syncedlyrics": bool(
                settings.get("sources.syncedlyrics.enabled", True)
            ),
            "browser_port": settings.get("behavior.ws_port", 56789),
            "smtc_fallback": bool(
                settings.get("behavior.smtc_position_fallback", True)
            ),
        }

    qt_platform = "not-initialized"
    try:
        from PySide6.QtGui import QGuiApplication

        instance = QGuiApplication.instance()
        if instance is not None:
            qt_platform = QGuiApplication.platformName()
    except Exception as exc:
        qt_platform = f"unavailable: {exc}"

    dependencies = {
        name: _module_status(name)
        for name in (
            "PySide6",
            "httpx",
            "websockets",
            "syncedlyrics",
            "dbus" if sys.platform != "win32" else "winrt",
        )
    }
    return {
        "application": "Lyricaod",
        "version": application_version(),
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "qt_platform": qt_platform,
        "paths": {
            "config": str(config_dir()),
            "cache": str(cache_dir()),
            "logs": str(
                logging_state.log_dir if logging_state else default_log_dir()
            ),
            "log_file": str(logging_state.log_file)
            if logging_state and logging_state.log_file
            else None,
        },
        "enabled": enabled,
        "dependencies": dependencies,
    }


def format_diagnostics(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def run_self_test() -> tuple[bool, list[str]]:
    """Exercise packaged imports and local storage without external services."""
    failures: list[str] = []
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    required_modules = [
        "PySide6",
        "httpx",
        "websockets",
        "syncedlyrics",
        "config.settings",
        "lyrics.manager",
        "lyrics.lrc_parser",
        "ui.overlay",
        "player",
    ]
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f"import {module_name}: {exc}")

    try:
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication(
            ["lyricaod-self-test"]
        )
        if application is None:
            failures.append("Qt application initialization returned no instance")
    except Exception as exc:
        failures.append(f"Qt application initialization: {exc}")

    with tempfile.TemporaryDirectory(prefix="lyricaod-self-test-") as raw_temp:
        temporary = Path(raw_temp)
        try:
            config_file = temporary / "config" / "settings.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text('{"ok": true}\n', encoding="utf-8")
            json.loads(config_file.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"configuration storage: {exc}")

        try:
            database = sqlite3.connect(temporary / "cache" / "cache.db")
        except sqlite3.Error:
            (temporary / "cache").mkdir(parents=True, exist_ok=True)
            try:
                database = sqlite3.connect(temporary / "cache" / "cache.db")
            except sqlite3.Error as exc:
                failures.append(f"SQLite open: {exc}")
                database = None
        if database is not None:
            try:
                database.execute("CREATE TABLE self_test(value INTEGER)")
                database.execute("INSERT INTO self_test VALUES (1)")
                database.commit()
                assert database.execute("SELECT value FROM self_test").fetchone() == (1,)
            except Exception as exc:
                failures.append(f"SQLite operation: {exc}")
            finally:
                database.close()

        try:
            test_logging = configure_logging(
                log_dir=temporary / "logs", console=False
            )
            get_logger("self_test").info("self-test logging")
            for handler in get_logger("self_test").handlers:
                handler.flush()
            if test_logging.log_file and not test_logging.log_file.exists():
                failures.append("logging did not create the expected file")
        except Exception as exc:
            failures.append(f"logging initialization: {exc}")

    if failures:
        for failure in failures:
            logger.error("Self-test failure: %s", failure)
    else:
        logger.info("Self-test completed successfully")
    return not failures, failures
