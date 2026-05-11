"""
Lyricaod — Desktop Lyrics Overlay.

Displays synced / unsynced lyrics as a transparent overlay on top of
all other windows, sourced from LRClib (and optionally Musixmatch).

Supported platforms: Linux (MPRIS/D-Bus), Windows (SMTC/WinRT).

Entry point: ``python -m src.main`` or ``python src/main.py``
"""

import sys
import signal
import argparse

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from player import MprisListener, BrowserWsListener
from lyrics.manager import LyricsManager, LyricsResult
from config.settings import Settings
from ui.overlay import OverlayWindow
from ui.tray import TrayIcon
from meta_utils import normalise_yt_meta


class UnifiedPlayerListener(QObject):
    """Aggregates MprisListener and BrowserWsListener into a single
    interface compatible with the rest of the application.

    Signals from both backends are merged and filtered so that only the
    currently-active player drives the UI.  Tray menus and the overlay
    see a single unified player list.
    """

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)
    players_changed = Signal(list)
    active_player_changed = Signal(str)

    def __init__(self, mpris, browser_ws=None, parent=None):
        super().__init__(parent)
        self._mpris = mpris
        self._browser_ws = browser_ws
        self._pinned_player = ""
        self._active_player = ""

        self._mpris.metadata_changed.connect(self._filter_metadata)
        self._mpris.position_changed.connect(self._filter_position)
        self._mpris.playback_state_changed.connect(self._filter_state)
        self._mpris.players_changed.connect(self._update_players)
        self._mpris.active_player_changed.connect(self._set_active)

        if self._browser_ws:
            self._browser_ws.metadata_changed.connect(self._filter_metadata)
            self._browser_ws.position_changed.connect(self._filter_position)
            self._browser_ws.playback_state_changed.connect(self._filter_state)
            self._browser_ws.players_changed.connect(self._update_players)
            self._browser_ws.active_player_changed.connect(self._set_active)

        self._update_players()

    # ------------------------------------------------------------------
    #  Internal signal filtering
    # ------------------------------------------------------------------

    def _set_active(self, player_id: str):
        self._active_player = player_id
        self.active_player_changed.emit(player_id)

    def _update_players(self):
        players = self.get_players()
        self.players_changed.emit(players)

    def _filter_metadata(self, meta: dict):
        pn = meta.get("player_name", "")
        if self._active_player and pn != self._active_player:
            return
        self.metadata_changed.emit(meta)

    def _filter_position(self, pos: int):
        sender = self.sender()
        if sender is self._mpris and self._active_player == "browser-ws":
            return
        if sender is self._browser_ws and self._active_player != "browser-ws":
            return
        self.position_changed.emit(pos)

    def _filter_state(self, status: str, player_id: str):
        if self._active_player and player_id != self._active_player:
            return
        self.playback_state_changed.emit(status, player_id)

    # ------------------------------------------------------------------
    #  Public API (compatible with MprisListener / SmtcListener)
    # ------------------------------------------------------------------

    def pin_player(self, player_id: str):
        self._pinned_player = player_id
        self._mpris.pin_player(player_id)
        if self._browser_ws:
            self._browser_ws.pin_player(player_id)

    def unpin_player(self):
        self._pinned_player = ""
        self._mpris.unpin_player()
        if self._browser_ws:
            self._browser_ws.unpin_player()

    def set_fallback(self, enabled: bool):
        """Delegate to the underlying SMTC listener (Windows only)."""
        if hasattr(self._mpris, "set_fallback"):
            self._mpris.set_fallback(enabled)

    def get_players(self) -> list[str]:
        players = list(self._mpris.get_players())
        if self._browser_ws:
            players.extend(self._browser_ws.get_players())
        return players

    def get_active_player(self) -> str:
        return self._active_player

    def get_player_status(self, player_id: str) -> str:
        if self._browser_ws and player_id == "browser-ws":
            return self._browser_ws.get_player_status(player_id)
        return self._mpris.get_player_status(player_id)

    def get_current_metadata(self) -> dict | None:
        if self._browser_ws and self._active_player == "browser-ws":
            return self._browser_ws.get_current_metadata()
        return self._mpris.get_current_metadata()


class Application:
    """Orchestrates MPRIS listener, lyrics fetching, UI overlay, and tray."""

    def __init__(self):
        self._qapp = QApplication(sys.argv)
        self._qapp.setQuitOnLastWindowClosed(False)
        self._qapp.setApplicationName("Lyricaod")
        self._qapp.setOrganizationName("lyricaod")
        self._qapp.setDesktopFileName("lyricaod")

        # Core services (order matters: wire signals before MPRIS scan)
        self.settings = Settings()
        self.lyrics = LyricsManager(
            ttl_days=self.settings.get("cache.ttl_days", 30),
            max_entries=self.settings.get("cache.max_entries", 10000),
            lrclib_enabled=self.settings.get("sources.lrclib.enabled", True),
        )

        self.lyrics.lyrics_ready.connect(self._on_lyrics_ready)
        self.lyrics.lyrics_not_found.connect(self._on_lyrics_not_found)
        self.lyrics.alternatives_ready.connect(self._on_alternatives_ready)
        self.settings.changed.connect(self._on_settings_changed)
        self._current_meta: dict = {}

        # MPRIS / Browser WebSocket listeners
        if sys.platform == "win32":
            smtc = MprisListener(
                fallback=self.settings.get("behavior.smtc_position_fallback", True)
            )
            browser = BrowserWsListener(
                port=self.settings.get("behavior.ws_port", 56789)
            )
            self.mpris = UnifiedPlayerListener(smtc, browser, None)
        else:
            self.mpris = UnifiedPlayerListener(MprisListener(), None, None)
        pinned_player = self.settings.get("behavior.pinned_player")
        if pinned_player:
            self.mpris.pin_player(pinned_player)
        self.mpris.metadata_changed.connect(self._on_metadata_changed)
        self.mpris.position_changed.connect(self._on_position_changed)
        self.mpris.active_player_changed.connect(self._on_active_player_changed)

        # UI (tray connects to mpris signals internally)
        self.overlay = OverlayWindow(self.settings)
        self.overlay.alternative_selected.connect(self.on_select_alternative)
        self.overlay.alternatives_requested.connect(self._on_alternatives_requested)
        self.overlay.closed.connect(self._on_overlay_closed)
        self.overlay.resync_requested.connect(self._on_resync_requested)
        self.tray = TrayIcon.create(self)

        # Show overlay (unless start_minimized)
        if not self.settings.get("behavior.start_minimized", False):
            self.overlay.set_always_on_top(
                self.settings.get("behavior.always_on_top", True)
            )
            self.overlay.show()
            # Defer positioning: Wayland may ignore geometry set before show
            QTimer.singleShot(0, self._position_overlay)
            # Then restore user-saved position (overrides anchor if enabled)
            QTimer.singleShot(10, self.overlay.restore_position)

    # ------------------------------------------------------------------
    #  Signal handlers
    # ------------------------------------------------------------------

    def _on_metadata_changed(self, metadata: dict):
        artist = metadata.get("artist", "")
        title = metadata.get("title", "")
        album = metadata.get("album", "")
        trackid = metadata.get("trackid", "")
        length_ms = metadata.get("length_ms", 0)

        # Normalise YouTube Music metadata noise before searching.
        artist, title = normalise_yt_meta(artist, title)

        # Store normalised values so fetch_alternatives uses them too.
        self._current_meta = {**metadata, "artist": artist, "title": title}

        print(f"[Main] meta → artist=\"{artist}\" title=\"{title}\" album=\"{album}\"")
        self.overlay.set_seek(0, length_ms)
        if title:
            self.overlay.set_loading(True)
            try:
                self.lyrics.fetch_lyrics(artist, title, album, trackid, length_ms)
            except Exception as exc:
                print(f"[Main] fetch_lyrics error: {exc}")
                self.overlay.set_loading(False)

    def _on_position_changed(self, position_ms: int):
        self.overlay.set_position(position_ms)
        self.overlay.set_seek(position_ms, self.overlay._seek_length)

    def _on_active_player_changed(self, bus_name: str):
        meta = self.mpris.get_current_metadata()
        if meta:
            self._on_metadata_changed(meta)
        else:
            self.overlay.set_plain_text("Waiting for media…")

    def _on_lyrics_ready(self, result: LyricsResult):
        lyrics = result.primary
        self.overlay.set_loading(False)
        if lyrics.synced and lyrics.lrc:
            self.overlay.set_lyrics_data(lyrics.lrc, synced=True)
        elif lyrics.plain_text:
            self.overlay.set_plain_text(lyrics.plain_text)
        else:
            self.overlay.set_plain_text("(instrumental)")
        self.overlay.set_alternatives(result.alternatives)

    def _on_lyrics_not_found(self, artist, title):
        self.overlay.set_loading(False)
        self.overlay.set_alternatives([])
        if title:
            self.overlay.set_plain_text(f"No lyrics found for {title}")
        else:
            self.overlay.set_plain_text("Waiting for media…")

    def _on_alternatives_requested(self):
        """User clicked the alternatives button — fetch alternatives on demand."""
        meta = self._current_meta
        if not meta:
            return
        artist = meta.get("artist", "")
        title = meta.get("title", "")
        if not title:
            return
        print(f"[Main] fetching alternatives for \"{title}\" by \"{artist}\"")
        self.overlay.set_alternatives_loading(True)
        self.lyrics.fetch_alternatives(
            artist,
            title,
            meta.get("album", ""),
            meta.get("length_ms", 0),
        )

    def _on_alternatives_ready(self, alternatives: list):
        self.overlay.set_alternatives_loading(False)
        self.overlay.set_alternatives(alternatives)
        if alternatives:
            self.overlay._show_alternatives_menu()

    def _on_settings_changed(self):
        self.lyrics.set_lrclib_enabled(
            self.settings.get("sources.lrclib.enabled", True)
        )
        self.lyrics.set_cache_limits(
            self.settings.get("cache.ttl_days", 30),
            self.settings.get("cache.max_entries", 10000),
        )
        self.overlay.set_hide_delay(self.settings.get("behavior.hide_delay_ms", 2000))
        if sys.platform == "win32":
            self.mpris.set_fallback(
                self.settings.get("behavior.smtc_position_fallback", True)
            )
        # Only reposition if the user hasn't manually placed the window
        if not self.settings.get("behavior.remember_position", True):
            self._position_overlay()
        self.overlay._shrink_to_content()
        self.overlay.update()

    def on_reload(self, metadata: dict):
        """Public entry point for tray 'Reload Lyrics' action."""
        self._on_metadata_changed(metadata)

    def _on_overlay_closed(self):
        """Hide the overlay and sync the tray checkbox."""
        self.overlay.hide()
        if self.tray:
            self.tray.set_show_checked(False)

    def _on_resync_requested(self):
        """Reload lyrics for the current track (triggered by overlay resync button)."""
        self.overlay.set_loading(True)
        meta = self.mpris.get_current_metadata()
        if meta:
            self._on_metadata_changed(meta)
        else:
            self.overlay.set_loading(False)

    def on_select_alternative(self, index: int):
        """Called when the user picks an alternative lyrics result in the overlay."""
        data = self.lyrics.select_alternative(index)
        if data and data.synced and data.lrc:
            self.overlay.set_lyrics_data(data.lrc, synced=True)
            # Refresh alternatives list (old primary is now in the alternatives)
            self.overlay.set_alternatives(self.lyrics._cached_alternatives)
        elif data and data.plain_text:
            self.overlay.set_plain_text(data.plain_text)
            self.overlay.set_alternatives(self.lyrics._cached_alternatives)

    # ------------------------------------------------------------------
    #  Window positioning
    # ------------------------------------------------------------------

    def _position_overlay(self):
        screens = self._qapp.screens()
        idx = min(self.settings.get("window.screen_index", 0), len(screens) - 1)
        screen = screens[idx]
        geo = screen.availableGeometry()

        width = int(geo.width() * self.settings.get("window.width_pct", 60) / 100)
        height = self.settings.get("window.height_px", 300)
        anchor = self.settings.get("window.anchor", "bottom-center")
        ox = self.settings.get("window.offset_x", 0)
        oy = self.settings.get("window.offset_y", -100)

        # X coordinate
        if "left" in anchor:
            x = geo.x() + ox
        elif "right" in anchor:
            x = geo.x() + geo.width() - width + ox
        else:  # center variants
            x = geo.x() + (geo.width() - width) // 2 + ox

        # Y coordinate
        if "top" in anchor:
            y = geo.y() + oy
        elif "bottom" in anchor:
            y = geo.y() + geo.height() - height + oy
        else:  # center
            y = geo.y() + (geo.height() - height) // 2 + oy

        self.overlay.setGeometry(x, y, width, height)

    # ------------------------------------------------------------------

    def run(self) -> int:
        return self._qapp.exec()


def main():
    parser = argparse.ArgumentParser(description="Lyricaod — Desktop Lyrics Overlay")
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start with the overlay hidden (tray icon only).",
    )
    args = parser.parse_args()

    app = Application()

    # Ctrl+C in the terminal quits cleanly
    signal.signal(signal.SIGINT, lambda sig, frame: app._qapp.quit())

    if args.minimized:
        app.overlay.hide()
        if app.tray:
            app.tray.set_show_checked(False)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
