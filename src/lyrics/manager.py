"""Lyrics fetching orchestrator with SQLite caching.

Fetches lyrics from configured sources (LRClib first) and caches results.
Also returns alternative matches so the user can switch if the primary
result is wrong.
"""

import hashlib
import json
import os
import sqlite3
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal

from .lrclib import get_lrclib, search_all, LrcLibResult
from .syncedlyrics_client import get_syncedlyrics, SyncedLyricsResult
from .lrc_parser import ParsedLRC, parse_lrc


def _get_cache_dir() -> Path:
    """Return the platform-appropriate cache directory for lyricaod.

    - Windows:     ``%LOCALAPPDATA%\\lyricaod``
    - Linux/macOS: ``~/.cache/lyricaod``
    """
    if sys.platform == "win32":
        base = Path(
            os.environ.get(
                "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
            )
        )
    else:
        base = Path.home() / ".cache"
    return base / "lyricaod"


CACHE_DIR = _get_cache_dir()
CACHE_FORMAT_VERSION = 2


@dataclass
class LyricsData:
    artist: str
    title: str
    synced: bool
    lrc: ParsedLRC | None
    plain_text: str | None
    source: str


@dataclass
class LyricsResult:
    """Primary result + alternatives for the same search."""
    primary: LyricsData
    alternatives: list[LyricsData] = field(default_factory=list)


class _FetchPrimaryThread(QThread):
    """Fetches a single best-match lyrics result from enabled providers."""

    finished_ok = Signal(object)  # tuple[str, object] | None
    finished_error = Signal(str)

    def __init__(
        self,
        artist: str,
        title: str,
        album: str,
        duration_ms: int,
        use_lrclib: bool,
        use_syncedlyrics: bool,
        syncedlyrics_enhanced: bool,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._artist = artist
        self._title = title
        self._album = album
        self._duration_ms = duration_ms
        self._use_lrclib = use_lrclib
        self._use_syncedlyrics = use_syncedlyrics
        self._syncedlyrics_enhanced = syncedlyrics_enhanced
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        if self._cancel:
            return
        try:
            if self._use_syncedlyrics:
                try:
                    synced = get_syncedlyrics(
                        self._artist,
                        self._title,
                        enhanced=self._syncedlyrics_enhanced,
                    )
                except Exception as exc:
                    print(f"[syncedlyrics] failed, falling back to LRClib: {exc}")
                    synced = None
                if self._cancel:
                    return
                if synced:
                    print(f"[syncedlyrics] found: \"{self._title}\" by \"{self._artist}\"")
                    self.finished_ok.emit(("syncedlyrics", synced))
                    return

            if not self._use_lrclib:
                print(f"[Lyrics] LRClib disabled; no results for \"{self._title}\"")
                self.finished_ok.emit(None)
                return

            result = get_lrclib(
                self._artist, self._title, self._album, self._duration_ms
            )
            if self._cancel:
                return

            if result is None and self._artist:
                print(
                    f"[LRClib] direct lookup failed, trying title-only search"
                    f" for \"{self._title}\""
                )
                fallbacks = search_all(
                    "",
                    self._title,
                    self._album,
                    self._duration_ms,
                    max_results=1,
                )
                if self._cancel:
                    return
                result = fallbacks[0] if fallbacks else None

            if result:
                print(f"[LRClib] found: \"{result.track_name}\" by \"{result.artist_name}\"")
                self.finished_ok.emit(("lrclib", result))
            else:
                print(f"[LRClib] no results for \"{self._title}\" by \"{self._artist}\"")
                self.finished_ok.emit(None)
        except Exception as exc:
            if not self._cancel:
                self.finished_error.emit(str(exc))


class _FetchAltThread(QThread):
    """Fetches alternative lyrics matches from LRClib."""

    finished_ok = Signal(object)  # list[LrcLibResult]
    finished_error = Signal(str)

    def __init__(
        self,
        artist: str,
        title: str,
        album: str,
        duration_ms: int,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._artist = artist
        self._title = title
        self._album = album
        self._duration_ms = duration_ms
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        if self._cancel:
            return
        try:
            results = search_all(
                self._artist, self._title, self._album, self._duration_ms
            )
            if self._cancel:
                return
            names = [r.track_name for r in results]
            print(f"[LRClib] alternatives: {len(results)} result(s): {names}")
            self.finished_ok.emit(results)
        except Exception as exc:
            if not self._cancel:
                self.finished_error.emit(str(exc))


class LyricsManager(QObject):
    """High-level API for fetching lyrics with alternative results."""

    lyrics_ready = Signal(LyricsResult)
    lyrics_not_found = Signal(str, str)
    alternatives_ready = Signal(list)  # list[LyricsData]

    def __init__(
        self,
        ttl_days: int = 30,
        max_entries: int = 10000,
        lrclib_enabled: bool = True,
        syncedlyrics_enabled: bool = True,
        syncedlyrics_enhanced: bool = True,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._ttl_days = ttl_days
        self._max_entries = max_entries
        self._lrclib_enabled = lrclib_enabled
        self._syncedlyrics_enabled = syncedlyrics_enabled
        self._syncedlyrics_enhanced = syncedlyrics_enhanced
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(CACHE_DIR / "cache.db"))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()
        self._worker: _FetchPrimaryThread | _FetchAltThread | None = None
        self._live_workers: set[_FetchPrimaryThread | _FetchAltThread] = set()
        self._req_id = 0
        self._last_search_key = ""  # cache key of current search
        self._cached_alternatives: list[LyricsData] = []

    # ------------------------------------------------------------------
    #  Database
    # ------------------------------------------------------------------

    def _init_db(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                fetched_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()
        # TTL cleanup
        cutoff = _time.time() - self._ttl_days * 86400
        self._conn.execute("DELETE FROM cache WHERE fetched_at < ?", (cutoff,))
        self._conn.commit()
        # LRU eviction
        self._enforce_max_entries()

    def _enforce_max_entries(self):
        max_entries = self._max_entries
        count_row = self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()
        count = count_row[0] if count_row else 0
        if count > max_entries:
            to_delete = count - max_entries
            self._conn.execute(
                "DELETE FROM cache WHERE key IN ("
                "  SELECT key FROM cache ORDER BY fetched_at ASC LIMIT ?"
                ")", (to_delete,)
            )
            self._conn.commit()

    def _make_key(self, artist: str, title: str, trackid: str = "") -> str:
        raw = f"{artist}|{title}|{trackid}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _dict_to_lyrics_data(entry: dict) -> LyricsData:
        lrc = None
        if entry.get("lrc_raw"):
            lrc = parse_lrc(entry["lrc_raw"])
        return LyricsData(
            artist=entry["artist"],
            title=entry["title"],
            synced=entry["synced"],
            lrc=lrc,
            plain_text=entry.get("plain_text"),
            source=entry.get("source", "cache"),
        )

    @staticmethod
    def _lyrics_data_to_dict(data: LyricsData) -> dict:
        lrc_raw = LyricsManager._lrc_to_raw(data.lrc) if data.lrc else ""
        return {
            "artist": data.artist,
            "title": data.title,
            "synced": data.synced,
            "plain_text": data.plain_text,
            "lrc_raw": lrc_raw,
            "source": data.source,
        }

    def _get_cached(self, key: str) -> tuple[LyricsData | None, list[LyricsData]]:
        """Return (primary, alternatives) from cache, or (None, [])."""
        row = self._conn.execute(
            "SELECT data FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None, []
        payload = json.loads(row[0])

        if isinstance(payload, dict) and "primary" in payload:
            if payload.get("version") != CACHE_FORMAT_VERSION:
                return None, []
            primary = self._dict_to_lyrics_data(payload["primary"])
            alternatives = [
                self._dict_to_lyrics_data(a)
                for a in payload.get("alternatives", [])
            ]
            return primary, alternatives

        return None, []

    def _put_cache(self, key: str, primary: LyricsData, alternatives: list[LyricsData]):
        payload = {
            "version": CACHE_FORMAT_VERSION,
            "primary": self._lyrics_data_to_dict(primary),
            "alternatives": [self._lyrics_data_to_dict(a) for a in alternatives],
        }
        data = json.dumps(payload, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, data, fetched_at) VALUES (?, ?, ?)",
            (key, data, _time.time()),
        )
        self._conn.commit()
        self._enforce_max_entries()

    def _update_cache_alternatives(self, key: str, alternatives: list[LyricsData]):
        """Update only the alternatives field of an existing cache entry."""
        row = self._conn.execute(
            "SELECT data FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return
        payload = json.loads(row[0])
        if isinstance(payload, dict) and "primary" in payload:
            payload["alternatives"] = [
                self._lyrics_data_to_dict(a) for a in alternatives
            ]
            data = json.dumps(payload, ensure_ascii=False)
            self._conn.execute(
                "UPDATE cache SET data = ?, fetched_at = ? WHERE key = ?",
                (data, _time.time(), key),
            )
            self._conn.commit()

    @staticmethod
    def _format_lrc_timestamp(timestamp_ms: int) -> str:
        timestamp_ms = max(0, timestamp_ms)
        mins = timestamp_ms // 60000
        secs = (timestamp_ms % 60000) / 1000
        return f"{mins:02d}:{secs:05.2f}"

    @staticmethod
    def _lrc_to_raw(lrc: ParsedLRC) -> str:
        parts: list[str] = []
        if lrc.title:
            parts.append(f"[ti:{lrc.title}]")
        if lrc.artist:
            parts.append(f"[ar:{lrc.artist}]")
        if lrc.offset_ms:
            parts.append(f"[offset:{lrc.offset_ms}]")
        for line in lrc.lines:
            timestamp_ms = line.timestamp_ms - lrc.offset_ms
            line_tag = f"[{LyricsManager._format_lrc_timestamp(timestamp_ms)}]"
            if line.words:
                word_parts = []
                for word in line.words:
                    word_ms = word.timestamp_ms - lrc.offset_ms
                    word_tag = f"<{LyricsManager._format_lrc_timestamp(word_ms)}>"
                    word_parts.append(f"{word_tag}{word.text}")
                parts.append(f"{line_tag}{''.join(word_parts).rstrip()}")
            else:
                parts.append(f"{line_tag}{line.text}")
        return "\n".join(parts)

    @staticmethod
    def _convert_lrclib_result(result: LrcLibResult) -> LyricsData:
        if result.synced_lyrics:
            lrc = parse_lrc(result.synced_lyrics)
            return LyricsData(
                artist=result.artist_name,
                title=result.track_name,
                synced=True,
                lrc=lrc,
                plain_text=result.plain_lyrics,
                source="lrclib",
            )
        return LyricsData(
            artist=result.artist_name,
            title=result.track_name,
            synced=False,
            lrc=None,
            plain_text=result.plain_lyrics,
            source="lrclib",
        )


    @staticmethod
    def _convert_syncedlyrics_result(result: SyncedLyricsResult) -> LyricsData:
        if result.synced_lyrics:
            lrc = parse_lrc(result.synced_lyrics)
            return LyricsData(
                artist=result.artist_name,
                title=result.track_name,
                synced=True,
                lrc=lrc,
                plain_text=result.plain_lyrics,
                source="syncedlyrics",
            )
        return LyricsData(
            artist=result.artist_name,
            title=result.track_name,
            synced=False,
            lrc=None,
            plain_text=result.plain_lyrics,
            source="syncedlyrics",
        )

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def fetch_lyrics(
        self,
        artist: str,
        title: str,
        album: str = "",
        trackid: str = "",
        duration_ms: int = 0,
        force_refresh: bool = False,
    ):
        print(f"[Lyrics] search: title=\"{title}\" artist=\"{artist}\" album=\"{album}\"")

        # Bump generation for every call so stale worker results are discarded.
        self._req_id += 1
        req_id = self._req_id

        # Always cooperatively cancel currently running worker without blocking UI.
        self._cancel_active_worker()

        key = self._make_key(artist, title, trackid)
        cached, cached_alts = self._get_cached(key)

        if not title:
            self._last_search_key = ""
            self._cached_alternatives = []
            self.lyrics_not_found.emit(artist, title)
            return

        if not self._lrclib_enabled and not self._syncedlyrics_enabled:
            self._last_search_key = ""
            self._cached_alternatives = []
            self.lyrics_not_found.emit(artist, title)
            return

        if cached and not force_refresh:
            self._last_search_key = key
            self._cached_alternatives = cached_alts
            result = LyricsResult(primary=cached, alternatives=cached_alts)
            self.lyrics_ready.emit(result)
            return

        worker = _FetchPrimaryThread(
            artist,
            title,
            album,
            duration_ms,
            self._lrclib_enabled,
            self._syncedlyrics_enabled,
            self._syncedlyrics_enhanced,
            self,
        )
        self._register_worker(worker)
        worker.finished_ok.connect(
            lambda result, a=artist, t=title, k=key, rid=req_id, cached_result=cached, cached_result_alts=cached_alts, refresh=force_refresh:
                self._on_fetch_done(result, a, t, k, rid, cached_result, cached_result_alts, refresh),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.finished_error.connect(
            lambda msg, a=artist, t=title, k=key, rid=req_id, cached_result=cached, cached_result_alts=cached_alts, refresh=force_refresh:
                self._on_fetch_error(a, t, k, rid, msg, cached_result, cached_result_alts, refresh),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.start()

    def fetch_alternatives(
        self,
        artist: str,
        title: str,
        album: str = "",
        duration_ms: int = 0,
    ):
        """Fetch alternative lyrics matches for the current track.

        Called when the user clicks the alternatives button in the overlay.
        Emits ``alternatives_ready`` with ``list[LyricsData]`` on success.
        """
        self._req_id += 1
        req_id = self._req_id
        self._cancel_active_worker()

        if not self._lrclib_enabled:
            print("[LRClib] alternatives skipped: source disabled")
            self.alternatives_ready.emit([])
            return

        key = self._last_search_key

        worker = _FetchAltThread(artist, title, album, duration_ms, self)
        self._register_worker(worker)
        worker.finished_ok.connect(
            lambda results, k=key, rid=req_id:
                self._on_alternatives_done(results, k, rid),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.finished_error.connect(
            lambda msg, rid=req_id: self._on_fetch_error("", "", "", rid, msg),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.start()

    def _on_fetch_done(
        self,
        result,
        artist,
        title,
        key,
        req_id,
        cached_result: LyricsData | None = None,
        cached_alts: list[LyricsData] | None = None,
        force_refresh: bool = False,
    ):
        if req_id != self._req_id:
            return
        self._last_search_key = ""
        if result is None:
            if force_refresh and cached_result is not None:
                fallback_alts = list(cached_alts or [])
                self._last_search_key = key
                self._cached_alternatives = fallback_alts
                self.lyrics_ready.emit(
                    LyricsResult(primary=cached_result, alternatives=fallback_alts)
                )
                return
            print(f"[Lyrics] not found: \"{title}\" by \"{artist}\"")
            self._cached_alternatives = []
            self.lyrics_not_found.emit(artist, title)
            return

        source, payload = result
        if source == "syncedlyrics":
            primary = self._convert_syncedlyrics_result(payload)
        else:
            primary = self._convert_lrclib_result(payload)
        self._cached_alternatives = []
        self._last_search_key = key
        self._put_cache(key, primary, [])  # no alternatives yet

        print(f"[Lyrics] primary: \"{primary.title}\" by \"{primary.artist}\"")
        self.lyrics_ready.emit(LyricsResult(primary=primary, alternatives=[]))

    def _on_alternatives_done(self, results: list, key, req_id):
        if req_id != self._req_id:
            return
        if not results:
            self.alternatives_ready.emit([])
            return

        alts = [self._convert_lrclib_result(r) for r in results]
        self._cached_alternatives = alts
        self._update_cache_alternatives(key, alts)

        print(f"[Lyrics] alternatives: {len(alts)} result(s)")
        self.alternatives_ready.emit(alts)

    def _on_fetch_error(
        self,
        artist,
        title,
        key,
        req_id,
        message: str = "",
        cached_result: LyricsData | None = None,
        cached_alts: list[LyricsData] | None = None,
        force_refresh: bool = False,
    ):
        if req_id != self._req_id:
            return
        if message:
            print(f"[Lyrics] fetch error: {message}")
        if force_refresh and cached_result is not None:
            fallback_alts = list(cached_alts or [])
            self._last_search_key = key
            self._cached_alternatives = fallback_alts
            self.lyrics_ready.emit(
                LyricsResult(primary=cached_result, alternatives=fallback_alts)
            )
            return
        self._cached_alternatives = []
        self.lyrics_not_found.emit(artist, title)

    def select_alternative(self, index: int) -> LyricsData | None:
        """Switch to an alternative (0 = first alternative).
        The previously displayed lyrics become an alternative, ensuring the
        swap is reversible."""
        if 0 <= index < len(self._cached_alternatives):
            self._cached_alternatives[0], self._cached_alternatives[index] = (
                self._cached_alternatives[index],
                self._cached_alternatives[0],
            )
            return self._cached_alternatives[0]
        return None

    def set_lrclib_enabled(self, enabled: bool):
        self._lrclib_enabled = enabled

    def set_syncedlyrics_enabled(self, enabled: bool):
        self._syncedlyrics_enabled = enabled

    def set_syncedlyrics_enhanced(self, enabled: bool):
        self._syncedlyrics_enhanced = enabled

    def set_cache_limits(self, ttl_days: int, max_entries: int):
        self._ttl_days = ttl_days
        self._max_entries = max_entries
        cutoff = _time.time() - self._ttl_days * 86400
        self._conn.execute("DELETE FROM cache WHERE fetched_at < ?", (cutoff,))
        self._conn.commit()
        self._enforce_max_entries()

    def shutdown(self):
        self._req_id += 1
        for worker in list(self._live_workers):
            if worker.isRunning():
                worker.cancel()

    def _register_worker(self, worker: _FetchPrimaryThread | _FetchAltThread):
        self._live_workers.add(worker)
        self._worker = worker
        worker.finished.connect(
            lambda w=worker: self._release_worker(w),
            type=Qt.ConnectionType.QueuedConnection,
        )

    def _release_worker(self, worker: _FetchPrimaryThread | _FetchAltThread):
        self._live_workers.discard(worker)
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _cancel_active_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
