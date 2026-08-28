"""Browser WebSocket media-session listener.

A browser extension sends a versioned, source-identified state message over a
loopback WebSocket. Transport connectivity and active media are deliberately
separate: an idle extension must never pre-empt MPRIS or SMTC playback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal


class _WebSocketThread(QThread):
    """Run the loopback WebSocket server on a dedicated asyncio thread."""

    state_ready = Signal(object)
    connection_count_changed = Signal(int)
    server_error = Signal(str)

    def __init__(self, port: int, parent: QObject | None = None):
        super().__init__(parent)
        self._port = port
        self._running = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._connection_count = 0

    def stop(self):
        self._running = False
        self.requestInterruption()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)

    async def _handler(self, websocket):
        self._connection_count += 1
        self.connection_count_changed.emit(self._connection_count)
        try:
            async for message in websocket:
                if not self._running or self.isInterruptionRequested():
                    break
                try:
                    data = json.loads(message)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict):
                    self.state_ready.emit(data)
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
            max_queue=16,
            ping_interval=20,
            ping_timeout=20,
        )
        try:
            while self._running and not self.isInterruptionRequested():
                await asyncio.sleep(0.25)
        finally:
            self._server.close()
            await self._server.wait_closed()

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_server())
        except Exception as exc:
            if self._running and not self.isInterruptionRequested():
                self.server_error.emit(str(exc))
        finally:
            self._loop.close()
            self._loop = None


class BrowserWsListener(QObject):
    """Expose browser media state through the common player-listener API."""

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)
    players_changed = Signal(list)
    active_player_changed = Signal(str)
    connection_changed = Signal(bool)
    media_active_changed = Signal(bool)
    server_error = Signal(str)

    _PROTOCOL_VERSION = 1
    _MAX_TEXT_LENGTH = 1024
    _MAX_INSTANCE_ID_LENGTH = 128
    _STALE_TIMEOUT_SEC = 8.0
    _MAX_MEDIA_SEC = 2 * 24 * 60 * 60
    _MAX_RATE = 16.0
    _SEEK_THRESHOLD_MS = 750
    _SCORE_PLAYING = 1000.0
    _SCORE_PAUSED = 500.0
    _SCORE_OWNER = 100.0

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        port: int = 56789,
        clock=None,
        start_server: bool = True,
    ):
        super().__init__(parent)
        self._clock = clock or time.monotonic
        self._port = int(port)
        self._stopping = False

        self._current_meta: dict = {}
        self._status = "Stopped"
        self._active_player = ""
        self._pinned_player = ""
        self._players: list[str] = []
        self._connected = False
        self._connection_count = 0
        self._media_active = False
        self._active_source_key = ""
        self._sources: dict[str, dict] = {}
        self._last_sequence_by_source: dict[str, int] = {}

        self._position_ms = 0
        self._position_time = 0.0
        self._playback_rate = 1.0
        self._last_reported_pos_ms: int | None = None

        self._ws_thread: _WebSocketThread | None = None
        if start_server:
            self._ws_thread = _WebSocketThread(self._port, self)
            self._ws_thread.state_ready.connect(self._on_state)
            self._ws_thread.connection_count_changed.connect(
                self._on_connection_count_changed
            )
            self._ws_thread.server_error.connect(self.server_error.emit)
            self._ws_thread.start()

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start(16)

    def _on_connection_count_changed(self, count: int):
        self._connection_count = max(0, int(count))
        connected = self._connection_count > 0
        if connected != self._connected:
            self._connected = connected
            self.connection_changed.emit(connected)
        if not connected:
            self._deactivate_media()

    def _on_state(self, data: dict):
        if self._stopping:
            return
        try:
            self._apply_payload(data)
        except Exception as exc:
            print(f"[BrowserWS] state application failed: {exc}", flush=True)

    def _apply_payload(self, data: dict) -> bool:
        payload = self._validate_payload(data)
        if payload is None:
            return False

        source_key = payload["source_key"]
        sequence = payload["sequence"]
        previous_sequence = self._last_sequence_by_source.get(source_key, -1)
        if sequence <= previous_sequence:
            return False
        self._last_sequence_by_source[source_key] = sequence

        now = self._clock()
        self._prune_stale_sources(now)

        title = payload["title"]
        status = payload["status"]
        candidate = bool(title) and status in {"Playing", "Paused"}

        if not candidate:
            # Only the source that owns the active media may release it: an
            # idle second browser must not stop what another one is playing.
            self._sources.pop(source_key, None)
            if source_key == self._active_source_key:
                self._promote_best_source(now)
            return True

        self._sources[source_key] = {
            "title": title,
            "artist": payload["artist"],
            "album": payload["album"],
            "status": status,
            "position_ms": round(payload["position"] * 1000),
            "length_ms": round(payload["duration"] * 1000),
            "rate": payload["rate"],
            "updated_at": now,
        }

        best_key = self._select_source_key(now)
        if best_key is not None and (
            best_key == source_key or best_key != self._active_source_key
        ):
            self._adopt_source(best_key, now)
        return True

    def _source_score(self, source_key: str, record: dict, now: float):
        """Rank one source the way the extension ranks tabs within a browser."""
        age = now - record["updated_at"]
        if age > self._STALE_TIMEOUT_SEC:
            return None
        if record["status"] == "Playing":
            score = self._SCORE_PLAYING
        elif record["status"] == "Paused":
            score = self._SCORE_PAUSED
        else:
            return None
        # Hysteresis: the owning source keeps the media unless a higher
        # priority source appears, otherwise two live browsers publishing at
        # the same cadence would swap ownership on every heartbeat.
        if source_key == self._active_source_key:
            score += self._SCORE_OWNER
        score += max(0.0, self._STALE_TIMEOUT_SEC - age)
        return score

    def _select_source_key(self, now: float) -> str | None:
        best_key = None
        best_score = None
        for source_key, record in self._sources.items():
            score = self._source_score(source_key, record, now)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_key = source_key
                best_score = score
        return best_key

    def _prune_stale_sources(self, now: float):
        for source_key in [
            key
            for key, record in self._sources.items()
            if (now - record["updated_at"]) > self._STALE_TIMEOUT_SEC
        ]:
            del self._sources[source_key]

    def _promote_best_source(self, now: float):
        best_key = self._select_source_key(now)
        if best_key is None:
            self._deactivate_media()
        else:
            self._adopt_source(best_key, now)

    def _adopt_source(self, source_key: str, now: float):
        record = self._sources[source_key]
        status = record["status"]
        length_ms = record["length_ms"]
        position_ms = record["position_ms"]
        if status == "Playing":
            # A record adopted after its own report carries an aged position;
            # advance it instead of rewinding playback to where it was.
            position_ms += round(
                max(0.0, (now - record["updated_at"]) * 1000.0 * record["rate"])
            )
        if length_ms:
            position_ms = min(position_ms, length_ms)

        old_status = self._status
        old_meta = self._current_meta
        source_changed = source_key != self._active_source_key
        was_active = self._media_active

        new_meta = {
            "title": record["title"],
            "artist": record["artist"],
            "album": record["album"],
            "trackid": self._track_id(record["title"], record["artist"]),
            "length_ms": length_ms,
            "player_name": "browser-ws",
        }

        # Commit state before active-player signals so observers can query the
        # new metadata immediately from get_current_metadata().
        self._active_source_key = source_key
        self._current_meta = new_meta
        self._status = status
        self._update_timeline(
            now=now,
            position_ms=position_ms,
            rate=record["rate"],
            status=status,
            old_status=old_status,
            source_changed=source_changed,
        )

        if not was_active:
            self._set_media_active(True)
        if new_meta != old_meta:
            self.metadata_changed.emit(dict(new_meta))
        if status != old_status or not was_active:
            self.playback_state_changed.emit(status, "browser-ws")
        if status == "Paused":
            self.position_changed.emit(position_ms)

    @staticmethod
    def _track_id(title: str, artist: str) -> str:
        """Return an identity that is stable for one track.

        Service workers restart and tabs come and go constantly, so browser
        instance/tab/frame identity must never reach the lyrics cache key.
        """
        raw = f"{artist}\x1f{title}".casefold().encode("utf-8")
        return f"browser-ws:{hashlib.sha256(raw).hexdigest()[:16]}"

    def _update_timeline(
        self,
        *,
        now: float,
        position_ms: int,
        rate: float,
        status: str,
        old_status: str,
        source_changed: bool,
    ):
        if status != "Playing":
            self._position_ms = position_ms
            self._position_time = 0.0
            self._playback_rate = rate
            self._last_reported_pos_ms = position_ms
            return

        if self._position_time:
            elapsed = (now - self._position_time) * 1000.0 * self._playback_rate
            predicted_ms = self._position_ms + round(max(0.0, elapsed))
        else:
            predicted_ms = position_ms

        became_playing = old_status != "Playing"
        rate_changed = not math.isclose(
            rate, self._playback_rate, rel_tol=1e-9, abs_tol=1e-9
        )
        seek_detected = abs(position_ms - predicted_ms) > self._SEEK_THRESHOLD_MS

        if (
            self._last_reported_pos_ms is None
            or source_changed
            or became_playing
            or seek_detected
            or rate_changed
        ):
            self._position_ms = position_ms
            self._position_time = now
            self._playback_rate = rate
        self._last_reported_pos_ms = position_ms

    def _validate_payload(self, data: dict) -> dict | None:
        if not isinstance(data, dict):
            return None
        if data.get("protocol_version") != self._PROTOCOL_VERSION:
            return None

        sequence = data.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            return None
        observed_at_ms = self._finite_float(data.get("observed_at_ms"), -1.0)
        if observed_at_ms < 0:
            return None

        source = data.get("source")
        if not isinstance(source, dict):
            return None
        instance_id = self._safe_text(
            source.get("instance_id", ""), self._MAX_INSTANCE_ID_LENGTH
        )
        tab_id = source.get("tab_id")
        frame_id = source.get("frame_id")
        if not instance_id:
            return None
        if (
            not isinstance(tab_id, int)
            or isinstance(tab_id, bool)
            or tab_id < 0
            or not isinstance(frame_id, int)
            or isinstance(frame_id, bool)
            or frame_id < 0
        ):
            return None

        state = data.get("state")
        if not isinstance(state, dict):
            return None
        title = self._safe_text(state.get("title", ""), self._MAX_TEXT_LENGTH)
        artist = self._safe_text(state.get("artist", ""), self._MAX_TEXT_LENGTH)
        album = self._safe_text(state.get("album", ""), self._MAX_TEXT_LENGTH)
        if title is None or artist is None or album is None:
            return None

        status = state.get("status", "Stopped")
        if status not in {"Playing", "Paused", "Stopped"}:
            return None
        position = self._finite_float(state.get("position"), -1.0)
        duration = self._finite_float(state.get("duration"), -1.0)
        rate = self._finite_float(state.get("rate"), -1.0)
        if (
            position < 0
            or position > self._MAX_MEDIA_SEC
            or duration < 0
            or duration > self._MAX_MEDIA_SEC
            or rate <= 0
            or rate > self._MAX_RATE
        ):
            return None
        if duration > 0:
            position = min(position, duration)

        return {
            "source_key": f"{instance_id}:{tab_id}:{frame_id}",
            "sequence": sequence,
            "observed_at_ms": observed_at_ms,
            "title": title,
            "artist": artist,
            "album": album,
            "status": status,
            "position": position,
            "duration": duration,
            "rate": rate,
        }

    @staticmethod
    def _finite_float(value, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _safe_text(value, maximum: int) -> str | None:
        if value is None:
            return ""
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text if len(text) <= maximum else None

    def _enforce_freshness(self):
        if not self._media_active:
            return
        if not self._connected:
            self._deactivate_media()
            return
        now = self._clock()
        self._prune_stale_sources(now)
        if self._active_source_key not in self._sources:
            self._promote_best_source(now)

    def _tick_position(self):
        self._enforce_freshness()
        if not self._media_active or self._status != "Playing":
            return
        if self._position_time <= 0:
            return
        elapsed = (
            (self._clock() - self._position_time)
            * 1000.0
            * self._playback_rate
        )
        position = self._position_ms + round(max(0.0, elapsed))
        length = self._current_meta.get("length_ms", 0)
        if length:
            position = min(position, length)
        self.position_changed.emit(max(0, position))

    def _set_media_active(self, active: bool):
        active = bool(active)
        if active == self._media_active:
            return
        self._media_active = active
        if active:
            self._players = ["browser-ws"]
            self._active_player = "browser-ws"
        else:
            self._players = []
            self._active_player = ""
        self.media_active_changed.emit(active)
        self.players_changed.emit(list(self._players))
        self.active_player_changed.emit(self._active_player)

    def _deactivate_media(self):
        was_active = self._media_active
        old_status = self._status
        self._position_time = 0.0
        self._last_reported_pos_ms = None
        self._active_source_key = ""
        self._sources.clear()
        self._current_meta = {}
        self._status = "Stopped"
        self._set_media_active(False)
        if was_active or old_status != "Stopped":
            self.playback_state_changed.emit("Stopped", "browser-ws")

    def pin_player(self, player_id: str):
        self._pinned_player = player_id

    def unpin_player(self):
        self._pinned_player = ""

    def get_players(self) -> list[str]:
        return list(self._players)

    def get_active_player(self) -> str:
        return self._active_player

    def get_player_status(self, player_id: str) -> str:
        if player_id != "browser-ws":
            return "Unknown"
        return self._status if self._media_active else "Stopped"

    def get_current_metadata(self) -> dict | None:
        return dict(self._current_meta) if self._media_active else None

    def is_connected(self) -> bool:
        return self._connected

    def is_media_active(self) -> bool:
        return self._media_active

    def get_port(self) -> int:
        return self._port

    def stop(self):
        if self._stopping:
            return
        self._stopping = True
        self._pos_timer.stop()
        self._deactivate_media()
        if self._ws_thread is not None:
            try:
                self._ws_thread.state_ready.disconnect(self._on_state)
            except (RuntimeError, TypeError):
                pass
            self._ws_thread.stop()
            self._ws_thread.wait(5000)
