"""Unit tests for MPRIS listener timeline isolation."""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _SignalInstance:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args, **kwargs):
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _SignalDescriptor:
    def __set_name__(self, owner, name):
        self._name = f"__sig_{name}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = _SignalInstance()
            instance.__dict__[self._name] = signal
        return signal


class _FakeTimer:
    def __init__(self, parent=None):
        self.timeout = _SignalInstance()
        self.started = False

    def start(self, _interval):
        self.started = True

    def stop(self):
        self.started = False

    @staticmethod
    def singleShot(_ms, callback):
        callback()


class _FakeQObject:
    def __init__(self, parent=None):
        self._parent = parent


class _FakeMatch:
    def __init__(self):
        self.removed = False

    def remove(self):
        self.removed = True


class _FakeProxy:
    def __init__(self, bus, bus_name):
        self._bus = bus
        self._bus_name = bus_name


class _FakeDBusException(Exception):
    pass


class _FakeInterface:
    def __init__(self, proxy, _iface_name):
        self._proxy = proxy

    def connect_to_signal(self, _signal_name, _callback):
        match = _FakeMatch()
        self._proxy._bus.matches.append(match)
        return match

    def Get(self, _iface_name, prop_name):
        bus = self._proxy._bus
        bus_name = self._proxy._bus_name
        if prop_name == "Position":
            if bus_name in bus.fail_position_get_for:
                raise _FakeDBusException("failed to fetch Position")
            bus.position_get_calls[bus_name] = bus.position_get_calls.get(bus_name, 0) + 1
        return bus.player_props[bus_name][prop_name]


class _FakeBus:
    def __init__(self):
        self.names = []
        self.player_props = {}
        self.position_get_calls = {}
        self.fail_position_get_for = set()
        self.matches = []
        self.name_owner_matches = []

    def list_names(self):
        return list(self.names)

    def get_object(self, bus_name, _path):
        return _FakeProxy(self, bus_name)

    def add_signal_receiver(self, _callback, **_kwargs):
        match = _FakeMatch()
        self.name_owner_matches.append(match)
        return match

    def set_player(
        self, bus_name: str, *, status: str, position_us: int, rate: float = 1.0
    ):
        self.player_props[bus_name] = {
            "Metadata": {
                "xesam:title": f"Title {bus_name}",
                "xesam:artist": [f"Artist {bus_name}"],
                "xesam:album": "",
                "mpris:trackid": f"/{bus_name}",
                "mpris:length": 300_000_000,
            },
            "Rate": rate,
            "PlaybackStatus": status,
            "Position": position_us,
        }


def _install_fake_modules(bus):
    dbus_module = types.ModuleType("dbus")
    dbus_module.DBusException = _FakeDBusException
    dbus_module.Dictionary = dict
    dbus_module.SessionBus = lambda: bus
    dbus_module.Interface = lambda proxy, iface: _FakeInterface(proxy, iface)

    dbus_mainloop = types.ModuleType("dbus.mainloop")
    dbus_mainloop_glib = types.ModuleType("dbus.mainloop.glib")
    dbus_mainloop_glib.DBusGMainLoop = lambda set_as_default=True: None

    glib_module = types.ModuleType("GLib")
    glib_module.main_context_default = lambda: types.SimpleNamespace(
        pending=lambda: False, iteration=lambda _x: None
    )
    gi_repo = types.ModuleType("gi.repository")
    gi_repo.GLib = glib_module
    gi_module = types.ModuleType("gi")
    gi_module.repository = gi_repo

    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.QObject = _FakeQObject
    qtcore.QTimer = _FakeTimer
    qtcore.Signal = lambda *args, **kwargs: _SignalDescriptor()
    pyside6 = types.ModuleType("PySide6")
    pyside6.QtCore = qtcore

    sys.modules["dbus"] = dbus_module
    sys.modules["dbus.mainloop"] = dbus_mainloop
    sys.modules["dbus.mainloop.glib"] = dbus_mainloop_glib
    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = gi_repo
    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtCore"] = qtcore


def _load_mpris_module():
    module_path = SRC / "player" / "mpris.py"
    spec = importlib.util.spec_from_file_location("test_mpris_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _make_listener():
    bus = _FakeBus()
    _install_fake_modules(bus)
    mpris = _load_mpris_module()
    fake_clock = types.SimpleNamespace(now=1000.0)
    mpris.time.monotonic = lambda: fake_clock.now
    listener = mpris.MprisListener()
    return mpris, bus, listener


def _player_timeline(listener, bus_name):
    return listener._players[bus_name]["timeline"]


def test_inactive_playing_does_not_move_active_timeline():
    mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=100_000)
    bus.set_player("B", status="Paused", position_us=900_000)
    listener._add_player("A")
    listener._add_player("B")

    assert listener.get_active_player() == "A"
    active_before = _player_timeline(listener, "A").position_ms

    bus.player_props["B"]["Position"] = 990_000
    listener._on_properties_changed(
        "B", mpris.MPRIS_PLAYER_IFACE, {"PlaybackStatus": "Playing"}, []
    )

    assert listener.get_active_player() == "A"
    assert _player_timeline(listener, "A").position_ms == active_before
    assert bus.position_get_calls.get("B", 0) == 0


def test_inactive_position_seeked_rate_do_not_affect_active():
    mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=300_000)
    bus.set_player("B", status="Paused", position_us=700_000)
    listener._add_player("A")
    listener._add_player("B")

    timeline_a = _player_timeline(listener, "A")
    snapshot = (
        timeline_a.position_ms,
        timeline_a.position_time,
        timeline_a.playback_rate,
        timeline_a.last_sync_latency_ms,
    )

    listener._on_properties_changed(
        "B", mpris.MPRIS_PLAYER_IFACE, {"Position": 710_000}, []
    )
    listener._on_seeked("B", 720_000)
    listener._on_properties_changed("B", mpris.MPRIS_PLAYER_IFACE, {"Rate": 1.5}, [])

    assert (
        timeline_a.position_ms,
        timeline_a.position_time,
        timeline_a.playback_rate,
        timeline_a.last_sync_latency_ms,
    ) == snapshot


def test_switch_to_playing_player_syncs_immediately():
    _mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=100_000)
    bus.set_player("B", status="Playing", position_us=2_000_000)
    listener._add_player("A")
    listener._add_player("B")

    before_calls = bus.position_get_calls.get("B", 0)
    listener.pin_player("B")

    assert listener.get_active_player() == "B"
    assert bus.position_get_calls.get("B", 0) == before_calls + 1
    assert _player_timeline(listener, "B").position_ms == 2000


def test_pinned_player_is_not_switched_by_other_playing():
    mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=100_000)
    bus.set_player("B", status="Paused", position_us=500_000)
    listener._add_player("A")
    listener._add_player("B")
    listener.pin_player("A")

    listener._on_properties_changed(
        "B", mpris.MPRIS_PLAYER_IFACE, {"PlaybackStatus": "Playing"}, []
    )

    assert listener.get_active_player() == "A"


def test_remove_active_player_moves_to_next_playing():
    _mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=100_000)
    bus.set_player("B", status="Playing", position_us=600_000)
    listener._add_player("A")
    listener._add_player("B")

    before_calls = bus.position_get_calls.get("B", 0)
    listener._remove_player("A")

    assert listener.get_active_player() == "B"
    assert bus.position_get_calls.get("B", 0) >= before_calls + 1


def test_sync_failure_keeps_last_valid_anchor():
    _mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=345_000)
    listener._add_player("A")

    timeline = _player_timeline(listener, "A")
    snapshot = (timeline.position_ms, timeline.position_time, timeline.last_sync_latency_ms)
    bus.fail_position_get_for.add("A")
    listener._sync_position_from_player("A")

    assert (timeline.position_ms, timeline.position_time, timeline.last_sync_latency_ms) == snapshot


def test_stop_stops_timers_and_receivers():
    _mpris, bus, listener = _make_listener()
    bus.set_player("A", status="Playing", position_us=100_000)
    listener._add_player("A")

    listener.stop()

    assert not listener._glib_timer.started
    assert not listener._pos_timer.started
    assert bus.name_owner_matches and bus.name_owner_matches[0].removed
    assert bus.matches and all(match.removed for match in bus.matches)


if __name__ == "__main__":
    import traceback

    tests = [
        ("inactive_playing_does_not_move_active_timeline", test_inactive_playing_does_not_move_active_timeline),
        ("inactive_position_seeked_rate_do_not_affect_active", test_inactive_position_seeked_rate_do_not_affect_active),
        ("switch_to_playing_player_syncs_immediately", test_switch_to_playing_player_syncs_immediately),
        ("pinned_player_is_not_switched_by_other_playing", test_pinned_player_is_not_switched_by_other_playing),
        ("remove_active_player_moves_to_next_playing", test_remove_active_player_moves_to_next_playing),
        ("sync_failure_keeps_last_valid_anchor", test_sync_failure_keeps_last_valid_anchor),
        ("stop_stops_timers_and_receivers", test_stop_stops_timers_and_receivers),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
