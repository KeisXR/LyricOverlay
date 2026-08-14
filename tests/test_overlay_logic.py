"""Logic tests for the lyrics overlay without requiring a display server."""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path


class _SignalInstance:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback, *args, **kwargs):
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


def _signal(*_args, **_kwargs):
    return _SignalDescriptor()


def _property(_type, getter, setter):
    return property(getter, setter)


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass


class _QWidget(_Dummy):
    def update(self):
        self.updated = getattr(self, "updated", 0) + 1

    def wheelEvent(self, event):
        pass

    def keyPressEvent(self, event):
        pass


class _Rect:
    def __init__(self, *args):
        self.args = args

    def isNull(self):
        return not self.args or self.args == (0, 0, 0, 0)

    def contains(self, *_args):
        return False


class _Point:
    def __init__(self, x=0, y=0):
        self._x = x
        self._y = y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Animation:
    def __init__(self, *args, **kwargs):
        self.starts = 0
        self.stops = 0

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1

    def setDuration(self, *_args):
        pass

    def setStartValue(self, *_args):
        pass

    def setEndValue(self, *_args):
        pass

    def setEasingCurve(self, *_args):
        pass


class _Timer(_Dummy):
    def __init__(self, *args, **kwargs):
        self.timeout = _SignalInstance()

    def setSingleShot(self, *_args):
        pass

    def setInterval(self, *_args):
        pass

    def start(self, *_args):
        pass

    def stop(self):
        pass


class _Qt:
    class MouseButton:
        LeftButton = 1

    class Key:
        Key_PageDown = 1
        Key_PageUp = 2

    class WindowType:
        FramelessWindowHint = 1
        Tool = 2
        WindowDoesNotAcceptFocus = 4
        WindowStaysOnTopHint = 8

    class WidgetAttribute:
        WA_TranslucentBackground = 1
        WA_ShowWithoutActivating = 2

    class TextElideMode:
        ElideRight = 1

    class AlignmentFlag:
        AlignCenter = 1


class _Easing:
    class Type:
        OutCubic = 1


qtcore = types.ModuleType("PySide6.QtCore")
qtcore.QEasingCurve = _Easing
qtcore.Property = _property
qtcore.QPoint = _Point
qtcore.QRect = _Rect
qtcore.Qt = _Qt
qtcore.QTimer = _Timer
qtcore.QPropertyAnimation = _Animation
qtcore.Signal = _signal
qtgui = types.ModuleType("PySide6.QtGui")
for name in (
    "QColor",
    "QEnterEvent",
    "QFont",
    "QFontMetrics",
    "QMouseEvent",
    "QPainter",
    "QPen",
    "QMoveEvent",
    "QWheelEvent",
    "QKeyEvent",
    "QGuiApplication",
):
    setattr(qtgui, name, _Dummy)
qtwidgets = types.ModuleType("PySide6.QtWidgets")
qtwidgets.QWidget = _QWidget
qtwidgets.QMenu = _Dummy
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


@dataclass
class _Word:
    timestamp_ms: int
    text: str


@dataclass
class _Line:
    timestamp_ms: int
    text: str
    words: list | None = None


@dataclass
class _Parsed:
    lines: list


lyrics_module = types.ModuleType("lyrics.lrc_parser")
lyrics_module.ParsedLRC = _Parsed
lyrics_module.find_current_line = lambda lrc, pos: max(
    [index for index, line in enumerate(lrc.lines) if line.timestamp_ms <= pos],
    default=-1,
)
sys.modules["lyrics"] = types.ModuleType("lyrics")
sys.modules["lyrics.lrc_parser"] = lyrics_module
ui_module = types.ModuleType("ui")
ui_module.__path__ = []
sys.modules["ui"] = ui_module
color_module = types.ModuleType("ui.color_utils")
color_module.color_from_setting = lambda *_args: None
sys.modules["ui.color_utils"] = color_module

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "ui" / "overlay.py"
spec = importlib.util.spec_from_file_location("overlay_under_test", MODULE_PATH)
overlay_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = overlay_module
spec.loader.exec_module(overlay_module)


class _Settings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _make_overlay(lines, visible=5, synced=True):
    overlay = object.__new__(overlay_module.OverlayWindow)
    overlay._settings = _Settings(
        {
            "window.visible_lines": visible,
            "window.karaoke_enabled": True,
        }
    )
    overlay._display_lines = list(lines)
    overlay._synced = synced
    overlay._loading = False
    overlay._plain_scroll_start = 0
    overlay._current_line = -1
    overlay._current_word_index = -1
    overlay._current_word_progress = 0.0
    overlay._parsed_lrc = None
    overlay.updated = 0
    return overlay


def test_clamped_window_fills_last_page():
    overlay = _make_overlay([str(index) for index in range(10)], visible=5)
    assert overlay._clamped_window_start(9, 5) == 5
    assert list(range(overlay._clamped_window_start(9, 5), 10)) == [5, 6, 7, 8, 9]


def test_plain_scroll_can_reach_last_line():
    overlay = _make_overlay([str(index) for index in range(12)], visible=5, synced=False)
    assert overlay._max_start_index(5) == 7
    overlay._plain_scroll_start = overlay._max_start_index(5)
    assert overlay._plain_scroll_start + 5 == 12


def test_hit_rects_are_cleared():
    overlay = _make_overlay(["a"])
    overlay._close_btn_rect = _Rect(1, 1, 2, 2)
    overlay._resync_btn_rect = _Rect(1, 1, 2, 2)
    overlay._alt_menu_rect = _Rect(1, 1, 2, 2)

    overlay._clear_control_hit_rects()

    assert overlay._close_btn_rect.isNull()
    assert overlay._resync_btn_rect.isNull()
    assert overlay._alt_menu_rect.isNull()


def test_last_word_uses_bounded_nontrivial_duration():
    overlay = _make_overlay(["line"])
    overlay._current_line = 0
    overlay._parsed_lrc = _Parsed(
        [_Line(1000, "line", [_Word(1000, "a"), _Word(1400, "b")])]
    )

    assert overlay._update_word_progress(1401)
    assert overlay._current_word_index == 1
    assert 0 < overlay._current_word_progress < 0.01

    overlay._update_word_progress(2200)
    assert 0.9 <= overlay._current_word_progress <= 1.0


def test_animations_are_reused():
    overlay = _make_overlay(["a"])
    overlay._controls_opacity = 0.0
    overlay._opacity_anim = _Animation()
    overlay._line_anim = _Animation()

    overlay._animate_controls_opacity(1.0)
    overlay._animate_controls_opacity(0.0)
    overlay._animate_line_transition()
    overlay._animate_line_transition()

    assert overlay._opacity_anim.starts == 2
    assert overlay._line_anim.starts == 2
    assert overlay._opacity_anim.stops == 2
    assert overlay._line_anim.stops == 2
