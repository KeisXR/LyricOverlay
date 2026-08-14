"""State and validation tests for the browser WebSocket listener."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


class _SignalInstance:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback, *args, **kwargs):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _SignalDescriptor:
    def __set_name__(self, owner, name):
        self.name = f"__signal_{name}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.__dict__.setdefault(self.name, _SignalInstance())


class _QObject:
    def __init__(self, parent=None):
        self.parent = parent


class _QThread(_QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._interrupted = False

    def start(self):
        pass

    def requestInterruption(self):
        self._interrupted = True

    def isInterruptionRequested(self):
        return self._interrupted

    def wait(self, _timeout):
        return True


class _QTimer(_QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeout = _SignalInstance()
        self.running = False

    def start(self, _interval):
        self.running = True

    def stop(self):
        self.running = False


def _signal(*_args, **_kwargs):
    return _SignalDescriptor()


qtcore = types.ModuleType("PySide6.QtCore")
qtcore.QObject = _QObject
qtcore.QThread = _QThread
qtcore.QTimer = _QTimer
qtcore.Signal = _signal
pyside = types.ModuleType("PySide6")
pyside.QtCore = qtcore
sys.modules.setdefault("PySide6", pyside)
sys.modules.setdefault("PySide6.QtCore", qtcore)

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "player" / "browser_ws.py"
spec = importlib.util.spec_from_file_location("browser_ws_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class _Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _payload(
    *,
    sequence=1,
    instance_id="instance-a",
    tab_id=1,
    frame_id=0,
    status="Playing",
    title="Song",
    position=1.0,
    duration=180.0,
    rate=1.0,
):
    return {
        "protocol_version": 1,
        "sequence": sequence,
        "observed_at_ms": 1_000_000,
        "source": {
            "instance_id": instance_id,
            "tab_id": tab_id,
            "frame_id": frame_id,
        },
        "state": {
            "title": title,
            "artist": "Artist",
            "album": "Album",
            "status": status,
            "position": position,
            "duration": duration,
            "rate": rate,
        },
    }


def _listener(clock=None):
    return module.BrowserWsListener(clock=clock or _Clock(), start_server=False)


def test_connection_without_media_is_not_active():
    listener = _listener()
    listener._on_connection_count_changed(1)

    assert listener.is_connected()
    assert not listener.is_media_active()
    assert listener.get_players() == []


def test_activation_signal_observer_sees_new_metadata():
    listener = _listener()
    listener._on_connection_count_changed(1)
    observed = []
    listener.active_player_changed.connect(
        lambda _player: observed.append(listener.get_current_metadata())
    )

    assert listener._apply_payload(_payload())

    assert observed[-1]["title"] == "Song"
    assert listener.get_active_player() == "browser-ws"


def test_paused_payload_is_active_but_has_fixed_position():
    listener = _listener()
    positions = []
    listener.position_changed.connect(positions.append)
    listener._on_connection_count_changed(1)

    assert listener._apply_payload(_payload(status="Paused", position=42.5))

    assert listener.is_media_active()
    assert listener.get_player_status("browser-ws") == "Paused"
    assert positions[-1] == 42500


def test_stale_media_deactivates_while_transport_remains_connected():
    clock = _Clock()
    listener = _listener(clock)
    listener._on_connection_count_changed(1)
    listener._apply_payload(_payload())

    clock.advance(listener._STALE_TIMEOUT_SEC + 0.1)
    listener._enforce_freshness()

    assert listener.is_connected()
    assert not listener.is_media_active()
    assert listener.get_current_metadata() is None


def test_one_of_two_disconnects_does_not_mark_bridge_disconnected():
    listener = _listener()
    listener._on_connection_count_changed(2)
    listener._on_connection_count_changed(1)

    assert listener.is_connected()
    assert listener._connection_count == 1


def test_replayed_sequence_from_same_source_is_rejected():
    listener = _listener()
    listener._on_connection_count_changed(1)
    assert listener._apply_payload(_payload(sequence=5, title="First"))

    assert not listener._apply_payload(_payload(sequence=5, title="Replay"))
    assert listener.get_current_metadata()["title"] == "First"


def test_sequence_can_restart_for_new_extension_instance():
    listener = _listener()
    listener._on_connection_count_changed(1)
    assert listener._apply_payload(_payload(sequence=50, instance_id="old"))
    assert listener._apply_payload(
        _payload(sequence=0, instance_id="new", title="After restart")
    )
    assert listener.get_current_metadata()["title"] == "After restart"


def test_nonfinite_and_malformed_payloads_are_rejected():
    listener = _listener()
    invalid = [
        {},
        _payload(rate=float("nan")),
        _payload(position=float("inf")),
        _payload(instance_id=""),
        _payload(tab_id=-1),
        _payload(title="x" * (listener._MAX_TEXT_LENGTH + 1)),
    ]

    assert all(not listener._apply_payload(payload) for payload in invalid)
    assert not listener.is_media_active()


def test_stopped_payload_releases_browser_player():
    listener = _listener()
    listener._on_connection_count_changed(1)
    listener._apply_payload(_payload())

    assert listener._apply_payload(
        _payload(sequence=2, status="Stopped", title="", position=0)
    )

    assert not listener.is_media_active()
    assert listener.get_players() == []
