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


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "ui" / "tray.py"


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

    spec = importlib.util.spec_from_file_location("tray_under_test", MODULE_PATH)
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


WATCHER = "org.kde.StatusNotifierWatcher"


class _FakeBus:
    """Minimal dbus.SessionBus stand-in for the StatusNotifier probe."""

    def __init__(self, owners, host_registered):
        self._owners = owners
        self._host_registered = host_registered
        self.probed = []

    def name_has_owner(self, service):
        self.probed.append(service)
        return service in self._owners

    def get_object(self, service, path):
        assert path == "/StatusNotifierWatcher"
        return ("proxy", service)


def _fake_dbus(owners=(WATCHER,), host_registered=True):
    """Build a fake ``dbus`` module plus the bus it hands out."""
    tray_module._TRAY_PROBE_RETRY_DELAY_SEC = 0
    bus = _FakeBus(set(owners), host_registered)

    class _Props:
        def __init__(self, proxy, iface):
            self._service = proxy[1]

        def Get(self, service, prop, timeout=None):
            assert prop == "IsStatusNotifierHostRegistered"
            assert timeout is not None, "the probe must not block indefinitely"
            answer = bus._host_registered
            if isinstance(answer, list):
                # One answer per probe round, so a host can appear on the retry.
                return answer.pop(0) if len(answer) > 1 else answer[0]
            return answer

    module = types.ModuleType("dbus")
    module.SessionBus = lambda: bus
    module.Interface = _Props
    return module, bus


def test_create_falls_back_when_no_statusnotifier_host_is_registered():
    # Wayland has no XEmbed fallback, so a watcher without a registered host
    # means the tray icon would never be displayed anywhere.
    module, bus = _fake_dbus(host_registered=False)
    app = _App()

    with _stubbed_modules({"dbus": module}):
        tray = tray_module.TrayIcon.create(app)

    assert isinstance(tray, tray_module.TraylessFallback)
    assert tray.available is False
    assert app.overlay.show_count == 1
    assert WATCHER in bus.probed

    # The overlay ✕ button routes through set_show_checked(False); with a real
    # TrayIcon that would hide the overlay for good.
    tray.set_show_checked(False)

    assert app.overlay.show_count == 2


def test_create_falls_back_when_no_watcher_owns_the_name():
    module, bus = _fake_dbus(owners=())
    app = _App()

    with _stubbed_modules({"dbus": module}):
        tray = tray_module.TrayIcon.create(app)

    assert isinstance(tray, tray_module.TraylessFallback)


def test_create_keeps_real_tray_when_a_host_is_registered():
    module, _bus = _fake_dbus(host_registered=True)
    app = _App()

    with _stubbed_modules({"dbus": module}):
        tray = tray_module.TrayIcon.create(app)

    assert not isinstance(tray, tray_module.TraylessFallback)


def test_probe_is_skipped_and_tray_kept_off_wayland():
    # On X11 the StatusNotifier watcher may legitimately be absent while an
    # XEmbed tray works, so the probe must not run at all.
    module, bus = _fake_dbus(owners=(), host_registered=False)
    app = _App()
    app.overlay.is_wayland = lambda: False

    with _stubbed_modules({"dbus": module}):
        tray = tray_module.TrayIcon.create(app)

    assert not isinstance(tray, tray_module.TraylessFallback)
    assert bus.probed == []


def test_tray_module_never_calls_is_system_tray_available():
    # Regression guard: QSystemTrayIcon.isSystemTrayAvailable() can segfault on
    # Wayland (Qt6 bug), which is why detection goes through D-Bus instead.
    # A native crash cannot be caught by try/except, and a stubbed Qt in these
    # tests would never reproduce it -- so the ban is enforced on the source.
    source = MODULE_PATH.read_text(encoding="utf-8")
    calls = [
        line.strip()
        for line in source.splitlines()
        if "isSystemTrayAvailable()" in line and not line.strip().startswith("#")
        and "``" not in line
    ]
    assert calls == [], f"isSystemTrayAvailable must not be called: {calls}"


def test_auto_player_selection_clears_pin():
    app = _App()
    tray = object.__new__(tray_module.TrayIcon)
    tray._app = app
    tray._update_player_menu_checks = lambda *_args: None

    tray._select_player("")

    assert app.settings.get("behavior.pinned_player") is None
    assert app.mpris.unpinned == 1


def test_host_appearing_on_the_retry_keeps_the_real_tray():
    # A panel that registers its StatusNotifier host just after start-up must
    # not be mistaken for a tray-less session.
    module, bus = _fake_dbus(host_registered=[False, True])
    app = _App()

    with _stubbed_modules({"dbus": module}):
        tray = tray_module.TrayIcon.create(app)

    assert not isinstance(tray, tray_module.TraylessFallback)
    assert bus.probed.count(WATCHER) == 2
