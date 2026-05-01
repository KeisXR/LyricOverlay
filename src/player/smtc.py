"""Windows SMTC (System Media Transport Controls) listener.

Monitors the Windows System Media Transport Controls session for
currently playing media and emits PySide6 signals compatible with
the Linux MprisListener interface.

Requires: winrt-runtime, winrt-Windows.Media.Control
"""

import asyncio
import datetime
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal


async def _query_smtc() -> dict | None:
    """Async query of current SMTC session state.

    Returns a plain dict with session info, or None if no session is active.
    """
    from winrt.windows.media.control import (  # type: ignore[import]
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )

    manager = await SessionManager.request_async()
    session = manager.get_current_session()
    if session is None:
        return None

    try:
        props = await session.try_get_media_properties_async()
    except Exception:
        return None

    playback_info = session.get_playback_info()
    timeline = session.get_timeline_properties()

    title = (getattr(props, "title", None) or "").strip()
    artist = (getattr(props, "artist", None) or "").strip()
    album = (getattr(props, "album_title", None) or "").strip()
    app_id = getattr(session, "source_app_user_model_id", "") or ""

    # Playback status
    status = "Stopped"
    rate = 1.0
    if playback_info:
        pbs = getattr(playback_info, "playback_status", None)
        if pbs == PlaybackStatus.PLAYING:
            status = "Playing"
        elif pbs == PlaybackStatus.PAUSED:
            status = "Paused"
        try:
            rate = float(getattr(playback_info, "playback_rate", 1.0) or 1.0)
        except (TypeError, ValueError):
            rate = 1.0

    # Timeline (position / duration)
    pos_ms = 0
    length_ms = 0
    if timeline:
        try:
            raw_pos_s = timeline.position.total_seconds()
            # SMTC reports a static snapshot of the position as of the last time
            # the app called SetTimelineProperties.  Advance it by the elapsed
            # wall-clock time since that update so every poll returns the true
            # current playback position instead of a frozen value.
            try:
                last_updated = timeline.last_updated_time
                # winrt maps Windows.Foundation.DateTime to a UTC-aware
                # datetime.datetime; the tzinfo guard handles any rare case
                # where a naive datetime is returned.
                if hasattr(last_updated, "tzinfo") and last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=datetime.timezone.utc)
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                elapsed_s = (now_utc - last_updated).total_seconds()
                if elapsed_s < 0:
                    # Clock skew or timezone mismatch — skip the adjustment.
                    print(
                        f"[SMTC] last_updated_time is in the future by"
                        f" {-elapsed_s:.3f}s; skipping position advance"
                    )
                    elapsed_s = 0.0
                pos_ms = round((raw_pos_s + elapsed_s * rate) * 1000)
            except (AttributeError, TypeError, OverflowError) as exc:
                print(f"[SMTC] Could not advance position via last_updated_time: {exc}")
                pos_ms = round(raw_pos_s * 1000)
        except Exception:
            pass
        try:
            length_ms = round(timeline.end_time.total_seconds() * 1000)
        except Exception:
            pass

    # All active sessions (for player list)
    sessions: list[str] = []
    try:
        for s in manager.get_sessions():
            sid = getattr(s, "source_app_user_model_id", "") or ""
            if sid:
                sessions.append(sid)
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
    """Background thread that polls SMTC every 500 ms.

    Maintains a persistent asyncio event loop to avoid the overhead of
    creating and tearing down a new loop on every poll cycle.
    """

    state_ready = Signal(object)  # dict | None

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while self._running:
                try:
                    state = loop.run_until_complete(_query_smtc())
                except Exception as exc:
                    print(f"[SMTC] Failed to query SMTC session state: {exc}")
                    state = None
                self.state_ready.emit(state)
                self.msleep(500)
        finally:
            loop.close()


class SmtcListener(QObject):
    """Windows SMTC session watcher.

    Emits the same signals as MprisListener so the rest of the application
    is unaware of the platform difference.

    Signals
    -------
    metadata_changed(dict)
        Emitted when the active session's track changes.
        dict keys: title, artist, album, trackid, length_ms, player_name

    position_changed(int)
        Interpolated playback position in ms, emitted at ~60 Hz.

    playback_state_changed(str, str)
        (status, app_id) where status is "Playing" / "Paused" / "Stopped".

    players_changed(list[str])
        Emitted when the set of active SMTC sessions changes.

    active_player_changed(str)
        Emitted when the active session changes.
    """

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)
    players_changed = Signal(list)
    active_player_changed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

        self._current_meta: dict = {}
        self._status = "Stopped"
        self._active_player = ""
        self._pinned_player = ""
        self._players: list[str] = []

        # Position interpolation state
        self._position_ms = 0
        self._position_time = 0.0
        self._playback_rate = 1.0

        # Background polling thread
        self._poll_thread = _SmtcPollThread(self)
        self._poll_thread.state_ready.connect(self._on_state)
        self._poll_thread.start()

        # High-frequency position interpolation (~60 Hz)
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start(16)

    # ------------------------------------------------------------------
    #  State update handler (called from main thread via Qt signal)
    # ------------------------------------------------------------------

    def _on_state(self, state: dict | None):
        if state is None:
            if self._active_player:
                self._active_player = ""
                self._players = []
                self._status = "Stopped"
                self.active_player_changed.emit("")
                self.players_changed.emit([])
            return

        app_id: str = state["app_id"]
        sessions: list[str] = state["sessions"]
        title: str = state["title"]
        artist: str = state["artist"]
        album: str = state["album"]
        status: str = state["status"]
        rate: float = state["rate"]
        pos_ms: int = state["pos_ms"]
        length_ms: int = state["length_ms"]

        # --- Players list ---
        if sessions != self._players:
            self._players = sessions
            self.players_changed.emit(sessions)

        # --- Active session ---
        if app_id != self._active_player:
            self._active_player = app_id
            self.active_player_changed.emit(app_id)

        # --- Metadata ---
        new_meta = {
            "title": title,
            "artist": artist,
            "album": album,
            "trackid": app_id,
            "length_ms": length_ms,
            "player_name": app_id,
        }
        prev = self._current_meta
        if (
            new_meta.get("title") != prev.get("title")
            or new_meta.get("artist") != prev.get("artist")
            or new_meta.get("album") != prev.get("album")
        ):
            self._current_meta = new_meta
            print(
                f"[SMTC] meta → artist=\"{artist}\" title=\"{title}\""
                f"  ({app_id})"
            )
            self.metadata_changed.emit(new_meta)

        # --- Playback status ---
        prev_status = self._status
        if status != self._status:
            self._status = status
            self.playback_state_changed.emit(status, app_id)

        # --- Sync position clock ---
        # Only reset the interpolation anchor when necessary:
        #   • the first time we see a Playing state (_position_time == 0)
        #   • transitioning into Playing from a non-Playing state (e.g. resume)
        #   • the reported position diverges significantly from interpolated
        #     position, indicating the user performed a seek
        # This prevents the anchor being reset every 500 ms poll cycle to the
        # same stale SMTC snapshot value, which would freeze the seek bar.
        _SEEK_THRESHOLD_MS = 1500
        if status == "Playing":
            self._playback_rate = rate
            if self._position_time > 0 and prev_status == "Playing":
                elapsed = (
                    (time.monotonic() - self._position_time)
                    * 1000.0
                    * self._playback_rate
                )
                expected_ms = self._position_ms + round(elapsed)
                if abs(pos_ms - expected_ms) > _SEEK_THRESHOLD_MS:
                    self._position_ms = pos_ms
                    self._position_time = time.monotonic()
            else:
                # First sync or resuming from Paused/Stopped
                self._position_ms = pos_ms
                self._position_time = time.monotonic()

    # ------------------------------------------------------------------
    #  Position interpolation
    # ------------------------------------------------------------------

    def _tick_position(self):
        if self._status != "Playing" or self._position_time == 0:
            return
        elapsed = (
            (time.monotonic() - self._position_time)
            * 1000.0
            * self._playback_rate
        )
        pos = self._position_ms + round(elapsed)
        length = self._current_meta.get("length_ms", 0)
        if length:
            pos = min(pos, length)
        self.position_changed.emit(pos)

    # ------------------------------------------------------------------
    #  Player pinning
    #  SMTC always exposes a single "current" session determined by
    #  Windows; there is no API to force a different session to become
    #  the active one.  These methods exist to satisfy the MprisListener
    #  interface so the rest of the application can call them
    #  unconditionally on any platform.
    # ------------------------------------------------------------------

    def pin_player(self, player_id: str):
        self._pinned_player = player_id

    def unpin_player(self):
        self._pinned_player = ""

    # ------------------------------------------------------------------
    #  Public query methods (compatible with MprisListener)
    # ------------------------------------------------------------------

    def get_players(self) -> list[str]:
        return list(self._players)

    def get_active_player(self) -> str:
        return self._active_player

    def get_player_status(self, player_id: str) -> str:
        return self._status if player_id == self._active_player else "Unknown"

    def get_current_metadata(self) -> dict | None:
        return self._current_meta if self._current_meta else None
