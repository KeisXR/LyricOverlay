"""Validated, atomically persisted application settings.

Unknown keys are preserved for forward compatibility. Known keys are
normalised to the type and range defined by ``DEFAULT_SETTINGS`` and the
explicit constraints below. Internal changes are visible immediately and are
persisted as a debounced atomic replace; external editor changes are reloaded
through the same change-notification path.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QFileSystemWatcher, QTimer, Signal


logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "window": {
        "screen_index": 0,
        "anchor": "bottom-center",
        "offset_x": 0,
        "offset_y": -100,
        "width_pct": 60,
        "height_px": 300,
        "font_size": 24,
        "font_family": "sans-serif",
        "text_color": "#ffffff",
        "text_shadow_color": "rgba(0,0,0,0.6)",
        "highlight_color": "#ffcc00",
        "visible_lines": 5,
        "background_enabled": False,
        "background_color": "rgba(0,0,0,0.45)",
        "lyrics_offset_ms": 0,
        "user_x": None,
        "user_y": None,
        "text_shadow": True,
        "show_seekbar": True,
        "karaoke_enabled": True,
    },
    "behavior": {
        "start_minimized": False,
        "auto_hide_controls": True,
        "hide_delay_ms": 2000,
        "pinned_player": None,
        "remember_position": True,
        "always_on_top": True,
        "smtc_position_fallback": True,
        "ws_port": 56789,
    },
    "sources": {
        "lrclib": {"enabled": True},
        "musixmatch": {"enabled": False, "api_key": ""},
        "syncedlyrics": {"enabled": True, "enhanced": True},
    },
    "cache": {
        "ttl_days": 30,
        "max_entries": 10000,
    },
}

_ANCHORS = {
    "top-left",
    "top-center",
    "top-right",
    "center",
    "bottom-left",
    "bottom-center",
    "bottom-right",
}

_INT_RANGES: dict[str, tuple[int, int]] = {
    "window.screen_index": (0, 64),
    "window.offset_x": (-100000, 100000),
    "window.offset_y": (-100000, 100000),
    "window.width_pct": (10, 100),
    "window.height_px": (40, 4320),
    "window.font_size": (8, 256),
    "window.visible_lines": (1, 50),
    "window.lyrics_offset_ms": (-60000, 60000),
    "behavior.hide_delay_ms": (0, 60000),
    "behavior.ws_port": (1024, 65535),
    "cache.ttl_days": (1, 3650),
    "cache.max_entries": (100, 1000000),
}

_NULLABLE_INTS = {
    "window.user_x": (-100000, 100000),
    "window.user_y": (-100000, 100000),
}

_STRING_LIMITS = {
    "window.font_family": 256,
    "window.text_color": 128,
    "window.text_shadow_color": 128,
    "window.highlight_color": 128,
    "window.background_color": 128,
    "sources.musixmatch.api_key": 4096,
}


def _deep_copy_defaults() -> dict:
    return copy.deepcopy(DEFAULT_SETTINGS)


def _get_path(data: dict, path: str, default=None):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _set_path(data: dict, path: str, value) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _flatten(data: dict, prefix: str = "") -> dict[str, object]:
    flattened: dict[str, object] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flatten(value, path))
        else:
            flattened[path] = value
    return flattened


def _normalise_scalar(path: str, value, default):
    if path in _NULLABLE_INTS:
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            minimum, maximum = _NULLABLE_INTS[path]
            return max(minimum, min(maximum, value))
        return default

    if path == "behavior.pinned_player":
        if value is None:
            return None
        if isinstance(value, str) and len(value) <= 512:
            stripped = value.strip()
            return stripped or None
        return default

    if path == "window.anchor":
        return value if isinstance(value, str) and value in _ANCHORS else default

    if path in _INT_RANGES:
        if isinstance(value, int) and not isinstance(value, bool):
            minimum, maximum = _INT_RANGES[path]
            return max(minimum, min(maximum, value))
        return default

    if isinstance(default, bool):
        return value if isinstance(value, bool) else default

    if isinstance(default, str):
        if not isinstance(value, str):
            return default
        maximum = _STRING_LIMITS.get(path, 1024)
        return value if len(value) <= maximum else default

    return value if isinstance(value, type(default)) else default


def normalise_settings(loaded) -> dict:
    """Return a safe settings object while preserving unknown keys."""
    if not isinstance(loaded, dict):
        return _deep_copy_defaults()

    result = copy.deepcopy(loaded)
    defaults = _flatten(DEFAULT_SETTINGS)
    for path, default in defaults.items():
        value = _get_path(loaded, path, default)
        _set_path(result, path, _normalise_scalar(path, value, default))
    return result


class Settings(QObject):
    """JSON settings with validation, atomic persistence, and hot reload."""

    changed = Signal()
    value_changed = Signal(str, object)
    save_failed = Signal(str)

    SAVE_DEBOUNCE_MS = 250
    RELOAD_DEBOUNCE_MS = 150

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        config_dir: str | Path | None = None,
    ):
        super().__init__(parent)
        if config_dir is None:
            if sys.platform == "win32":
                config_home = os.environ.get(
                    "APPDATA", str(Path.home() / "AppData" / "Roaming")
                )
            else:
                config_home = os.environ.get(
                    "XDG_CONFIG_HOME", str(Path.home() / ".config")
                )
            self._config_dir = Path(config_home) / "lyricaod"
        else:
            self._config_dir = Path(config_dir)

        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._file = self._config_dir / "settings.json"
        self._writing = False
        self._dirty = False
        self._last_error = ""
        self._data = self._load_from_disk(fallback=_deep_copy_defaults())

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_watched_change)
        self._watcher.directoryChanged.connect(self._on_watched_change)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_now)

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(self.RELOAD_DEBOUNCE_MS)
        self._reload_timer.timeout.connect(self._reload_from_disk)

        self._ensure_watch_paths()
        if not self._file.exists():
            self._dirty = True
            self._save_now()

    @property
    def path(self) -> Path:
        return self._file

    @property
    def last_error(self) -> str:
        return self._last_error

    def _load_from_disk(self, *, fallback: dict) -> dict:
        if not self._file.exists():
            return normalise_settings(fallback)
        try:
            with self._file.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            logger.warning("Unable to load settings from %s: %s", self._file, exc)
            self._last_error = str(exc)
            return copy.deepcopy(fallback)
        return normalise_settings(loaded)

    def _ensure_watch_paths(self):
        directory = str(self._config_dir)
        if directory not in self._watcher.directories():
            self._watcher.addPath(directory)
        file_path = str(self._file)
        if self._file.exists() and file_path not in self._watcher.files():
            self._watcher.addPath(file_path)

    def _on_watched_change(self, _path: str):
        if self._writing:
            return
        self._reload_timer.start()

    def _reload_from_disk(self):
        self._ensure_watch_paths()
        if not self._file.exists():
            return

        previous = self._data
        loaded = self._load_from_disk(fallback=previous)
        if loaded == previous:
            return

        self._data = loaded
        self._emit_diff(previous, loaded)
        self.changed.emit()
        self._ensure_watch_paths()

    def _emit_diff(self, old: dict, new: dict):
        old_flat = _flatten(old)
        new_flat = _flatten(new)
        for path in sorted(set(old_flat) | set(new_flat)):
            if old_flat.get(path) != new_flat.get(path):
                self.value_changed.emit(path, new_flat.get(path))

    def _schedule_save(self):
        self._dirty = True
        self._save_timer.start()

    def _save_now(self) -> bool:
        if not self._dirty and self._file.exists():
            return True

        temp_path: Path | None = None
        self._writing = True
        self._watcher.blockSignals(True)
        try:
            fd, raw_path = tempfile.mkstemp(
                prefix="settings.", suffix=".tmp", dir=self._config_dir
            )
            temp_path = Path(raw_path)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._file)
            temp_path = None
            try:
                directory_fd = os.open(self._config_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            self._dirty = False
            self._last_error = ""
        except OSError as exc:
            logger.error("Unable to save settings to %s: %s", self._file, exc)
            self._last_error = str(exc)
            self.save_failed.emit(str(exc))
            return False
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._watcher.blockSignals(False)
            self._writing = False
            self._ensure_watch_paths()

        self.changed.emit()
        return True

    def flush(self) -> bool:
        """Persist pending changes immediately, primarily for shutdown."""
        self._save_timer.stop()
        return self._save_now()

    def get(self, key: str, default=None):
        return _get_path(self._data, key, default)

    def set(self, key: str, value):
        default = _get_path(DEFAULT_SETTINGS, key, None)
        if default is not None or key in {
            "window.user_x",
            "window.user_y",
            "behavior.pinned_player",
        }:
            value = _normalise_scalar(key, value, default)

        old_value = self.get(key, object())
        if old_value == value:
            return
        _set_path(self._data, key, value)
        self.value_changed.emit(key, value)
        self._schedule_save()
