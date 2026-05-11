"""Browser WebSocket media session listener.

Receives high-precision media metadata and playback position from a
browser extension via a local WebSocket connection.  Emits the same
PySide6 signals as SmtcListener so the rest of the application is
unaware of the transport difference.

Requires: websockets
"""

import asyncio
import json
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal


class _WebSocketThread(QThread):
    """Background thread that runs an asyncio WebSocket server."""

    state_ready = Signal(object)  # dict | None
    connected = Signal()
    disconnected = Signal()

    def __init__(self, port: int, parent: QObject | None = None):
        super().__init__(parent)
        self._port = port
        self._running = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _handler(self, websocket):
        self.connected.emit()
        try:
            async for message in websocket:
                if not self._running:
                    break
                try:
                    data = json.loads(message)
                    self.state_ready.emit(data)
                except json.JSONDecodeError:
                    pass
        finally:
            self.disconnected.emit()

    async def _run_server(self):
        import websockets

        self._server = await websockets.serve(
            self._handler, "127.0.0.1", self._port
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

    def __init__(self, parent: QObject | None = None, *, port: int = 56789):
        super().__init__(parent)

        self._current_meta: dict = {}
        self._status = "Stopped"
        self._active_player = ""
        self._pinned_player = ""
        self._players: list[str] = []
        self._connected = False

        # Position interpolation state
        self._position_ms = 0
        self._position_time = 0.0
        self._playback_rate = 1.0
        self._last_reported_pos_ms: int | None = None

        # Background WebSocket server thread
        self._ws_thread = _WebSocketThread(port, self)
        self._ws_thread.state_ready.connect(self._on_state)
        self._ws_thread.connected.connect(self._on_connected)
        self._ws_thread.disconnected.connect(self._on_disconnected)
        self._ws_thread.start()

        # High-frequency position interpolation (~60 Hz)
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start(16)

    # ------------------------------------------------------------------
    #  Connection lifecycle
    # ------------------------------------------------------------------

    def _on_connected(self):
        if not self._connected:
            self._connected = True
            self._players = ["browser-ws"]
            self.players_changed.emit(self._players)
            self._active_player = "browser-ws"
            self.active_player_changed.emit("browser-ws")

    def _on_disconnected(self):
        if self._connected:
            self._connected = False
            self._players = []
            self.players_changed.emit([])
            self._active_player = ""
            self.active_player_changed.emit("")
            self._status = "Stopped"
            self.playback_state_changed.emit("Stopped", "browser-ws")

    # ------------------------------------------------------------------
    #  State update handler
    # ------------------------------------------------------------------

    def _on_state(self, data: dict):
        try:
            self._on_state_impl(data)
        except Exception as exc:
            print(f"[BrowserWS] _on_state error: {exc}", flush=True)

    def _on_state_impl(self, data: dict):
        title = (data.get("title") or "").strip()
        artist = (data.get("artist") or "").strip()
        album = (data.get("album") or "").strip()
        status_raw = data.get("status", "Stopped")
        pos_sec = data.get("position", 0)
        dur_sec = data.get("duration", 0)
        rate = float(data.get("rate", 1.0) or 1.0)

        if status_raw in ("Playing", "Paused", "Stopped"):
            status = status_raw
        else:
            status = "Stopped"

        pos_ms = round(pos_sec * 1000)
        length_ms = round(dur_sec * 1000)

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
            self.metadata_changed.emit(new_meta)

        # --- Playback status ---
        old_status = self._status
        status_changed = status != self._status
        if status_changed:
            print(f"[BrowserWS] status {old_status} → {status}", flush=True)
            self._status = status
            self.playback_state_changed.emit(status, "browser-ws")

        # --- Position handling ---
        if status == "Playing":
            self._playback_rate = rate

            last_pos = self._last_reported_pos_ms

            # Browser sends high-precision position but network jitter can
            # cause micro-stutters if we snap every packet.  Anchor only on
            # seek (>2 s change) or when playback just started.
            seek_detected = (
                last_pos is not None
                and abs(pos_ms - last_pos) > 2000
            )
            became_playing = status_changed and old_status != "Playing"

            if last_pos is None or seek_detected or became_playing:
                self._position_ms = pos_ms
                self._position_time = time.monotonic()
                self._last_reported_pos_ms = pos_ms
                print(
                    f"[BrowserWS] pos anchor: {pos_ms}ms "
                    f"(seek={seek_detected}, became_playing={became_playing})",
                    flush=True,
                )
        else:
            self._position_ms = pos_ms
            self._position_time = 0.0
            self._last_reported_pos_ms = pos_ms

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
        return self._status if player_id == "browser-ws" else "Unknown"

    def get_current_metadata(self) -> dict | None:
        return self._current_meta if self._current_meta else None
