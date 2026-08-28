"""Lyrics fetch orchestration with durable candidate caching."""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal

from .cache_repository import CacheRepository, SCHEMA_VERSION
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
CACHE_FORMAT_VERSION = SCHEMA_VERSION
NEGATIVE_CACHE_TTL_SECONDS = 15 * 60


@dataclass
class LyricsData:
    artist: str
    title: str
    synced: bool
    lrc: ParsedLRC | None
    plain_text: str | None
    source: str
    raw_lyrics: str = ""
    provider_id: str = ""
    candidate_id: str = ""


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
            raw_results = search_all(
                self._artist,
                self._title,
                self._album,
                self._duration_ms,
            )
            if self._cancel:
                return
            self.finished_ok.emit(
                rank_lrclib_results(
                    self._artist,
                    self._title,
                    self._album,
                    self._duration_ms,
                    raw_results,
                )
            )
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
        *,
        cache_path: str | Path | None = None,
        cache_clock=None,
    ):
        super().__init__(parent)
        self._ttl_days = max(1, int(ttl_days))
        self._max_entries = max(1, int(max_entries))
        self._lrclib_enabled = lrclib_enabled
        self._syncedlyrics_enabled = syncedlyrics_enabled
        self._syncedlyrics_enhanced = syncedlyrics_enhanced

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = Path(cache_path) if cache_path is not None else CACHE_DIR / "cache.db"
        self._cache = CacheRepository(
            path,
            max_entries=self._max_entries,
            clock=cache_clock,
        )

        self._worker: _FetchPrimaryThread | _FetchAltThread | None = None
        self._live_workers: set[_FetchPrimaryThread | _FetchAltThread] = set()
        self._req_id = 0
        self._last_search_key = ""
        self._current_cache_aliases: tuple[str, ...] = ()
        self._current_primary: LyricsData | None = None
        self._cached_alternatives: list[LyricsData] = []

    @staticmethod
    def _legacy_key(artist: str, title: str, trackid: str = "") -> str:
        return hashlib.sha256(f"{artist}|{title}|{trackid}".encode()).hexdigest()

    def _make_key(
        self,
        artist: str,
        title: str,
        trackid: str = "",
        album: str = "",
        duration_ms: int = 0,
    ) -> str:
        return self._cache.canonical_key(artist, title, album, duration_ms)

    def _aliases(self, artist: str, title: str, trackid: str) -> tuple[str, ...]:
        """Return cache aliases that cannot collide across different tracks.

        Only the v2 key is aliased so migrated entries stay reachable. The bare
        trackid is deliberately excluded: players report constant identifiers
        ("browser-ws", an SMTC AUMID, a reused MPRIS path), so aliasing it would
        map every track to whichever entry was written last.
        """
        return (self._legacy_key(artist, title, trackid),)

    @staticmethod
    def _candidate_id(data: LyricsData) -> str:
        raw = data.raw_lyrics or data.plain_text or ""
        identity = "|".join(
            (
                data.source,
                data.provider_id,
                data.artist,
                data.title,
                raw,
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @classmethod
    def _ensure_candidate_id(cls, data: LyricsData) -> LyricsData:
        if data.candidate_id:
            return data
        return replace(data, candidate_id=cls._candidate_id(data))

    @classmethod
    def _dict_to_lyrics_data(cls, entry: dict) -> LyricsData:
        synced = bool(entry.get("synced", False))
        raw = entry.get("raw_lyrics") or entry.get("lrc_raw") or ""
        lrc = parse_lrc(raw) if synced and raw else None
        data = LyricsData(
            artist=str(entry.get("artist", "")),
            title=str(entry.get("title", "")),
            synced=synced,
            lrc=lrc,
            plain_text=entry.get("plain_text"),
            source=str(entry.get("source", "cache")),
            raw_lyrics=raw,
            provider_id=str(entry.get("provider_id", "")),
            candidate_id=str(entry.get("candidate_id", "")),
        )
        return cls._ensure_candidate_id(data)

    @classmethod
    def _lyrics_data_to_dict(cls, data: LyricsData) -> dict:
        data = cls._ensure_candidate_id(data)
        raw = data.raw_lyrics
        if not raw:
            raw = cls._lrc_to_raw(data.lrc) if data.lrc else data.plain_text or ""
        return {
            "artist": data.artist,
            "title": data.title,
            "synced": data.synced,
            "plain_text": data.plain_text,
            "raw_lyrics": raw,
            "source": data.source,
            "provider_id": data.provider_id,
            "candidate_id": data.candidate_id,
        }

    @classmethod
    def _payload_for(
        cls, primary: LyricsData, alternatives: list[LyricsData]
    ) -> dict:
        primary = cls._ensure_candidate_id(primary)
        seen = {primary.candidate_id}
        candidates = [primary]
        for item in alternatives:
            item = cls._ensure_candidate_id(item)
            if item.candidate_id not in seen:
                seen.add(item.candidate_id)
                candidates.append(item)
        return {
            "version": CACHE_FORMAT_VERSION,
            "candidates": [cls._lyrics_data_to_dict(item) for item in candidates],
            "selected_id": primary.candidate_id,
        }

    @classmethod
    def _result_from_payload(cls, payload: dict) -> LyricsResult | None:
        if not isinstance(payload, dict):
            return None
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            return None
        try:
            candidates = [
                cls._dict_to_lyrics_data(item)
                for item in raw_candidates
                if isinstance(item, dict)
            ]
        except (TypeError, ValueError):
            return None
        if not candidates:
            return None
        selected_id = str(payload.get("selected_id", ""))
        selected_index = next(
            (
                index
                for index, item in enumerate(candidates)
                if item.candidate_id == selected_id
            ),
            0,
        )
        primary = candidates[selected_index]
        alternatives = [
            item for index, item in enumerate(candidates) if index != selected_index
        ]
        return LyricsResult(primary, alternatives)

    @classmethod
    def _merge_refreshed_primary(
        cls, cached: LyricsResult, primary: LyricsData
    ) -> LyricsResult:
        """Fold a refreshed primary into a cached set, keeping its selection."""
        primary = cls._ensure_candidate_id(primary)
        selected = cls._ensure_candidate_id(cached.primary)
        if primary.candidate_id == selected.candidate_id:
            return LyricsResult(primary, list(cached.alternatives))
        merged: list[LyricsData] = []
        seen = {selected.candidate_id}
        for item in [*cached.alternatives, primary]:
            item = cls._ensure_candidate_id(item)
            if item.candidate_id not in seen:
                seen.add(item.candidate_id)
                merged.append(item)
        return LyricsResult(selected, merged)

    def _load_record(
        self, key: str, aliases: tuple[str, ...]
    ) -> tuple[LyricsResult | None, bool]:
        record = self._cache.get(key, aliases=aliases)
        if record is None:
            return None, False
        if record.negative:
            return None, True
        result = self._result_from_payload(record.payload or {})
        return result, False

    def _get_cached(
        self, key: str, aliases: tuple[str, ...] = ()
    ) -> tuple[LyricsData | None, list[LyricsData]]:
        result, negative = self._load_record(key, aliases)
        if result is None or negative:
            return None, []
        return result.primary, result.alternatives

    def _put_cache(
        self,
        key: str,
        primary: LyricsData,
        alternatives: list[LyricsData],
        aliases: tuple[str, ...] = (),
    ):
        self._cache.put(
            key,
            self._payload_for(primary, alternatives),
            ttl_seconds=self._ttl_days * 86400,
            aliases=aliases,
        )

    def _update_cache_alternatives(
        self, key: str, alternatives: list[LyricsData]
    ):
        if not self._current_primary:
            return
        self._put_cache(
            key,
            self._current_primary,
            alternatives,
            self._current_cache_aliases,
        )

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
                words = []
                for word in line.words:
                    word_ms = word.timestamp_ms - lrc.offset_ms
                    words.append(
                        f"<{LyricsManager._format_lrc_timestamp(word_ms)}>{word.text}"
                    )
                parts.append(f"{line_tag}{''.join(words).rstrip()}")
            else:
                parts.append(f"{line_tag}{line.text}")
        return "\n".join(parts)

    @classmethod
    def _convert_lrclib_result(cls, result: LrcLibResult) -> LyricsData:
        raw = result.synced_lyrics or result.plain_lyrics or ""
        data = LyricsData(
            artist=result.artist_name,
            title=result.track_name,
            synced=bool(result.synced_lyrics),
            lrc=parse_lrc(result.synced_lyrics) if result.synced_lyrics else None,
            plain_text=result.plain_lyrics,
            source="lrclib",
            raw_lyrics=raw,
            provider_id=str(result.id or ""),
        )
        return cls._ensure_candidate_id(data)

    @classmethod
    def _convert_syncedlyrics_result(cls, result: SyncedLyricsResult) -> LyricsData:
        raw = result.synced_lyrics or result.plain_lyrics or ""
        data = LyricsData(
            artist=result.artist_name,
            title=result.track_name,
            synced=bool(result.synced_lyrics),
            lrc=parse_lrc(result.synced_lyrics) if result.synced_lyrics else None,
            plain_text=result.plain_lyrics,
            source="syncedlyrics",
            raw_lyrics=raw,
            provider_id=f"{result.artist_name}|{result.track_name}",
        )
        return cls._ensure_candidate_id(data)

    def _adopt_result(
        self,
        key: str,
        aliases: tuple[str, ...],
        result: LyricsResult,
    ) -> None:
        self._last_search_key = key
        self._current_cache_aliases = aliases
        self._current_primary = result.primary
        self._cached_alternatives = list(result.alternatives)

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
        request_id = self._req_id
        self._cancel_active_worker()

        key = self._make_key(artist, title, trackid, album, duration_ms)
        aliases = self._aliases(artist, title, trackid)
        cached_result, negative = self._load_record(key, aliases)

        if not title:
            self._clear_current_context()
            self.lyrics_not_found.emit(artist, title)
            return

        if cached_result is not None and not force_refresh:
            self._adopt_result(key, aliases, cached_result)
            self.lyrics_ready.emit(cached_result)
            return
        if negative and not force_refresh:
            self._clear_current_context()
            self.lyrics_not_found.emit(artist, title)
            return

        if not self._lrclib_enabled and not self._syncedlyrics_enabled:
            if cached_result is not None:
                self._adopt_result(key, aliases, cached_result)
                self.lyrics_ready.emit(cached_result)
            else:
                self._clear_current_context()
                self.lyrics_not_found.emit(artist, title)
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
            lambda provider_result, a=artist, t=title, k=key, alias_set=aliases,
            rid=request_id, cached=cached_result, refresh=force_refresh:
            self._on_fetch_done(
                provider_result,
                a,
                t,
                k,
                rid,
                alias_set,
                cached,
                refresh,
            ),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.finished_error.connect(
            lambda message, a=artist, t=title, k=key, alias_set=aliases,
            rid=request_id, cached=cached_result, refresh=force_refresh:
            self._on_fetch_error(
                a,
                t,
                k,
                rid,
                message,
                alias_set,
                cached,
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
        request_id = self._req_id
        self._cancel_active_worker()
        if not self._lrclib_enabled:
            self.alternatives_ready.emit([])
            return
        worker = _FetchAltThread(artist, title, album, duration_ms, self)
        self._register_worker(worker)
        worker.finished_ok.connect(
            lambda results, key=self._last_search_key, rid=request_id:
            self._on_alternatives_done(results, key, rid),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.finished_error.connect(
            lambda message, rid=request_id: self._on_alternatives_error(
                rid, message
            ),
            type=Qt.ConnectionType.QueuedConnection,
        )
        worker.start()

    def _on_fetch_done(
        self,
        provider_result,
        artist: str,
        title: str,
        key: str,
        request_id: int,
        aliases: tuple[str, ...] = (),
        cached_result: LyricsResult | None = None,
        force_refresh: bool = False,
    ):
        if request_id != self._req_id:
            return
        if provider_result is None:
            if force_refresh and cached_result is not None:
                self._adopt_result(key, aliases, cached_result)
                self.lyrics_ready.emit(cached_result)
                return
            self._cache.put_negative(
                key,
                ttl_seconds=NEGATIVE_CACHE_TTL_SECONDS,
                aliases=aliases,
            )
            self._clear_current_context()
            self.lyrics_not_found.emit(artist, title)
            return

        source, payload = provider_result
        primary = (
            self._convert_syncedlyrics_result(payload)
            if source == "syncedlyrics"
            else self._convert_lrclib_result(payload)
        )
        result = (
            self._merge_refreshed_primary(cached_result, primary)
            if cached_result is not None
            else LyricsResult(primary, [])
        )
        self._put_cache(key, result.primary, result.alternatives, aliases)
        self._adopt_result(key, aliases, result)
        self.lyrics_ready.emit(result)

    def _on_alternatives_done(self, results: list, key: str, request_id: int):
        if request_id != self._req_id:
            # Superseded by a newer request: the stale results must never be
            # delivered, but the UI still needs a terminal signal so its
            # "loading alternatives" state does not stay on forever.
            print("[Lyrics] alternatives discarded: superseded by a newer request")
            self.alternatives_ready.emit([])
            return
        if not results or not self._current_primary:
            self.alternatives_ready.emit([])
            return
        new_items = [self._convert_lrclib_result(item) for item in results]
        primary_id = self._current_primary.candidate_id
        merged: list[LyricsData] = []
        seen = {primary_id}
        for item in [*self._cached_alternatives, *new_items]:
            item = self._ensure_candidate_id(item)
            if item.candidate_id not in seen:
                seen.add(item.candidate_id)
                merged.append(item)
        self._cached_alternatives = merged
        self._put_cache(
            key,
            self._current_primary,
            merged,
            self._current_cache_aliases,
        )
        self.alternatives_ready.emit(list(merged))

    def _on_alternatives_error(self, request_id: int, message: str = ""):
        if request_id != self._req_id:
            return
        self.alternatives_ready.emit([])

    def _on_fetch_error(
        self,
        artist: str,
        title: str,
        key: str,
        request_id: int,
        message: str = "",
        aliases: tuple[str, ...] = (),
        cached_result: LyricsResult | None = None,
        force_refresh: bool = False,
    ):
        if request_id != self._req_id:
            return
        if force_refresh and cached_result is not None:
            self._adopt_result(key, aliases, cached_result)
            self.lyrics_ready.emit(cached_result)
            return
        self._clear_current_context()
        self.lyrics_not_found.emit(artist, title)

    def select_alternative(self, index: int) -> LyricsData | None:
        if (
            self._current_primary is None
            or not (0 <= index < len(self._cached_alternatives))
        ):
            return None
        selected = self._cached_alternatives[index]
        old_primary = self._current_primary
        remaining = [
            item
            for current_index, item in enumerate(self._cached_alternatives)
            if current_index != index
        ]
        self._current_primary = selected
        self._cached_alternatives = [old_primary, *remaining]
        if self._last_search_key:
            self._put_cache(
                self._last_search_key,
                selected,
                self._cached_alternatives,
                self._current_cache_aliases,
            )
        return selected

    def _clear_current_context(self):
        self._last_search_key = ""
        self._current_cache_aliases = ()
        self._current_primary = None
        self._cached_alternatives = []

    def set_lrclib_enabled(self, enabled: bool):
        self._lrclib_enabled = enabled

    def set_syncedlyrics_enabled(self, enabled: bool):
        self._syncedlyrics_enabled = enabled

    def set_syncedlyrics_enhanced(self, enabled: bool):
        self._syncedlyrics_enhanced = enabled

    def set_cache_limits(self, ttl_days: int, max_entries: int):
        self._ttl_days = max(1, int(ttl_days))
        self._max_entries = max(1, int(max_entries))
        self._cache.set_max_entries(self._max_entries)
        self._cache.set_positive_ttl(self._ttl_days * 86400)

    def shutdown(self):
        self._req_id += 1
        for worker in list(self._live_workers):
            if worker.isRunning():
                worker.cancel()
        self._cache.close()

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
