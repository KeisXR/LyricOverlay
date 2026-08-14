"""Tests for validated and atomic settings persistence."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path


class _SignalInstance:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _SignalDescriptor:
    def __set_name__(self, owner, name):
        self.name = f"__sig_{name}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.__dict__.setdefault(self.name, _SignalInstance())


def _signal(*_args):
    return _SignalDescriptor()


class _QObject:
    def __init__(self, parent=None):
        pass


class _Timer:
    def __init__(self, parent=None):
        self.timeout = _SignalInstance()
        self.start_count = 0
        self.stopped = False

    def setSingleShot(self, _value):
        pass

    def setInterval(self, value):
        self.interval = value

    def start(self):
        self.start_count += 1

    def stop(self):
        self.stopped = True


class _Watcher:
    def __init__(self, parent=None):
        self.fileChanged = _SignalInstance()
        self.directoryChanged = _SignalInstance()
        self._files = []
        self._directories = []
        self.blocked = False

    def addPath(self, path):
        if os.path.isdir(path):
            if path not in self._directories:
                self._directories.append(path)
        elif path not in self._files:
            self._files.append(path)
        return True

    def files(self):
        return list(self._files)

    def directories(self):
        return list(self._directories)

    def blockSignals(self, value):
        self.blocked = value


qtcore = types.ModuleType("PySide6.QtCore")
qtcore.QObject = _QObject
qtcore.QFileSystemWatcher = _Watcher
qtcore.QTimer = _Timer
qtcore.Signal = _signal
pyside = types.ModuleType("PySide6")
pyside.QtCore = qtcore
sys.modules.update({"PySide6": pyside, "PySide6.QtCore": qtcore})

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "config" / "settings.py"
spec = importlib.util.spec_from_file_location("settings_under_test", MODULE_PATH)
settings_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = settings_module
spec.loader.exec_module(settings_module)


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_non_object_top_level_uses_defaults(tmp_path):
    for value in ([], None, "bad"):
        _write(tmp_path / "settings.json", value)
        settings = settings_module.Settings(config_dir=tmp_path)
        assert settings.get("window.font_size") == 24
        assert settings.get("behavior.ws_port") == 56789


def test_known_values_are_type_checked_and_clamped(tmp_path):
    _write(
        tmp_path / "settings.json",
        {
            "window": {
                "font_size": 9999,
                "visible_lines": "many",
                "anchor": "elsewhere",
            },
            "behavior": {"ws_port": 1, "hide_delay_ms": -5},
            "cache": {"ttl_days": 99999, "max_entries": 1},
            "future": {"key": "preserved"},
        },
    )
    settings = settings_module.Settings(config_dir=tmp_path)

    assert settings.get("window.font_size") == 256
    assert settings.get("window.visible_lines") == 5
    assert settings.get("window.anchor") == "bottom-center"
    assert settings.get("behavior.ws_port") == 1024
    assert settings.get("behavior.hide_delay_ms") == 0
    assert settings.get("cache.ttl_days") == 3650
    assert settings.get("cache.max_entries") == 100
    assert settings.get("future.key") == "preserved"


def test_set_emits_immediate_value_change_but_debounces_disk_write(tmp_path):
    settings = settings_module.Settings(config_dir=tmp_path)
    events = []
    batches = []
    settings.value_changed.connect(lambda key, value: events.append((key, value)))
    settings.changed.connect(lambda: batches.append(True))

    for value in range(100):
        settings.set("window.offset_x", value)

    assert events[-1] == ("window.offset_x", 99)
    assert settings._save_timer.start_count == 99
    assert batches == []
    assert settings.flush()
    assert len(batches) == 1
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["window"]["offset_x"] == 99


def test_atomic_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    settings = settings_module.Settings(config_dir=tmp_path)
    settings.set("window.font_size", 42)
    assert settings.flush()
    before = (tmp_path / "settings.json").read_bytes()
    settings.set("window.font_size", 43)

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(settings_module.os, "replace", fail_replace)
    assert not settings.flush()
    assert (tmp_path / "settings.json").read_bytes() == before
    assert settings.get("window.font_size") == 43


def test_external_reload_uses_same_notifications(tmp_path):
    settings = settings_module.Settings(config_dir=tmp_path)
    events = []
    batches = []
    settings.value_changed.connect(lambda key, value: events.append((key, value)))
    settings.changed.connect(lambda: batches.append(True))
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    data["window"]["font_size"] = 48
    _write(tmp_path / "settings.json", data)

    settings._reload_from_disk()

    assert ("window.font_size", 48) in events
    assert len(batches) == 1


def test_replaced_file_is_readded_to_watcher(tmp_path):
    settings = settings_module.Settings(config_dir=tmp_path)
    settings._watcher._files.clear()

    settings._ensure_watch_paths()

    assert str(tmp_path / "settings.json") in settings._watcher.files()


def test_nullable_position_and_pin_are_normalised(tmp_path):
    settings = settings_module.Settings(config_dir=tmp_path)
    settings.set("window.user_x", "invalid")
    settings.set("behavior.pinned_player", "   ")

    assert settings.get("window.user_x") is None
    assert settings.get("behavior.pinned_player") is None
