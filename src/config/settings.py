"""
Settings manager with JSON persistence and file-watching for hot-reload.

Stores settings in a platform-appropriate config directory:
  - Linux/macOS: ``$XDG_CONFIG_HOME/lyricaod/settings.json``
    (defaults to ``~/.config/lyricaod/settings.json``)
  - Windows:     ``%APPDATA%\\lyricaod\\settings.json``

Uses ``QFileSystemWatcher`` to detect external edits at runtime.
"""

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QFileSystemWatcher, Signal

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


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Settings(QObject):
    """Application settings with JSON persistence and hot-reload.

    Signals
    -------
    changed()
        Emitted when the settings file is modified externally.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        if sys.platform == "win32":
            config_home = os.environ.get(
                "APPDATA", str(Path.home() / "AppData" / "Roaming")
            )
        else:
            config_home = os.environ.get(
                "XDG_CONFIG_HOME", str(Path.home() / ".config")
            )
        self._config_dir = Path(config_home) / "lyricaod"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._file = self._config_dir / "settings.json"
        self._data = self._load()

        self._watcher = QFileSystemWatcher(self)
        if not self._file.exists():
            self._save()
        self._watcher.addPath(str(self._file))
        self._watcher.fileChanged.connect(self._on_file_changed)

    def _load(self) -> dict:
        if self._file.exists():
            try:
                with open(self._file, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                # Start from a fresh copy of defaults so keys never go missing
                base = json.loads(json.dumps(DEFAULT_SETTINGS))
                return _deep_merge(base, loaded)
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return json.loads(json.dumps(DEFAULT_SETTINGS))

    def _save(self):
        self._watcher.blockSignals(True)
        try:
            with open(self._file, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
        finally:
            self._watcher.blockSignals(False)

    def _on_file_changed(self, path: str):
        self._data = self._load()
        self.changed.emit()
        # Re-add – some text editors replace the file (delete + recreate)
        if path not in self._watcher.files():
            self._watcher.addPath(path)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        """Get a setting by dot-separated key, e.g. ``"window.font_size"``."""
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, ...)
                if value is ...:
                    return default
            else:
                return default
        return value

    def set(self, key: str, value):
        """Set a setting by dot-separated key and persist immediately."""
        keys = key.split(".")
        target = self._data
        for k in keys[:-1]:
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        self._save()
