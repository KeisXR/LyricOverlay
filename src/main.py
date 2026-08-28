"""Lyricaod desktop lyrics overlay entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

if "--self-test" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication

from diagnostics import collect_diagnostics, format_diagnostics, run_self_test
from logging_setup import LoggingState, configure_logging, get_logger
from runtime_status import RuntimeState, RuntimeStatus, state


logger = get_logger(__name__)


class UnifiedPlayerListener(QObject):
    """Merge native and browser player backends behind one signal contract."""

    metadata_changed = Signal(dict)
    position_changed = Signal(int)
    playback_state_changed = Signal(str, str)
    players_changed = Signal(list)
    active_player_changed = Signal(str)

    def __init__(self, native, browser_ws=None, parent=None):
        super().__init__(parent)
        self._mpris = native
        self._browser_ws = browser_ws
        self._pinned_player = ""
        self._active_player = ""
        self._mpris_active = ""
        self._browser_active = ""
        self._browser_media_active = False

        self._mpris.metadata_changed.connect(self._filter_metadata)
        self._mpris.position_changed.connect(self._filter_position)
        self._mpris.playback_state_changed.connect(self._filter_state)
        self._mpris.players_changed.connect(self._update_players)
        self._mpris.active_player_changed.connect(
            lambda player_id: self._set_backend_active("mpris", player_id)
        )

        if self._browser_ws:
            self._browser_ws.metadata_changed.connect(self._filter_metadata)
            self._browser_ws.position_changed.connect(self._filter_position)
            self._browser_ws.playback_state_changed.connect(self._filter_state)
            self._browser_ws.players_changed.connect(self._update_players)
            self._browser_ws.active_player_changed.connect(
                lambda player_id: self._set_backend_active("browser", player_id)
            )
            if hasattr(self._browser_ws, "media_active_changed"):
                self._browser_ws.media_active_changed.connect(
                    self._set_browser_media_active
                )

        self._update_players()

    def _set_backend_active(self, backend: str, player_id: str):
        if backend == "browser":
            self._browser_active = player_id
        else:
            self._mpris_active = player_id
        self._choose_active_player()

    def _update_players(self, *_args):
        players = self.get_players()
        self.players_changed.emit(players)
        self._choose_active_player()

    def _set_browser_media_active(self, active: bool):
        self._browser_media_active = bool(active)
        if not self._browser_media_active:
            self._browser_active = ""
        self._choose_active_player()

    def _choose_active_player(self):
        players = self.get_players()
        if self._pinned_player and self._pinned_player in players:
            active = self._pinned_player
        elif self._browser_media_active and self._browser_active:
            active = self._browser_active
        elif self._mpris_active:
            active = self._mpris_active
        else:
            active = ""
        if active != self._active_player:
            logger.debug("Active player changed: %s -> %s", self._active_player, active)
            self._active_player = active
            self.active_player_changed.emit(active)

    def _filter_metadata(self, metadata: dict):
        player_name = metadata.get("player_name", "")
        if self._active_player and player_name != self._active_player:
            return
        self.metadata_changed.emit(metadata)

    def _filter_position(self, position_ms: int):
        sender = self.sender()
        if sender is self._mpris and self._active_player == "browser-ws":
            return
        if sender is self._browser_ws and self._active_player != "browser-ws":
            return
        self.position_changed.emit(position_ms)

    def _filter_state(self, status: str, player_id: str):
        if self._active_player and player_id != self._active_player:
            return
        self.playback_state_changed.emit(status, player_id)

    def pin_player(self, player_id: str):
        self._pinned_player = player_id
        self._mpris.pin_player(player_id)
        if self._browser_ws:
            self._browser_ws.pin_player(player_id)
        self._choose_active_player()

    def unpin_player(self):
        self._pinned_player = ""
        self._mpris.unpin_player()
        if self._browser_ws:
            self._browser_ws.unpin_player()
        self._choose_active_player()

    def set_fallback(self, enabled: bool):
        if hasattr(self._mpris, "set_fallback"):
            self._mpris.set_fallback(enabled)

    def get_players(self) -> list[str]:
        players = list(self._mpris.get_players())
        if self._browser_ws:
            for player in self._browser_ws.get_players():
                if player not in players:
                    players.append(player)
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

    def is_browser_connected(self) -> bool:
        return bool(self._browser_ws and self._browser_ws.is_connected())

    def get_browser_port(self) -> int | None:
        return self._browser_ws.get_port() if self._browser_ws else None

    def stop(self):
        if hasattr(self._mpris, "stop"):
            self._mpris.stop()
        if self._browser_ws and hasattr(self._browser_ws, "stop"):
            self._browser_ws.stop()


class Application:
    """Own all long-lived services and translate backend states for the UI."""

    def __init__(self, logging_state: LoggingState):
        from config.settings import Settings
        from diagnostic_lyrics_manager import DiagnosticLyricsManager
        from meta_utils import normalise_yt_meta
        from player import BrowserWsListener, MprisListener
        from ui.overlay import OverlayWindow
        from ui.tray import TrayIcon

        self._normalise_meta = normalise_yt_meta
        self._logging_state = logging_state
        self._shutting_down = False
        self.runtime_state = state(RuntimeStatus.STARTING)

        self._qapp = QApplication(sys.argv)
        self._qapp.setQuitOnLastWindowClosed(False)
        self._qapp.setApplicationName("Lyricaod")
        self._qapp.setOrganizationName("lyricaod")
        self._qapp.setDesktopFileName("lyricaod")

        self.settings = Settings()
        self.lyrics = DiagnosticLyricsManager(
            ttl_days=self.settings.get("cache.ttl_days", 30),
            max_entries=self.settings.get("cache.max_entries", 10000),
            lrclib_enabled=self.settings.get("sources.lrclib.enabled", True),
            syncedlyrics_enabled=self.settings.get(
                "sources.syncedlyrics.enabled", True
            ),
            syncedlyrics_enhanced=self.settings.get(
                "sources.syncedlyrics.enhanced", True
            ),
        )
        self.lyrics.lyrics_ready.connect(self._on_lyrics_ready)
        self.lyrics.lyrics_not_found.connect(self._on_lyrics_not_found)
        self.lyrics.lyrics_error.connect(self._on_lyrics_error)
        self.lyrics.alternatives_ready.connect(self._on_alternatives_ready)
        self.settings.changed.connect(self._on_settings_changed)
        self._current_meta: dict = {}

        if sys.platform == "win32":
            native_listener = MprisListener(
                fallback=self.settings.get(
                    "behavior.smtc_position_fallback", True
                )
            )
        else:
            native_listener = MprisListener()

        self._browser = BrowserWsListener(
            port=self.settings.get("behavior.ws_port", 56789)
        )
        self.mpris = UnifiedPlayerListener(native_listener, self._browser, None)
        pinned_player = self.settings.get("behavior.pinned_player")
        if pinned_player:
            self.mpris.pin_player(pinned_player)
        self.mpris.metadata_changed.connect(self._on_metadata_changed)
        self.mpris.position_changed.connect(self._on_position_changed)
        self.mpris.active_player_changed.connect(self._on_active_player_changed)

        self.overlay = OverlayWindow(self.settings)
        self.overlay.alternative_selected.connect(self.on_select_alternative)
        self.overlay.alternatives_requested.connect(
            self._on_alternatives_requested
        )
        self.overlay.closed.connect(self._on_overlay_closed)
        self.overlay.resync_requested.connect(self._on_resync_requested)
        self.tray = TrayIcon.create(self)
        self._install_diagnostic_actions()

        # Shutdown wiring. _shutdown drives the whole teardown in order with
        # per-step error isolation and logging, so a failure in one step cannot
        # stop the others. The explicit connections after it are the contract
        # this file must keep: settings are written debounced, so losing
        # settings.flush silently discards the last SAVE_DEBOUNCE_MS of changes
        # on exit. Every one of these calls is idempotent, so the belt-and-
        # braces wiring costs nothing at runtime.
        self._qapp.aboutToQuit.connect(self._shutdown)
        self._qapp.aboutToQuit.connect(self.lyrics.shutdown)
        self._qapp.aboutToQuit.connect(self.mpris.stop)
        self._qapp.aboutToQuit.connect(self.settings.flush)
        QTimer.singleShot(1000, self._check_browser_bridge)

        if not self.settings.get("behavior.start_minimized", False):
            self.overlay.set_always_on_top(
                self.settings.get("behavior.always_on_top", True)
            )
            self.overlay.show()
            QTimer.singleShot(0, self._position_overlay)
            QTimer.singleShot(10, self.overlay.restore_position)
        self._set_runtime_state(state(RuntimeStatus.WAITING_FOR_PLAYER))

    def _install_diagnostic_actions(self):
        menu = getattr(self.tray, "_menu", None) if self.tray else None
        if menu is None:
            return
        menu.addSeparator()
        open_logs = QAction("ログフォルダを開く", self.tray)
        open_logs.triggered.connect(self.open_log_directory)
        menu.addAction(open_logs)
        copy_diagnostics = QAction("診断情報をコピー", self.tray)
        copy_diagnostics.triggered.connect(self.copy_diagnostics)
        menu.addAction(copy_diagnostics)

    def open_log_directory(self):
        directory = self._logging_state.log_dir
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def diagnostic_text(self) -> str:
        return format_diagnostics(
            collect_diagnostics(
                settings=self.settings,
                logging_state=self._logging_state,
            )
        )

    def copy_diagnostics(self):
        self._qapp.clipboard().setText(self.diagnostic_text())
        logger.info("Diagnostic information copied to clipboard")

    def _set_runtime_state(self, runtime_state: RuntimeState):
        if runtime_state == self.runtime_state:
            return
        self.runtime_state = runtime_state
        tooltip = f"Lyricaod — {runtime_state.message}"
        self.overlay.setToolTip(tooltip)
        if self.tray and hasattr(self.tray, "setToolTip"):
            self.tray.setToolTip(tooltip)
        if runtime_state.status in {
            RuntimeStatus.NETWORK_TIMEOUT,
            RuntimeStatus.NETWORK_ERROR,
            RuntimeStatus.RATE_LIMITED,
            RuntimeStatus.PROVIDER_ERROR,
            RuntimeStatus.BROWSER_BRIDGE_ERROR,
        }:
            logger.warning(
                "Runtime state=%s detail=%s",
                runtime_state.status.value,
                runtime_state.detail,
            )
        else:
            logger.debug("Runtime state=%s", runtime_state.status.value)

    def _check_browser_bridge(self):
        thread = getattr(self._browser, "_ws_thread", None)
        if thread is not None and not thread.isRunning():
            self._on_browser_bridge_error(
                f"WebSocket listener stopped on port {self._browser.get_port()}"
            )

    def _on_browser_bridge_error(self, detail: str):
        self._set_runtime_state(
            state(RuntimeStatus.BROWSER_BRIDGE_ERROR, detail=detail)
        )

    def _on_metadata_changed(self, metadata: dict, force_refresh: bool = False):
        artist = metadata.get("artist", "")
        title = metadata.get("title", "")
        album = metadata.get("album", "")
        trackid = metadata.get("trackid", "")
        length_ms = metadata.get("length_ms", 0)
        artist, title = self._normalise_meta(artist, title)
        self._current_meta = {**metadata, "artist": artist, "title": title}

        logger.debug(
            "Metadata received player=%s artist=%r title=%r album=%r",
            metadata.get("player_name", ""),
            artist,
            title,
            album,
        )
        self.overlay.set_seek(0, length_ms)
        if not title:
            self._set_runtime_state(state(RuntimeStatus.WAITING_FOR_PLAYER))
            return

        self.overlay.set_loading(True)
        # fetch_lyrics starts a new request generation, so any alternatives
        # fetch still in flight is discarded (it may even be cancelled before
        # it emits): clear its loading state here so the alternatives button
        # never stays stuck on "…".
        self.overlay.set_alternatives_loading(False)
        self._set_runtime_state(state(RuntimeStatus.LOADING_LYRICS))
        try:
            self.lyrics.fetch_lyrics(
                artist,
                title,
                album,
                trackid,
                length_ms,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            logger.exception("Unable to start lyrics fetch")
            self.overlay.set_loading(False)
            self._on_lyrics_error(
                artist, title, "provider_error", str(exc)
            )

    def _on_position_changed(self, position_ms: int):
        self.overlay.set_position(position_ms)
        self.overlay.set_seek(position_ms, self.overlay._seek_length)

    def _on_active_player_changed(self, _player_id: str):
        metadata = self.mpris.get_current_metadata()
        if metadata:
            self._on_metadata_changed(metadata)
        else:
            self.overlay.set_plain_text("メディアの再生待ち...")
            self._set_runtime_state(state(RuntimeStatus.WAITING_FOR_PLAYER))

    def _on_lyrics_ready(self, result):
        lyrics = result.primary
        self.overlay.set_loading(False)
        if lyrics.synced and lyrics.lrc:
            self.overlay.set_lyrics_data(lyrics.lrc, synced=True)
        elif lyrics.plain_text:
            self.overlay.set_plain_text(lyrics.plain_text)
        else:
            self.overlay.set_plain_text("(インストゥルメンタル)")
        self.overlay.set_alternatives(result.alternatives)

        delivery = getattr(self.lyrics, "delivery_status", "network")
        if delivery == "cache_hit":
            self._set_runtime_state(state(RuntimeStatus.CACHE_HIT))
        elif delivery == "stale_cache":
            self._set_runtime_state(state(RuntimeStatus.STALE_CACHE))
        else:
            self._set_runtime_state(state(RuntimeStatus.READY))

    def _on_lyrics_not_found(self, _artist: str, title: str):
        self.overlay.set_loading(False)
        self.overlay.set_alternatives([])
        if title:
            self.overlay.set_plain_text(f"{title} の歌詞が見つかりません")
            self._set_runtime_state(state(RuntimeStatus.NO_LYRICS))
        else:
            self.overlay.set_plain_text("メディアの再生待ち...")
            self._set_runtime_state(state(RuntimeStatus.WAITING_FOR_PLAYER))

    def _on_lyrics_error(
        self, _artist: str, title: str, code: str, detail: str
    ):
        mapping = {
            "sources_disabled": RuntimeStatus.SOURCES_DISABLED,
            "network_timeout": RuntimeStatus.NETWORK_TIMEOUT,
            "network_error": RuntimeStatus.NETWORK_ERROR,
            "rate_limited": RuntimeStatus.RATE_LIMITED,
            "provider_error": RuntimeStatus.PROVIDER_ERROR,
        }
        status = mapping.get(code, RuntimeStatus.PROVIDER_ERROR)
        runtime_state = state(status, detail=detail)
        self.overlay.set_loading(False)
        self.overlay.set_alternatives([])
        prefix = f"{title}: " if title else ""
        self.overlay.set_plain_text(prefix + runtime_state.message)
        self._set_runtime_state(runtime_state)

    def _on_alternatives_requested(self):
        metadata = self._current_meta
        if not metadata or not metadata.get("title"):
            return
        logger.debug("Alternative lyrics requested")
        self.overlay.set_alternatives_loading(True)
        self.lyrics.fetch_alternatives(
            metadata.get("artist", ""),
            metadata.get("title", ""),
            metadata.get("album", ""),
            metadata.get("length_ms", 0),
        )

    def _on_alternatives_ready(self, alternatives: list):
        self.overlay.set_alternatives_loading(False)
        if not alternatives:
            # No matches, or a discarded stale fetch: keep whatever the
            # current track already has instead of wiping it.
            return
        self.overlay.set_alternatives(alternatives)
        self.overlay._show_alternatives_menu()

    def _on_settings_changed(self):
        self.lyrics.set_lrclib_enabled(
            self.settings.get("sources.lrclib.enabled", True)
        )
        self.lyrics.set_syncedlyrics_enabled(
            self.settings.get("sources.syncedlyrics.enabled", True)
        )
        self.lyrics.set_syncedlyrics_enhanced(
            self.settings.get("sources.syncedlyrics.enhanced", True)
        )
        self.lyrics.set_cache_limits(
            self.settings.get("cache.ttl_days", 30),
            self.settings.get("cache.max_entries", 10000),
        )
        self.overlay.set_hide_delay(
            self.settings.get("behavior.hide_delay_ms", 2000)
        )
        if sys.platform == "win32":
            self.mpris.set_fallback(
                self.settings.get("behavior.smtc_position_fallback", True)
            )
        if not self.settings.get("behavior.remember_position", True):
            self._position_overlay()
        self.overlay._shrink_to_content()
        self.overlay.update()

    def on_reload(self, metadata: dict):
        """Public entry point for the tray / settings 'Reload Lyrics' action."""
        self._on_metadata_changed(metadata, force_refresh=True)

    def _on_overlay_closed(self):
        self.overlay.hide()
        if self.tray:
            self.tray.set_show_checked(False)

    def _on_resync_requested(self):
        """Reload lyrics for the current track (overlay resync button)."""
        self.overlay.set_loading(True)
        metadata = self.mpris.get_current_metadata()
        if metadata:
            self._on_metadata_changed(metadata, force_refresh=True)
        else:
            self.overlay.set_loading(False)

    def on_select_alternative(self, index: int):
        data = self.lyrics.select_alternative(index)
        if data and data.synced and data.lrc:
            self.overlay.set_lyrics_data(data.lrc, synced=True)
            self.overlay.set_alternatives(self.lyrics._cached_alternatives)
        elif data and data.plain_text:
            self.overlay.set_plain_text(data.plain_text)
            self.overlay.set_alternatives(self.lyrics._cached_alternatives)

    def _position_overlay(self):
        screens = self._qapp.screens()
        if not screens:
            return
        requested = int(self.settings.get("window.screen_index", 0))
        index = max(0, min(requested, len(screens) - 1))
        geometry = screens[index].availableGeometry()
        width = int(
            geometry.width()
            * self.settings.get("window.width_pct", 60)
            / 100
        )
        height = self.settings.get("window.height_px", 300)
        anchor = self.settings.get("window.anchor", "bottom-center")
        offset_x = self.settings.get("window.offset_x", 0)
        offset_y = self.settings.get("window.offset_y", -100)

        if "left" in anchor:
            x = geometry.x() + offset_x
        elif "right" in anchor:
            x = geometry.x() + geometry.width() - width + offset_x
        else:
            x = geometry.x() + (geometry.width() - width) // 2 + offset_x

        if "top" in anchor:
            y = geometry.y() + offset_y
        elif "bottom" in anchor:
            y = geometry.y() + geometry.height() - height + offset_y
        else:
            y = geometry.y() + (geometry.height() - height) // 2 + offset_y
        self.overlay.setGeometry(x, y, width, height)

    def _shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Application shutdown started")
        try:
            if hasattr(self.lyrics, "shutdown"):
                self.lyrics.shutdown()
        except Exception:
            logger.exception("Lyrics manager shutdown failed")
        try:
            self.mpris.stop()
        except Exception:
            logger.exception("Player listener shutdown failed")
        try:
            if hasattr(self.settings, "flush"):
                self.settings.flush()
        except Exception:
            logger.exception("Settings flush failed")

    def run(self) -> int:
        return self._qapp.exec()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lyricaod — Desktop Lyrics Overlay"
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="オーバーレイを非表示（トレイのみ）で起動します。",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="診断情報をJSONで表示して終了します。",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="外部サービスを使わず主要依存と保存領域を検査します。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="debug levelのログを有効にします。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    logging_state = configure_logging(
        level=logging.DEBUG if arguments.debug else logging.INFO
    )

    def log_uncaught(exc_type, exc_value, traceback):
        logger.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, traceback),
        )

    sys.excepthook = log_uncaught

    if arguments.diagnose:
        print(
            format_diagnostics(
                collect_diagnostics(logging_state=logging_state)
            )
        )
        return 0

    if arguments.self_test:
        success, failures = run_self_test()
        if success:
            print("Lyricaod self-test: OK")
            return 0
        print("Lyricaod self-test: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    application = Application(logging_state)
    signal.signal(
        signal.SIGINT, lambda _signal, _frame: application._qapp.quit()
    )
    if arguments.minimized:
        application.overlay.hide()
        if application.tray:
            application.tray.set_show_checked(False)
    return application.run()


if __name__ == "__main__":
    raise SystemExit(main())
