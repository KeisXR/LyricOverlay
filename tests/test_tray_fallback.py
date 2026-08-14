"""Logic tests for tray-less fallback and automatic player selection."""

import importlib.util
import sys
import types
from pathlib import Path


class _Timer:
    @staticmethod
    def singleShot(_delay, callback):
        callback()


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass


class _Tray(_Dummy):
    class ActivationReason:
        Trigger = 1


class _Qt:
    class AlignmentFlag:
        AlignCenter = 1


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
sys.modules.update(
    {
        "PySide6": pyside,
        "PySide6.QtCore": qtcore,
        "PySide6.QtGui": qtgui,
        "PySide6.QtWidgets": qtwidgets,
    }
)

ui_module = types.ModuleType("ui")
ui_module.__path__ = []
sys.modules["ui"] = ui_module
kwin_module = types.ModuleType("ui.kwin_rules")
kwin_module.set_rule_enabled = lambda enabled: ("id", True)
sys.modules["ui.kwin_rules"] = kwin_module
settings_dialog_module = types.ModuleType("ui.settings_dialog")
settings_dialog_module.SettingsDialog = _Dummy
sys.modules["ui.settings_dialog"] = settings_dialog_module

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "ui" / "tray.py"
spec = importlib.util.spec_from_file_location("tray_under_test", MODULE_PATH)
tray_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = tray_module
spec.loader.exec_module(tray_module)


class _Overlay:
    def __init__(self):
        self.show_count = 0

    def show(self):
        self.show_count += 1


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

    def unpin_player(self):
        self.unpinned += 1

    def pin_player(self, name):
        self.pinned.append(name)


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


def test_auto_player_selection_clears_pin():
    app = _App()
    tray = object.__new__(tray_module.TrayIcon)
    tray._app = app
    tray._update_player_menu_checks = lambda *_args: None

    tray._select_player("")

    assert app.settings.get("behavior.pinned_player") is None
    assert app.mpris.unpinned == 1
