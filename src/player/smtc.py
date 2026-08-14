"""Windows System Media Transport Controls listener.

The listener polls the current SMTC session on a worker thread, applies each
snapshot atomically on the Qt thread, and interpolates playback position from a
monotonic timeline anchor.  The public signals intentionally mirror the Linux
MPRIS listener.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal


_manager = None
_SEEK_THRESHOLD_MS = 2000
_MAX_MEDIA_MS = 2 * 24 * 60 * 60 * 1000
_MAX_RATE = 16.0


def _finite_float(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _normalise_ms(value, *, maximum: int = _MAX_MEDIA_MS) -> int:
    number = _finite_float(value, 0.0)
    return max(0, min(maximum, round(number)))


def _normalise_rate(value) -> float:
    rate = _finite_float(value, 1.0)
    if rate <= 0 or rate > _MAX_RATE:
        return 1.0
    return rate


async def _query_smtc() -> dict | None:
    """Return one normalised-looking raw snapshot from Windows SMTC."""
    global _manager

    from winrt.windows.media.control import (  # type: ignore[import]
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )

    if _manager is None:
        try:
            _manager = await SessionManager.request_async()
        except Exception as exc:
            print(f"[SMTC] Failed to get SessionManager: {exc}", flush=True)
            return None

    try:
        session = _manager.get_current_session()
    except Exception as exc:
        print(f"[SMTC] get_current_session failed: {exc}", flush=True)
        _manager = None
        return None

    if session is None:
        return None

    try:
        props = await session.try_get_media_properties_async()
    except Exception as exc:
        print(f"[SMTC] try_get_media_properties_async failed: {exc}", flush=True)
        props = None

    try:
        playback_info = session.get_playback_info()
    except Exception as exc:
        print(f"[SMTC] get_playback_info failed: {exc}", flush=True)
        playback_info = None

    try:
        timeline = session.get_timeline_properties()
    except Exception as exc:
        print(f"[SMTC] get_timeline_properties failed: {exc}", flush=True)
        timeline = None

    title = (getattr(props, "title", None) or "").strip() if props else ""
    artist = (getattr(props, "artist", None) or "").strip() if props else ""
    album = (getattr(props, "album_title", None) or "").strip() if props else ""
    app_id = str(getattr(session, "source_app_user_model_id", "") or "")

    status = "Stopped"
    rate = 1.0
    if playback_info is not None:
        playback_status = getattr(playback_info, "playback_status", None)
        if playback_status == PlaybackStatus.PLAYING:
            status = "Playing"
        elif playback_status == PlaybackStatus.PAUSED:
            status = "Paused"
        rate = _normalise_rate(getattr(playback_info, "playback_rate", 1.0))

    pos_ms = 0
    length_ms = 0
    if timeline is not None:
        try:
            pos_ms = round(timeline.position.total_seconds() * 1000)
        except Exception as exc:
            print(f"[SMTC] timeline.position failed: {exc}", flush=True)
        try:
            length_ms = round(timeline.end_time.total_seconds() * 1000)
        except Exception as exc:
            print(f"[SMTC] timeline.end_time failed: {exc}", flush=True)

    sessions: list[str] = []
    try:
        for candidate in _manager.get_sessions():
            session_id = str(
                getattr(candidate, "source_app_user_model_id", "") or ""
            )
            if session_id and session_id not in sessions:
                sessions.append(session_id)
    except Exception:
        if app_id:
            sessions = [app_id]

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "app_id": app_id,
        "status": status,
        "rate": rate,
        "pos_ms": pos_ms,
        "length_ms": length_ms,
        "sessions": sessions,
    }


class _SmtcPollThread(QThread):
    """Poll SMTC on a persistent asyncio loop without blocking Qt."""

    state_ready = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._running = True

    def stop(self):
        self._running = False
        self.requestInterruption()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while self._running and not self.isInterruptionRequested():
                try:
                    state = loop.run_until_complete(_query_smtc())
                except Exception as exc:
                    print(f"[SMTC] poll error: {exc}", flush=True)
                    state = None
                if self._running and not self.isInterruptionRequested():
                    self.state_ready.emit(state)
                self.msleep(250)
        finally:
            loop.close()


@dataclass
class _TimelineAnchor:
    position_ms: int = 0
    observed_at: float = 0.0
    rate: float = 1.0

    def estimate(self, now: float) -> int:
        if self.observed_at <= 0:
            return self.position_ms
        elapsed_ms = max(0.0, now - self.observed_at) * 1000.0 * self.rate
        return self.position_ms + round(elapsed_ms)


class SmtcListener(QObject):
    """Windows SMTC session watcher with deterministic state transitions."""

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)
    players_changed = Signal(list)
    active_player_changed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        fallback: bool = True,
        clock: Callable[[], float] | None = None,
    ):
        super().__init__(parent)
        self._clock = clock or time.monotonic
        self._fallback = bool(fallback)
        self._stopping = False

        self._current_meta: dict = {}
        self._status = "Stopped"
        self._active_player = ""
        self._pinned_player = ""
        self._players: list[str] = []
        self._timeline = _TimelineAnchor()
        self._last_reported_pos_ms: int | None = None

        self._poll_thread = _SmtcPollThread(self)
        self._poll_thread.state_ready.connect(self._on_state)
        self._poll_thread.start()

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start(16)

    def _on_state(self, state: dict | None):
        if self._stopping:
            return
        try:
            self._apply_state(state)
        except Exception as exc:
            print(f"[SMTC] state application failed: {exc}", flush=True)

    @staticmethod
    def _normalise_state(state: dict) -> dict:
        status = state.get("status", "Stopped")
        if status not in {"Playing", "Paused", "Stopped"}:
            status = "Stopped"

        length_ms = _normalise_ms(state.get("length_ms", 0))
        pos_ms = _normalise_ms(state.get("pos_ms", 0))
        if length_ms:
            pos_ms = min(pos_ms, length_ms)

        sessions: list[str] = []
        raw_sessions = state.get("sessions", [])
        if isinstance(raw_sessions, (list, tuple)):
            for value in raw_sessions:
                session_id = str(value or "").strip()
                if session_id and session_id not in sessions:
                    sessions.append(session_id)

        app_id = str(state.get("app_id", "") or "").strip()
        if app_id and app_id not in sessions:
            sessions.insert(0, app_id)

        return {
            "title": str(state.get("title", "") or "").strip(),
            "artist": str(state.get("artist", "") or "").strip(),
            "album": str(state.get("album", "") or "").strip(),
            "app_id": app_id,
            "status": status,
            "rate": _normalise_rate(state.get("rate", 1.0)),
            "pos_ms": pos_ms,
            "length_ms": length_ms,
            "sessions": sessions,
        }

    def _apply_state(self, raw_state: dict | None):
        if raw_state is None:
            self._clear_session()
            return

        state = self._normalise_state(raw_state)
        now = self._clock()
        app_id = state["app_id"]
        status = state["status"]
        rate = state["rate"]
        reported_pos = state["pos_ms"]
        length_ms = state["length_ms"]

        new_meta = {
            "title": state["title"],
            "artist": state["artist"],
            "album": state["album"],
            "trackid": app_id,
            "length_ms": length_ms,
            "player_name": app_id,
        }

        old_active = self._active_player
        old_players = self._players
        old_meta = self._current_meta
        old_status = self._status

        self._update_timeline(
            now=now,
            reported_pos_ms=reported_pos,
            status=status,
            rate=rate,
            active_changed=app_id != old_active,
            old_status=old_status,
            length_ms=length_ms,
        )

        # Commit the complete snapshot before any observer is notified.
        self._players = state["sessions"]
        self._active_player = app_id
        self._current_meta = new_meta
        self._status = status
        self._last_reported_pos_ms = reported_pos

        if self._players != old_players:
            self.players_changed.emit(list(self._players))
        if self._active_player != old_active:
            self.active_player_changed.emit(self._active_player)
        if new_meta != old_meta:
            self.metadata_changed.emit(dict(new_meta))
        if status != old_status or self._active_player != old_active:
            self.playback_state_changed.emit(status, app_id)

        if status != "Playing":
            self.position_changed.emit(self._bounded_position(reported_pos))

    def _update_timeline(
        self,
        *,
        now: float,
        reported_pos_ms: int,
        status: str,
        rate: float,
        active_changed: bool,
        old_status: str,
        length_ms: int,
    ):
        if status != "Playing":
            self._timeline = _TimelineAnchor(reported_pos_ms, 0.0, rate)
            return

        if active_changed or old_status != "Playing" or self._timeline.observed_at <= 0:
            self._timeline = _TimelineAnchor(reported_pos_ms, now, rate)
            return

        predicted = self._bounded_position(
            self._timeline.estimate(now), length_ms=length_ms
        )
        rate_changed = not math.isclose(
            rate, self._timeline.rate, rel_tol=1e-9, abs_tol=1e-9
        )
        seek_detected = abs(reported_pos_ms - predicted) > _SEEK_THRESHOLD_MS

        if not self._fallback or seek_detected:
            anchor_pos = reported_pos_ms
        elif rate_changed:
            # Settle the old-rate interval before adopting the new rate.
            anchor_pos = predicted
        else:
            return

        self._timeline = _TimelineAnchor(anchor_pos, now, rate)

    def _clear_session(self):
        old_active = self._active_player
        old_players = self._players
        old_status = self._status

        self._current_meta = {}
        self._active_player = ""
        self._players = []
        self._status = "Stopped"
        self._timeline = _TimelineAnchor()
        self._last_reported_pos_ms = None

        if old_players:
            self.players_changed.emit([])
        if old_active:
            self.active_player_changed.emit("")
        if old_status != "Stopped" or old_active:
            self.playback_state_changed.emit("Stopped", old_active)
        self.position_changed.emit(0)

    def _bounded_position(self, position_ms: int, *, length_ms: int | None = None) -> int:
        position = max(0, min(_MAX_MEDIA_MS, int(position_ms)))
        length = (
            self._current_meta.get("length_ms", 0)
            if length_ms is None
            else length_ms
        )
        if length:
            position = min(position, int(length))
        return position

    def _tick_position(self):
        if self._stopping or self._status != "Playing":
            return
        position = self._timeline.estimate(self._clock())
        self.position_changed.emit(self._bounded_position(position))

    def pin_player(self, player_id: str):
        # SMTC exposes one OS-selected current session. Keep the value only so
        # the cross-platform listener interface remains compatible.
        self._pinned_player = player_id

    def unpin_player(self):
        self._pinned_player = ""

    def set_fallback(self, enabled: bool):
        self._fallback = bool(enabled)

    def get_players(self) -> list[str]:
        return list(self._players)

    def get_active_player(self) -> str:
        return self._active_player

    def get_player_status(self, player_id: str) -> str:
        return self._status if player_id == self._active_player else "Unknown"

    def get_current_metadata(self) -> dict | None:
        return dict(self._current_meta) if self._current_meta else None

    def stop(self):
        if self._stopping:
            return
        self._stopping = True
        self._pos_timer.stop()
        try:
            self._poll_thread.state_ready.disconnect(self._on_state)
        except (RuntimeError, TypeError):
            pass
        self._poll_thread.stop()
        self._poll_thread.wait(5000)
