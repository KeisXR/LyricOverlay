"""LyricsManager adapter that exposes typed delivery and error states."""

from __future__ import annotations

from PySide6.QtCore import Signal

from lyrics.manager import LyricsManager, LyricsResult


class DiagnosticLyricsManager(LyricsManager):
    """Preserve the existing API while exposing diagnostics to the UI."""

    lyrics_error = Signal(str, str, str, str)  # artist, title, code, detail

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivery_status = "network"

    @staticmethod
    def _classify_error(message: str) -> str:
        normalized = (message or "").casefold()
        if "429" in normalized or "rate limit" in normalized or "too many requests" in normalized:
            return "rate_limited"
        if "timeout" in normalized or "timed out" in normalized:
            return "network_timeout"
        if any(
            token in normalized
            for token in (
                "connect",
                "network",
                "dns",
                "name resolution",
                "connection reset",
                "unreachable",
            )
        ):
            return "network_error"
        return "provider_error"

    def fetch_lyrics(
        self,
        artist: str,
        title: str,
        album: str = "",
        trackid: str = "",
        duration_ms: int = 0,
        force_refresh: bool = False,
    ):
        if title:
            key = self._make_key(artist, title, trackid)
            cached, cached_alternatives = self._get_cached(key)
        else:
            cached, cached_alternatives = None, []

        if not self._lrclib_enabled and not self._syncedlyrics_enabled:
            if cached is not None:
                self.delivery_status = "cache_hit"
                self._cached_alternatives = cached_alternatives
                self.lyrics_ready.emit(
                    LyricsResult(primary=cached, alternatives=cached_alternatives)
                )
            else:
                self.delivery_status = "sources_disabled"
                self.lyrics_error.emit(
                    artist, title, "sources_disabled", "all lyrics sources are disabled"
                )
            return

        self.delivery_status = "cache_hit" if cached is not None else "network"
        return super().fetch_lyrics(
            artist, title, album, trackid, duration_ms, force_refresh
        )

    def _on_fetch_done(
        self,
        result,
        artist,
        title,
        key,
        req_id,
        cached_result=None,
        cached_alts=None,
        force_refresh: bool = False,
    ):
        if req_id == self._req_id:
            self.delivery_status = "network"
        return super()._on_fetch_done(
            result, artist, title, key, req_id,
            cached_result, cached_alts, force_refresh,
        )

    def _on_fetch_error(
        self,
        artist,
        title,
        key,
        req_id,
        message: str = "",
        cached_result=None,
        cached_alts=None,
        force_refresh: bool = False,
    ):
        if req_id != self._req_id:
            return
        if force_refresh and cached_result is not None:
            # A failed reload still has usable lyrics: let the base class serve
            # the cached ones instead of reporting an error over them.
            self.delivery_status = "cache_hit"
            return super()._on_fetch_error(
                artist, title, key, req_id, message,
                cached_result, cached_alts, force_refresh,
            )
        self._cached_alternatives = []
        code = self._classify_error(message)
        self.delivery_status = code
        self.lyrics_error.emit(artist, title, code, message)
