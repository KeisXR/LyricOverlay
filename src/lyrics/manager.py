"""Lyrics fetch orchestration with generation-safe workers and SQLite caching."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal

from .lrc_parser import ParsedLRC, parse_lrc
from .lrclib import LrcLibResult, search_all
from .provider_selector import rank_lrclib_results, select_primary_result
from .syncedlyrics_client import SyncedLyricsResult


def _get_cache_dir() -> Path:
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
    primary: LyricsData
    alternatives: list[LyricsData] = field(default_factory=list)


class _FetchPrimaryThread(QThread):
    finished_ok = Signal(object)
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
            result = select_primary_result(
                self._artist,
                self._title,
                self._album,
                self._duration_ms,
                use_lrclib=self._use_lrclib,
                use_syncedlyrics=self._use_syncedlyrics,
                syncedlyrics_enhanced=self._syncedlyrics_enhanced,
                cancelled=lambda: self._cancel,
            )
            if not self._cancel:
                self.finished_ok.emit(result)
        except Exception as exc:
            if not self._cancel:
                self.finished_error.emit(str(exc))


class _FetchAltThread(QThread):
    finished_ok = Signal(object)
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
                self._artist,
                self._title,
                self._album,
                self._duration_ms,
            )
            if self._cancel:
                return
            ranked = rank_lrclib_results(
                self._artist,
                self._title,
                self._album,
                self._duration_ms,
                results,
            )
            self.finished_ok.emit(ranked)
        except Exception as exc:
            if not self._cancel:
                self.finished_error.emit(str(exc))


class LyricsManager(QObject):
    lyrics_ready = Signal(LyricsResult)
    lyrics_not_found = Signal(str, str)
    alternatives_ready = Signal(list)

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
        self._last_search_key = ""
        self._cached_alternatives: list[LyricsData] = []

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
        cutoff = _time.time() - self._ttl_days * 86400
        self._conn.execute("DELETE FROM cache WHERE fetched_at < ?", (cutoff,))
        self._conn.commit()
        self._enforce_max_entries()

    def _enforce_max_entries(self):
        row = self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()
        count = row[0] if row else 0
        if count <= self._max_entries:
            return
        self._conn.execute(
            "DELETE FROM cache WHERE key IN ("
            " SELECT key FROM cache ORDER BY fetched_at ASC LIMIT ?"
            ")",
            (count - self._max_entries,),
        )
        self._conn.commit()

    def _make_key(self, artist: str, title: str, trackid: str = "") -> str:
        return hashlib.sha256(f"{artist}|{title}|{trackid}".encode()).hexdigest()

    @staticmethod
    def _dict_to_lyrics_data(entry: dict) -> LyricsData:
        lrc = parse_lrc(entry["lrc_raw"]) if entry.get("lrc_raw") else None
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
        return {
            "artist": data.artist,
            "title": data.title,
            "synced": data.synced,
            "plain_text": data.plain_text,
            "lrc_raw": LyricsManager._lrc_to_raw(data.lrc) if data.lrc else "",
            "source": data.source,
        }

    def _get_cached(self, key: str) -> tuple[LyricsData | None, list[LyricsData]]:
        row = self._conn.execute(
            "SELECT data FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None, []
        try:
            payload = json.loads(row[0])
            if (
                not isinstance(payload, dict)
                or payload.get("version") != CACHE_FORMAT_VERSION
                or "primary" not in payload
            ):
                return None, []
            primary = self._dict_to_lyrics_data(payload["primary"])
            alternatives = [
                self._dict_to_lyrics_data(item)
                for item in payload.get("alternatives", [])
            ]
            return primary, alternatives
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None, []

    def _put_cache(
        self, key: str, primary: LyricsData, alternatives: list[LyricsData]
    ):
        payload = {
            "version": CACHE_FORMAT_VERSION,
            "primary": self._lyrics_data_to_dict(primary),
            "alternatives": [
                self._lyrics_data_to_dict(item) for item in alternatives
            ],
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, data, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload, ensure_ascii=False), _time.time()),
        )
        self._conn.commit()
        self._enforce_max_entries()

    def _update_cache_alternatives(
        self, key: str, alternatives: list[LyricsData]
    ):
        row = self._conn.execute(
            "SELECT data FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or "primary" not in payload:
            return
        payload["alternatives"] = [
            self._lyrics_data_to_dict(item) for item in alternatives
        ]
        self._conn.execute(
            "UPDATE cache SET data = ?, fetched_at = ? WHERE key = ?",
            (json.dumps(payload, ensure_ascii=False), _time.time(), key),
        )
        self._conn.commit()

    @staticmethod
    def _format_lrc_timestamp(timestamp_ms: int) -> str:
        timestamp_ms = max(0, timestamp_ms)
        minutes = timestamp_ms // 60000
        seconds = (timestamp_ms % 60000) / 1000
        return f"{minutes:02d}:{seconds:05.2f}"

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
            line_ms = line.timestamp_ms - lrc.offset_ms
            line_tag = f"[{LyricsManager._format_lrc_timestamp(line_ms)}]"
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
            return LyricsData(
                artist=result.artist_name,
                title=result.track_name,
                synced=True,
                lrc=parse_lrc(result.synced_lyrics),
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
            return LyricsData(
                artist=result.artist_name,
                title=result.track_name,
                synced=True,
                lrc=parse_lrc(result.synced_lyrics),
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

    def fetch_lyrics(
        self,
        artist: str,
        title: str,
        album: str = "",
        trackid: str = "",
        duration_ms: int = 0,
        force_refresh: bool = False,
    ):
        self._req_id += 1
        req_id = self._req_id
        self._cancel_active_worker()

        key = self._make_key(artist, title, trackid)
        cached, cached_alternatives = self._get_cached(key)

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
            self._cached_alternatives = cached_alternatives
            self.lyrics_ready.emit(
                LyricsResult(primary=cached, alternatives=cached_alternatives)
            )
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
            lambda result, a=artist, t=title, k=key, rid=req_id,
            cached_result=cached, cached_alts=cached_alternatives,
            refresh=force_refresh: self._on_fetch_done(
                result,
                a,
                t,
                k,
                rid,
                cached_result,
                cached_alts,
                refresh,
            ),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.finished_error.connect(
            lambda message, a=artist, t=title, k=key, rid=req_id,
            cached_result=cached, cached_alts=cached_alternatives,
            refresh=force_refresh: self._on_fetch_error(
                a,
                t,
                k,
                rid,
                message,
                cached_result,
                cached_alts,
                refresh,
            ),
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
        self._req_id += 1
        req_id = self._req_id
        self._cancel_active_worker()

        if not self._lrclib_enabled:
            self.alternatives_ready.emit([])
            return

        key = self._last_search_key
        worker = _FetchAltThread(artist, title, album, duration_ms, self)
        self._register_worker(worker)
        worker.finished_ok.connect(
            lambda results, k=key, rid=req_id: self._on_alternatives_done(
                results, k, rid
            ),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.finished_error.connect(
            lambda message, rid=req_id: self._on_fetch_error(
                "", "", "", rid, message
            ),
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
                alternatives = list(cached_alts or [])
                self._last_search_key = key
                self._cached_alternatives = alternatives
                self.lyrics_ready.emit(
                    LyricsResult(cached_result, alternatives)
                )
                return
            self._cached_alternatives = []
            self.lyrics_not_found.emit(artist, title)
            return

        source, payload = result
        primary = (
            self._convert_syncedlyrics_result(payload)
            if source == "syncedlyrics"
            else self._convert_lrclib_result(payload)
        )
        self._cached_alternatives = []
        self._last_search_key = key
        self._put_cache(key, primary, [])
        self.lyrics_ready.emit(LyricsResult(primary, []))

    def _on_alternatives_done(self, results: list, key, req_id):
        if req_id != self._req_id:
            # Superseded by a newer request: the stale results must never be
            # delivered, but the UI still needs a terminal signal so its
            # "loading alternatives" state does not stay on forever.
            print("[Lyrics] alternatives discarded: superseded by a newer request")
            self.alternatives_ready.emit([])
            return
        if not results:
            self.alternatives_ready.emit([])
            return
        alternatives = [self._convert_lrclib_result(item) for item in results]
        self._cached_alternatives = alternatives
        self._update_cache_alternatives(key, alternatives)
        self.alternatives_ready.emit(alternatives)

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
        if force_refresh and cached_result is not None:
            alternatives = list(cached_alts or [])
            self._last_search_key = key
            self._cached_alternatives = alternatives
            self.lyrics_ready.emit(LyricsResult(cached_result, alternatives))
            return
        self._cached_alternatives = []
        self.lyrics_not_found.emit(artist, title)

    def select_alternative(self, index: int) -> LyricsData | None:
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

    def _register_worker(
        self, worker: _FetchPrimaryThread | _FetchAltThread
    ):
        self._live_workers.add(worker)
        self._worker = worker
        worker.finished.connect(
            lambda current=worker: self._release_worker(current),
            type=Qt.ConnectionType.QueuedConnection,
        )

    def _release_worker(
        self, worker: _FetchPrimaryThread | _FetchAltThread
    ):
        self._live_workers.discard(worker)
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _cancel_active_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
