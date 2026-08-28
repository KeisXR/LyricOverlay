"""Logic tests for tray-less fallback and automatic player selection."""

import contextlib
import importlib.util
import sys
import types
from pathlib import Path


class _Timer:
    @staticmethod
    def singleShot(_delay, callback):
        callback()


class _DummyMeta(type):
    def __getattr__(cls, _name):
        return _Dummy()


class _Dummy(metaclass=_DummyMeta):
    """Permissive Qt stand-in: unknown attributes are no-op dummies.

    The stubs have to be forgiving enough for ``TrayIcon.create()`` to run to
    completion, otherwise its broad ``except Exception`` would hand back the
    fallback for the wrong reason and hide the bug under test.
    """

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, _name):
        return _Dummy()

    def __call__(self, *args, **kwargs):
        return _Dummy()

    def __iter__(self):
        return iter(())


class _Tray(_Dummy):
    tray_available = True

    class ActivationReason:
        Trigger = 1

    @classmethod
    def isSystemTrayAvailable(cls):
        return cls.tray_available


class _Qt:
    class AlignmentFlag:
        AlignCenter = 1


@contextlib.contextmanager
def _stubbed_modules(modules):
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _load_tray_module():
    """Import src/ui/tray.py without leaving stubs behind in sys.modules."""
    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.Qt = _Qt
    qtcore.QTimer = _Timer
    qtgui = types.ModuleType("PySide6.QtGui")
    for name in ("QAction", "QColor", "QFont", "QIcon", "QPainter", "QPixmap"):
        setattr(qtgui, name, _Dummy)
    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    for name in ("QApplication", "QFontDialog", "QMenu"):
        setattr(qtwidgets, name, _Dummy)
    qtwidgets.QSystemTrayIcon = _Tray
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qtcore
    pyside.QtGui = qtgui
    pyside.QtWidgets = qtwidgets

    ui_module = types.ModuleType("ui")
    ui_module.__path__ = []
    kwin_module = types.ModuleType("ui.kwin_rules")
    kwin_module.set_rule_enabled = lambda enabled: ("id", True)
    settings_dialog_module = types.ModuleType("ui.settings_dialog")
    settings_dialog_module.SettingsDialog = _Dummy

    path = Path(__file__).resolve().parents[1] / "src" / "ui" / "tray.py"
    spec = importlib.util.spec_from_file_location("tray_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    stubs = {
        "PySide6": pyside,
        "PySide6.QtCore": qtcore,
        "PySide6.QtGui": qtgui,
        "PySide6.QtWidgets": qtwidgets,
        "ui": ui_module,
        "ui.kwin_rules": kwin_module,
        "ui.settings_dialog": settings_dialog_module,
        spec.name: module,
    }
    with _stubbed_modules(stubs):
        spec.loader.exec_module(module)
    return module


tray_module = _load_tray_module()


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _Overlay:
    def __init__(self):
        self.show_count = 0

    def show(self):
        self.show_count += 1

    def is_wayland(self):
        return True


class _Settings:
    def __init__(self):
        self.values = {"behavior.pinned_player": "player"}

    def set(self, key, value):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Mpris:
    def __init__(self):
        self.unpinned = 0
        self.pinned = []
        self.players_changed = _Signal()
        self.active_player_changed = _Signal()

    def unpin_player(self):
        self.unpinned += 1

    def pin_player(self, name):
        self.pinned.append(name)

    def get_players(self):
        return []

    def get_active_player(self):
        return ""

    def is_browser_connected(self):
        return False


class _App:
    def __init__(self):
        self.overlay = _Overlay()
        self.settings = _Settings()
        self.mpris = _Mpris()


def test_fallback_never_leaves_overlay_hidden():
    app = _App()
    fallback = tray_module.TraylessFallback(app)
    assert app.overlay.show_count == 1

    fallback.set_show_checked(False)

    assert app.overlay.show_count == 2


def test_create_falls_back_when_tray_is_reported_unavailable(monkeypatch):
    monkeypatch.setattr(tray_module.QSystemTrayIcon, "tray_available", False)
    app = _App()

    tray = tray_module.TrayIcon.create(app)

    assert isinstance(tray, tray_module.TraylessFallback)
    assert tray.available is False
    assert app.overlay.show_count == 1

    # The overlay ✕ button routes through set_show_checked(False); with a real
    # TrayIcon that would hide the overlay for good.
    tray.set_show_checked(False)

    assert app.overlay.show_count == 2


def test_create_keeps_real_tray_when_one_is_available():
    app = _App()

    tray = tray_module.TrayIcon.create(app)

    assert isinstance(tray, tray_module.TrayIcon)
    assert app.overlay.show_count == 0


def test_auto_player_selection_clears_pin():
    app = _App()
    tray = object.__new__(tray_module.TrayIcon)
    tray._app = app
    tray._update_player_menu_checks = lambda *_args: None

    tray._select_player("")

    assert app.settings.get("behavior.pinned_player") is None
    assert app.mpris.unpinned == 1
