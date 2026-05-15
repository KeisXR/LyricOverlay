"""
MPRIS D-Bus listener for KDE media players.
Handles discovery, metadata tracking, position interpolation,
and active player selection.

Requires: player -> org.mpris.MediaPlayer2.* on D-Bus session bus.
"""

import time

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from PySide6.QtCore import QObject, QTimer, Signal
from meta_utils import enrich_missing_meta

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

DBusGMainLoop(set_as_default=True)


class MprisListener(QObject):
    """Listens to all MPRIS players on the session bus and emits signals.

    Signals
    -------
    metadata_changed(dict)
        Emitted when any player's metadata changes.
        dict keys: title, artist, album, trackid, length_ms, player_name

    position_changed(int)
        Emits an interpolated playback position (ms) at ~30 Hz for the
        active player.

    playback_state_changed(str, str)
        Emits (status, bus_name). status is "Playing" / "Paused" / "Stopped".

    players_changed(list[str])
        Emitted when the set of discovered MPRIS players changes.

    active_player_changed(str)
        Emitted when the active player selection changes.
    """

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)  # status, bus_name
    players_changed = Signal(list)
    active_player_changed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._bus = dbus.SessionBus()

        # Per-player state: bus_name -> {"status": str, "metadata": dict|None}
        self._players: dict[str, dict] = {}
        self._active_player = ""
        self._pinned_player = ""

        # Position interpolation state (for active player)
        self._position_ms = 0
        self._position_time = 0.0
        self._playback_rate = 1.0
        self._last_sync_latency_ms = 20  # estimated D-Bus RTT, updated at runtime

        # --- Timers ---
        # Pump glib events into Qt event loop
        self._glib_timer = QTimer(self)
        self._glib_timer.timeout.connect(self._pump_glib)
        self._glib_timer.start(20)

        # High-frequency position interpolation (~60 Hz for smoother sync)
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._tick_position)
        self._pos_timer.start(16)

        # --- Subscribe to bus events ---
        # NameOwnerChanged: detect new/removed MPRIS players
        self._bus.add_signal_receiver(
            self._on_name_owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
        )

        # Scan for already-running players — deferred so signal connections
        # can be established first in main.py.
        QTimer.singleShot(0, self._scan_players)

    # ------------------------------------------------------------------
    #  GLib pump
    # ------------------------------------------------------------------

    def _pump_glib(self):
        """Process pending glib events inside Qt's event loop."""
        ctx = GLib.main_context_default()
        while ctx and ctx.pending():
            ctx.iteration(False)

    # ------------------------------------------------------------------
    #  Player discovery
    # ------------------------------------------------------------------

    def _scan_players(self):
        names = self._bus.list_names()
        for name in names:
            if name.startswith(MPRIS_PREFIX):
                self._add_player(name)

    def _add_player(self, bus_name: str):
        if bus_name in self._players:
            return
        self._players[bus_name] = {"status": "Stopped", "metadata": None}
        self._subscribe_player(bus_name)
        self.players_changed.emit(list(self._players.keys()))
        if not self._active_player and self._players:
            self._select_active()

    def _remove_player(self, bus_name: str):
        self._players.pop(bus_name, None)
        self.players_changed.emit(list(self._players.keys()))
        if bus_name == self._active_player:
            self._select_active()

    def _subscribe_player(self, bus_name: str):
        """Connect to PropertiesChanged and fetch initial state."""
        try:
            proxy = self._bus.get_object(bus_name, MPRIS_OBJECT_PATH)
            props_iface = dbus.Interface(proxy, PROPERTIES_IFACE)
            player_iface = dbus.Interface(proxy, MPRIS_PLAYER_IFACE)

            # Wrapper to capture bus_name in the closure
            def on_props(iface, changed, invalidated, p=bus_name):
                self._on_properties_changed(p, iface, changed, invalidated)

            props_iface.connect_to_signal("PropertiesChanged", on_props)
            player_iface.connect_to_signal(
                "Seeked",
                lambda position, p=bus_name: self._on_seeked(p, position),
            )

            # Fetch initial state so we have metadata before any signal fires
            self._fetch_initial(bus_name, props_iface)
        except dbus.DBusException as exc:
            print(f"[MPRIS] Failed to subscribe to {bus_name}: {exc}")

    def _fetch_initial(self, bus_name: str, props_iface):
        try:
            meta = props_iface.Get(MPRIS_PLAYER_IFACE, "Metadata")
            self._on_properties_changed(
                bus_name, MPRIS_PLAYER_IFACE, {"Metadata": meta}, []
            )
            rate = props_iface.Get(MPRIS_PLAYER_IFACE, "Rate")
            self._on_properties_changed(
                bus_name, MPRIS_PLAYER_IFACE, {"Rate": rate}, []
            )
            status = props_iface.Get(MPRIS_PLAYER_IFACE, "PlaybackStatus")
            self._on_properties_changed(
                bus_name, MPRIS_PLAYER_IFACE, {"PlaybackStatus": status}, []
            )
        except dbus.DBusException:
            pass

    def _on_name_owner_changed(self, name: str, old_owner: str, new_owner: str):
        if not name.startswith(MPRIS_PREFIX):
            return
        if new_owner and not old_owner:
            self._add_player(name)
        elif old_owner and not new_owner:
            self._remove_player(name)

    # ------------------------------------------------------------------
    #  Property change handler
    # ------------------------------------------------------------------

    def _on_properties_changed(
        self, bus_name: str, interface: str, changed: dict, invalidated: list
    ):
        if interface != MPRIS_PLAYER_IFACE:
            return

        player = self._players.get(bus_name)
        if player is None:
            return

        # --- Metadata ---
        if "Metadata" in changed:
            meta = self._parse_metadata(bus_name, changed["Metadata"])
            if meta:
                meta = self._enrich_metadata(bus_name, meta)
                player["metadata"] = meta
                print(f"[MPRIS] meta: \"{meta.get('title','?')}\" by \"{meta.get('artist','?')}\"  ({meta.get('player_name','?')})")
                if bus_name == self._active_player:
                    if player.get("status") == "Playing":
                        self._sync_position_from_player(bus_name)
                    self.metadata_changed.emit(meta)

        # --- PlaybackStatus ---
        if "PlaybackStatus" in changed:
            status = str(changed["PlaybackStatus"])
            player["status"] = status
            self.playback_state_changed.emit(status, bus_name)

            if status == "Playing":
                if bus_name != self._active_player:
                    # Don't blindly switch on every Playing event.
                    # Multiple MPRIS providers (e.g. browser + Plasma
                    # integration) can report the same media and flap.
                    self._select_active()
                # Re-sync position when playback starts
                # NOTE: update _position_ms FIRST, then reset the clock so
                # interpolation starts from the exact sync point.
                self._sync_position_from_player(bus_name)
            elif bus_name == self._active_player and status in ("Paused", "Stopped"):
                self._select_active()

        # --- Rate ---
        if "Rate" in changed and bus_name == self._active_player:
            self._playback_rate = float(changed["Rate"])
            # Re-sync so interpolation starts from the real current position
            self._sync_position_from_player(bus_name)

        # --- Position (periodic, ~1 Hz from MPRIS) ---
        if "Position" in changed and bus_name == self._active_player:
            # The Position value in the signal is stale by the D-Bus
            # propagation time. Compensate with our last measured latency.
            self._position_ms = int(changed["Position"]) // 1000 + self._last_sync_latency_ms
            self._position_time = time.monotonic()

    def _on_seeked(self, bus_name: str, position_us: int):
        """Handle explicit MPRIS seek notifications.

        Some players emit Seeked without a matching PropertiesChanged(Position),
        so relying on PropertiesChanged alone leaves the local interpolation
        clock offset until the next full sync.
        """
        if bus_name != self._active_player:
            return
        self._position_ms = int(position_us) // 1000
        self._position_time = time.monotonic()

    def _sync_position_from_player(self, bus_name: str):
        """Issue a synchronous Get(Position) call to sync the local clock.

        Measures D-Bus round-trip latency and adds it to the reported
        position so our local interpolation starts from the *actual*
        playback position, not the stale value at call start.
        """
        try:
            t0 = time.monotonic()
            proxy = self._bus.get_object(bus_name, MPRIS_OBJECT_PATH)
            props = dbus.Interface(proxy, PROPERTIES_IFACE)
            pos_us = props.Get(MPRIS_PLAYER_IFACE, "Position")
            t1 = time.monotonic()
            latency_ms = int((t1 - t0) * 1000.0 * self._playback_rate)
            self._last_sync_latency_ms = max(0, latency_ms)
            self._position_ms = int(pos_us) // 1000 + self._last_sync_latency_ms
            self._position_time = t1
        except dbus.DBusException:
            pass

    def _parse_metadata(self, bus_name: str, metadata_dict: dbus.Dictionary) -> dict | None:
        """Convert MPRIS Metadata variant map into a plain dict.

        Cleans up:
        - YouTube Music appends `` | YouTube Music`` to titles
        - Spotify web appends `` | Spotify``
        - Empty artist strings from Chrome-like players
        """
        if not metadata_dict:
            return None
        try:
            title = str(metadata_dict.get("xesam:title", ""))
            artists = metadata_dict.get("xesam:artist", [])
            artist = ", ".join(str(a) for a in artists if a) if artists else ""
            album = str(metadata_dict.get("xesam:album", ""))
            trackid = str(metadata_dict.get("mpris:trackid", "/"))
            length_us = metadata_dict.get("mpris:length", 0)
            length_ms = int(length_us) // 1000 if length_us else 0

            # Strip site-name suffixes (YouTube Music, Spotify, etc.)
            for sep in (" | YouTube Music", " | YouTube", " - YouTube", " | Spotify"):
                if sep in title:
                    title = title.split(sep)[0].strip()
                    break

            return {
                "title": title,
                "artist": artist,
                "album": album,
                "trackid": trackid,
                "length_ms": length_ms,
                "player_name": bus_name,
            }
        except Exception:
            return None

    def _enrich_metadata(self, bus_name: str, meta: dict) -> dict:
        candidates = []
        for other_bus, player in self._players.items():
            if other_bus == bus_name:
                continue
            other = player.get("metadata")
            if other:
                candidates.append(other)
        enriched, changed = enrich_missing_meta(meta, candidates)
        if changed or enriched.get("player_name") != bus_name:
            enriched = dict(enriched)
            enriched["player_name"] = bus_name
        return enriched

    # ------------------------------------------------------------------
    #  Position interpolation
    # ------------------------------------------------------------------

    def _tick_position(self):
        player = self._players.get(self._active_player)
        if not player or player.get("status") != "Playing":
            return
        if self._position_time == 0:
            return
        elapsed = (time.monotonic() - self._position_time) * 1000.0 * self._playback_rate
        pos = self._position_ms + round(elapsed)
        meta = player.get("metadata")
        if meta and meta.get("length_ms"):
            pos = min(pos, meta["length_ms"])
        self.position_changed.emit(pos)

    # ------------------------------------------------------------------
    #  Active player selection
    # ------------------------------------------------------------------

    def _select_active(self):
        """Priority rules: pinned > current Playing > Playing > existing > first available."""
        # 1. Pinned player
        if self._pinned_player and self._pinned_player in self._players:
            self._set_active(self._pinned_player)
            return

        # 2. Keep current player when it is still playing to avoid rapid
        #    oscillation between duplicate providers.
        current = self._players.get(self._active_player)
        if current and current.get("status") == "Playing":
            return

        # 3. Any Playing player.
        #    Prefer non-plasma-browser-integration first for position
        #    stability; keep plasma integration as fallback.
        preferred = None
        plasma_fallback = None
        for name, p in self._players.items():
            if p.get("status") != "Playing":
                continue
            if "plasma-browser-integration" in name:
                if plasma_fallback is None:
                    plasma_fallback = name
                continue
            if preferred is None:
                preferred = name
        if preferred:
            self._set_active(preferred)
            return
        if plasma_fallback:
            self._set_active(plasma_fallback)
            return

        # 4. Keep current if metadata exists
        if self._active_player and self._active_player in self._players:
            return

        # 5. First available
        if self._players:
            first = next(iter(self._players))
            self._set_active(first)
        else:
            self._active_player = ""
            self.active_player_changed.emit("")

    def _set_active(self, bus_name: str):
        if bus_name != self._active_player:
            self._active_player = bus_name
            self.active_player_changed.emit(bus_name)
            self._position_time = 0  # reset; will sync on next Playing/Position event

    def pin_player(self, bus_name: str):
        self._pinned_player = bus_name
        self._select_active()

    def unpin_player(self):
        self._pinned_player = ""
        self._select_active()

    # ------------------------------------------------------------------
    #  Public query methods
    # ------------------------------------------------------------------

    def get_players(self) -> list[str]:
        return list(self._players.keys())

    def get_active_player(self) -> str:
        return self._active_player

    def get_player_status(self, bus_name: str) -> str:
        p = self._players.get(bus_name)
        return p["status"] if p else "Unknown"

    def get_current_metadata(self) -> dict | None:
        p = self._players.get(self._active_player)
        if not p:
            return None
        meta = p.get("metadata")
        if not meta:
            return None
        if meta.get("artist") and meta.get("album") and meta.get("title"):
            return meta
        return self._enrich_metadata(self._active_player, meta)
