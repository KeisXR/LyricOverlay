"""Browser WebSocket media session listener.

Receives high-precision media metadata and playback position from a
browser extension via a local WebSocket connection.  Emits the same
PySide6 signals as SmtcListener so the rest of the application is
unaware of the transport difference.

Requires: websockets
"""

import asyncio
import json
import math
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal


class _WebSocketThread(QThread):
    """Background thread that runs an asyncio WebSocket server."""

    state_ready = Signal(object)  # dict | None
    connection_count_changed = Signal(int)

    def __init__(self, port: int, parent: QObject | None = None):
        super().__init__(parent)
        self._port = port
        self._running = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._connection_count = 0

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)

    async def _handler(self, websocket):
        self._connection_count += 1
        self.connection_count_changed.emit(self._connection_count)
        try:
            async for message in websocket:
                if not self._running:
                    break
                try:
                    data = json.loads(message)
                    if isinstance(data, dict):
                        self.state_ready.emit(data)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        finally:
            self._connection_count = max(0, self._connection_count - 1)
            self.connection_count_changed.emit(self._connection_count)

    async def _run_server(self):
        import websockets

        self._server = await websockets.serve(
            self._handler,
            "127.0.0.1",
            self._port,
            max_size=64 * 1024,
        )
        try:
            while self._running:
                await asyncio.sleep(0.5)
        finally:
            self._server.close()
            await self._server.wait_closed()

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_server())
        finally:
            self._loop.close()


class BrowserWsListener(QObject):
    """Browser WebSocket session watcher.

    Emits the same signals as SmtcListener so the rest of the application
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
        Emitted when the set of active sessions changes.

    active_player_changed(str)
        Emitted when the active session changes.
    """

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)
    players_changed = Signal(list)
    active_player_changed = Signal(str)
    connection_changed = Signal(bool)
    media_active_changed = Signal(bool)

    _PROTOCOL_VERSION = 1
    _MAX_TEXT_LENGTH = 1024
    _STALE_TIMEOUT_SEC = 8.0
    _MAX_POSITION_SEC = 60 * 60 * 24 * 2
    _MAX_DURATION_SEC = 60 * 60 * 24 * 2

    def __init__(self, parent: QObject | None = None, *, port: int = 56789):
        super().__init__(parent)

        self._current_meta: dict = {}
        self._port = port
        self._status = "Stopped"
        self._active_player = ""
        self._pinned_player = ""
        self._players: list[str] = []
        self._connected = False
        self._connection_count = 0
        self._media_active = False
        self._last_state_time = 0.0

        # Position interpolation state
        self._position_ms = 0
        self._position_time = 0.0
        self._playback_rate = 1.0
        self._last_reported_pos_ms: int | None = None

        # Background WebSocket server thread
        self._ws_thread = _WebSocketThread(port, self)
        self._ws_thread.state_ready.connect(self._on_state)
        self._ws_thread.connection_count_changed.connect(self._on_connection_count_changed)
        self._ws_thread.start()

        # High-frequency position interpolation (~60 Hz)
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start(16)

    # ------------------------------------------------------------------
    #  Connection lifecycle
    # ------------------------------------------------------------------

    def _on_connection_count_changed(self, count: int):
        self._connection_count = max(0, int(count))
        connected = self._connection_count > 0
        if connected != self._connected:
            self._connected = connected
            self.connection_changed.emit(connected)
        if not connected:
            self._deactivate_media()

    # ------------------------------------------------------------------
    #  State update handler
    # ------------------------------------------------------------------

    def _on_state(self, data: dict):
        try:
            self._on_state_impl(data)
        except Exception as exc:
            print(f"[BrowserWS] _on_state error: {exc}", flush=True)

    def _on_state_impl(self, data: dict):
        state = self._validate_payload(data)
        if state is None:
            return

        self._last_state_time = time.monotonic()
        title = state["title"]
        artist = state["artist"]
        album = state["album"]
        status = state["status"]
        pos_sec = state["position"]
        dur_sec = state["duration"]
        rate = state["rate"]

        pos_ms = round(pos_sec * 1000)
        length_ms = round(dur_sec * 1000)

        self._set_media_active(self._is_candidate_state(title, status))

        # --- Metadata ---
        new_meta = {
            "title": title,
            "artist": artist,
            "album": album,
            "trackid": "browser-ws",
            "length_ms": length_ms,
            "player_name": "browser-ws",
        }
        prev = self._current_meta
        meta_changed = (
            new_meta.get("title") != prev.get("title")
            or new_meta.get("artist") != prev.get("artist")
            or new_meta.get("album") != prev.get("album")
        )
        self._current_meta = new_meta
        if meta_changed:
            print(
                f"[BrowserWS] meta → artist=\"{artist}\" title=\"{title}\"",
                flush=True,
            )
            if self._media_active:
                self.metadata_changed.emit(new_meta)

        # --- Playback status ---
        old_status = self._status
        status_changed = status != self._status
        if status_changed:
            print(f"[BrowserWS] status {old_status} → {status}", flush=True)
            self._status = status
            if self._media_active or status == "Stopped":
                self.playback_state_changed.emit(status, "browser-ws")

        # --- Position handling ---
        if status == "Playing":
            self._playback_rate = rate

            if self._position_time:
                elapsed = (
                    (time.monotonic() - self._position_time)
                    * 1000.0
                    * self._playback_rate
                )
                predicted_pos_ms = self._position_ms + round(elapsed)
            else:
                predicted_pos_ms = pos_ms

            # Browser sends high-precision position but network jitter can
            # cause micro-stutters if we snap every packet.  Anchor only on
            # actual drift/seek or when playback just started.
            seek_detected = abs(pos_ms - predicted_pos_ms) > 750
            became_playing = status_changed and old_status != "Playing"

            if self._last_reported_pos_ms is None or seek_detected or became_playing:
                self._position_ms = pos_ms
                self._position_time = time.monotonic()
                print(
                    f"[BrowserWS] pos anchor: {pos_ms}ms "
                    f"(seek={seek_detected}, became_playing={became_playing})",
                    flush=True,
                )
            self._last_reported_pos_ms = pos_ms
        else:
            self._position_ms = pos_ms
            self._position_time = 0.0
            self._last_reported_pos_ms = pos_ms
            if self._media_active:
                self.position_changed.emit(pos_ms)

    @staticmethod
    def _safe_float(value, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    def _tick_position(self):
        self._enforce_freshness()
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
    #  Player pinning (compatible with MprisListener)
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
        if player_id != "browser-ws":
            return "Unknown"
        return self._status if self._media_active else "Stopped"

    def get_current_metadata(self) -> dict | None:
        return self._current_meta if self._media_active and self._current_meta else None

    def is_connected(self) -> bool:
        return self._connected

    def is_media_active(self) -> bool:
        return self._media_active

    def get_port(self) -> int:
        return self._port

    def stop(self):
        self._pos_timer.stop()
        self._ws_thread.stop()
        self._ws_thread.wait(2000)

    def _set_media_active(self, active: bool):
        if active == self._media_active:
            return
        self._media_active = active
        self.media_active_changed.emit(active)
        if active:
            self._players = ["browser-ws"]
            self._active_player = "browser-ws"
            self.players_changed.emit(self._players)
            self.active_player_changed.emit("browser-ws")
            return
        self._players = []
        self._active_player = ""
        self.players_changed.emit([])
        self.active_player_changed.emit("")

    def _deactivate_media(self):
        self._set_media_active(False)
        self._position_time = 0.0
        if self._status != "Stopped":
            self._status = "Stopped"
            self.playback_state_changed.emit("Stopped", "browser-ws")

    def _enforce_freshness(self):
        if not self._media_active:
            return
        if not self._connected:
            self._deactivate_media()
            return
        if self._last_state_time <= 0:
            return
        if (time.monotonic() - self._last_state_time) > self._STALE_TIMEOUT_SEC:
            self._deactivate_media()

    def _validate_payload(self, data: dict) -> dict | None:
        if not isinstance(data, dict):
            return None
        if data.get("protocol_version") != self._PROTOCOL_VERSION:
            return None
        sequence = data.get("sequence")
        if not isinstance(sequence, int) or sequence < 0:
            return None
        observed = self._safe_float(data.get("observed_at_ms"), -1.0)
        if observed < 0:
            return None
        source = data.get("source")
        if not isinstance(source, dict):
            return None
        tab_id = source.get("tab_id")
        frame_id = source.get("frame_id")
        if not isinstance(tab_id, int) or not isinstance(frame_id, int):
            return None
        state = data.get("state")
        if not isinstance(state, dict):
            return None
        title = self._safe_text(state.get("title", ""))
        artist = self._safe_text(state.get("artist", ""))
        album = self._safe_text(state.get("album", ""))
        if title is None or artist is None or album is None:
            return None
        status = state.get("status", "Stopped")
        if status not in ("Playing", "Paused", "Stopped"):
            return None
        position = self._safe_float(state.get("position"), -1.0)
        duration = self._safe_float(state.get("duration"), -1.0)
        rate = self._safe_float(state.get("rate"), -1.0)
        if (
            position < 0
            or position > self._MAX_POSITION_SEC
            or duration < 0
            or duration > self._MAX_DURATION_SEC
            or rate <= 0
            or rate > 16.0
        ):
            return None
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "status": status,
            "position": position,
            "duration": duration,
            "rate": rate,
        }

    def _safe_text(self, value) -> str | None:
        if value is None:
            return ""
        if not isinstance(value, str):
            return None
        text = value.strip()
        if len(text) > self._MAX_TEXT_LENGTH:
            return None
        return text

    @staticmethod
    def _is_candidate_state(title: str, status: str) -> bool:
        return bool(title) and status in ("Playing", "Paused")
