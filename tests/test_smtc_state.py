"""State-machine tests for the Windows SMTC listener."""

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

    def msleep(self, _milliseconds):
        pass

    def wait(self, _milliseconds):
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

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "player" / "smtc.py"
spec = importlib.util.spec_from_file_location("smtc_under_test", MODULE_PATH)
smtc = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = smtc
spec.loader.exec_module(smtc)


class _Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _state(**overrides):
    state = {
        "title": "Title",
        "artist": "Artist",
        "album": "Album",
        "app_id": "player.app",
        "status": "Playing",
        "rate": 1.0,
        "pos_ms": 0,
        "length_ms": 300000,
        "sessions": ["player.app"],
    }
    state.update(overrides)
    return state


def test_active_change_observer_sees_new_metadata():
    clock = _Clock()
    listener = smtc.SmtcListener(clock=clock)
    observed = []
    listener.active_player_changed.connect(
        lambda _player: observed.append(listener.get_current_metadata())
    )

    listener._apply_state(_state(title="New title"))

    assert observed[-1]["title"] == "New title"
    assert listener.get_active_player() == "player.app"


def test_none_snapshot_clears_state_and_emits_stopped():
    clock = _Clock()
    listener = smtc.SmtcListener(clock=clock)
    stopped = []
    listener.playback_state_changed.connect(lambda *args: stopped.append(args))
    listener._apply_state(_state())

    listener._apply_state(None)

    assert listener.get_current_metadata() is None
    assert listener.get_active_player() == ""
    assert listener.get_players() == []
    assert stopped[-1][0] == "Stopped"


def test_regular_position_reports_do_not_reanchor_as_seeks():
    clock = _Clock()
    listener = smtc.SmtcListener(clock=clock, fallback=True)
    listener._apply_state(_state(pos_ms=0))
    original_anchor_time = listener._timeline.observed_at

    for position in (250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2250):
        clock.advance(0.25)
        listener._apply_state(_state(pos_ms=position))

    assert listener._timeline.observed_at == original_anchor_time
    assert listener._timeline.position_ms == 0


def test_large_seek_reanchors_to_reported_position():
    clock = _Clock()
    listener = smtc.SmtcListener(clock=clock, fallback=True)
    listener._apply_state(_state(pos_ms=1000))
    clock.advance(0.25)

    listener._apply_state(_state(pos_ms=10000))

    assert listener._timeline.position_ms == 10000
    assert listener._timeline.observed_at == clock.value


def test_pause_resume_does_not_include_paused_wall_time():
    clock = _Clock()
    listener = smtc.SmtcListener(clock=clock)
    positions = []
    listener.position_changed.connect(positions.append)
    listener._apply_state(_state(pos_ms=2000))

    clock.advance(1.0)
    listener._apply_state(_state(status="Paused", pos_ms=3000))
    clock.advance(30.0)
    listener._apply_state(_state(status="Playing", pos_ms=3000))
    clock.advance(1.0)
    listener._tick_position()

    assert positions[-1] == 4000


def test_rate_change_settles_old_rate_before_new_interpolation():
    clock = _Clock()
    listener = smtc.SmtcListener(clock=clock)
    positions = []
    listener.position_changed.connect(positions.append)
    listener._apply_state(_state(pos_ms=0, rate=1.0))

    clock.advance(2.0)
    listener._apply_state(_state(pos_ms=2000, rate=1.5))
    clock.advance(2.0)
    listener._tick_position()

    assert positions[-1] == 5000


def test_invalid_numbers_are_clamped():
    listener = smtc.SmtcListener(clock=_Clock())
    listener._apply_state(
        _state(pos_ms=float("nan"), length_ms=-1, rate=float("inf"))
    )

    assert listener._timeline.position_ms == 0
    assert listener._timeline.rate == 1.0
    assert listener.get_current_metadata()["length_ms"] == 0
