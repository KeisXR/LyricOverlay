"""Tests for lyrics manager serialization and request generations."""

import sys
from pathlib import Path

sys.path.insert(0, "src")

import pytest
from PySide6.QtCore import QObject, Signal

from lyrics.lrc_parser import LyricLine, LyricWord, ParsedLRC, parse_lrc
from lyrics.lrclib import LrcLibResult
from lyrics.manager import LyricsData, LyricsManager, _FetchAltThread, _FetchPrimaryThread
import lyrics.manager as manager_module


@pytest.fixture
def isolated_cache_dir(tmp_path, monkeypatch):
    cache_dir = tmp_path / "lyricaod-cache"
    monkeypatch.setattr(manager_module, "CACHE_DIR", Path(cache_dir))
    return cache_dir


class _SignalProxy(QObject):
    finished_ok = Signal(object)
    finished_error = Signal(str)
    finished = Signal()


class _FakePrimaryThread:
    _NO_EMIT = object()
    next_result = _NO_EMIT
    next_error = None
    starts = 0

    def __init__(self, *args, **kwargs):
        self._proxy = _SignalProxy()
        self.finished_ok = self._proxy.finished_ok
        self.finished_error = self._proxy.finished_error
        self.finished = self._proxy.finished
        self._running = False
        self.cancel_called = False
        type(self).starts += 1

    def start(self):
        self._running = True
        if type(self).next_error is not None:
            self.finished_error.emit(type(self).next_error)
            self._running = False
            self.finished.emit()
        elif type(self).next_result is not type(self)._NO_EMIT:
            self.finished_ok.emit(type(self).next_result)
            self._running = False
            self.finished.emit()

    def isRunning(self):
        return self._running

    def cancel(self):
        self.cancel_called = True

    def deleteLater(self):
        pass


class _FakeAltThread:
    next_results = []

    def __init__(self, *args, **kwargs):
        self._proxy = _SignalProxy()
        self.finished_ok = self._proxy.finished_ok
        self.finished_error = self._proxy.finished_error
        self.finished = self._proxy.finished
        self._running = False
        self.cancel_called = False

    def start(self):
        self._running = True
        self.finished_ok.emit(list(type(self).next_results))
        self._running = False
        self.finished.emit()

    def isRunning(self):
        return self._running

    def cancel(self):
        self.cancel_called = True

    def deleteLater(self):
        pass


def _plain(title, artist="artist", text="lyrics"):
    return LyricsData(artist, title, False, None, text, "cache")


def test_lrc_to_raw_preserves_offset_once():
    lrc = ParsedLRC(
        offset_ms=1500,
        lines=[LyricLine(timestamp_ms=2500, text="Line")],
    )
    reparsed = parse_lrc(LyricsManager._lrc_to_raw(lrc))
    assert reparsed.offset_ms == 1500
    assert reparsed.lines[0].timestamp_ms == 2500


def test_lrc_to_raw_preserves_enhanced_word_timing():
    lrc = ParsedLRC(
        lines=[
            LyricLine(
                1000,
                "全力少年",
                [
                    LyricWord(1000, "全"),
                    LyricWord(1200, "力"),
                    LyricWord(1400, "少年"),
                ],
            )
        ]
    )
    reparsed = parse_lrc(LyricsManager._lrc_to_raw(lrc))
    assert reparsed.lines[0].text == "全力少年"
    assert [word.timestamp_ms for word in reparsed.lines[0].words] == [
        1000,
        1200,
        1400,
    ]


def test_fetch_alt_thread_constructor_matches_call_shape():
    assert _FetchAltThread("artist", "title", "album", 123000)


def test_primary_thread_delegates_to_selector(monkeypatch):
    expected = LrcLibResult(
        track_name="Title",
        artist_name="Artist",
        plain_lyrics="plain",
    )
    calls = []

    def fake_selector(*args, **kwargs):
        calls.append((args, kwargs))
        return "lrclib", expected

    monkeypatch.setattr(manager_module, "select_primary_result", fake_selector)
    emitted = []
    thread = _FetchPrimaryThread(
        "Artist", "Title", "", 0, True, True, True
    )
    thread.finished_ok.connect(emitted.append)
    thread.run()

    assert emitted == [("lrclib", expected)]
    assert calls[0][1]["use_lrclib"] is True
    assert calls[0][1]["use_syncedlyrics"] is True


def test_slow_a_does_not_overwrite_cache_hit_b(isolated_cache_dir, monkeypatch):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    _FakePrimaryThread.next_error = None
    manager = LyricsManager()
    ready = []
    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))

    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(key_b, _plain("Song B", "artist-b"), [])
    manager.fetch_lyrics("artist-a", "Song A")
    stale_request = manager._req_id
    key_a = manager._make_key("artist-a", "Song A", "")
    manager.fetch_lyrics("artist-b", "Song B")

    manager._on_fetch_done(
        (
            "lrclib",
            LrcLibResult(
                track_name="Song A",
                artist_name="artist-a",
                plain_lyrics="A",
            ),
        ),
        "artist-a",
        "Song A",
        key_a,
        stale_request,
    )
    assert ready == ["Song B"]


def test_empty_title_invalidates_pending_result(isolated_cache_dir, monkeypatch):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    manager = LyricsManager()
    ready = []
    not_found = []
    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    manager.lyrics_not_found.connect(lambda artist, title: not_found.append((artist, title)))

    manager.fetch_lyrics("artist-a", "Song A")
    stale_request = manager._req_id
    manager.fetch_lyrics("", "")
    manager._on_fetch_done(
        (
            "lrclib",
            LrcLibResult(
                track_name="Song A",
                artist_name="artist-a",
                plain_lyrics="A",
            ),
        ),
        "artist-a",
        "Song A",
        manager._make_key("artist-a", "Song A", ""),
        stale_request,
    )

    assert ready == []
    assert not_found == [("", "")]


def test_disabled_sources_invalidate_pending_result(isolated_cache_dir, monkeypatch):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    manager = LyricsManager()
    ready = []
    not_found = []
    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    manager.lyrics_not_found.connect(lambda artist, title: not_found.append((artist, title)))

    manager.fetch_lyrics("artist-a", "Song A")
    stale_request = manager._req_id
    manager.set_lrclib_enabled(False)
    manager.set_syncedlyrics_enabled(False)
    manager.fetch_lyrics("artist-b", "Song B")
    manager._on_fetch_done(
        (
            "lrclib",
            LrcLibResult(
                track_name="Song A",
                artist_name="artist-a",
                plain_lyrics="A",
            ),
        ),
        "artist-a",
        "Song A",
        manager._make_key("artist-a", "Song A", ""),
        stale_request,
    )

    assert ready == []
    assert not_found == [("artist-b", "Song B")]


def test_alternatives_after_cache_hit_use_current_key(isolated_cache_dir, monkeypatch):
    monkeypatch.setattr(manager_module, "_FetchAltThread", _FakeAltThread)
    manager = LyricsManager()
    key_a = manager._make_key("artist-a", "Song A", "")
    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(key_a, _plain("Song A", "artist-a"), [])
    manager._put_cache(key_b, _plain("Song B", "artist-b"), [])

    manager.fetch_lyrics("artist-a", "Song A")
    manager.fetch_lyrics("artist-b", "Song B")
    alternatives = [
        LrcLibResult(
            track_name="Song B alt",
            artist_name="artist-b",
            plain_lyrics="alt",
        )
    ]
    _FakeAltThread.next_results = alternatives
    manager.fetch_alternatives("artist-b", "Song B")
    manager._on_alternatives_done(alternatives, key_b, manager._req_id)

    assert manager._get_cached(key_a)[1] == []
    assert [item.title for item in manager._get_cached(key_b)[1]] == ["Song B alt"]


def test_stale_alternatives_result_emits_terminal_empty_signal(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchAltThread", _FakeAltThread)
    _FakeAltThread.next_results = []
    manager = LyricsManager()
    emitted = []
    manager.alternatives_ready.connect(emitted.append)

    key_a = manager._make_key("artist-a", "Song A", "")
    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(key_a, _plain("Song A", "artist-a"), [])
    manager._put_cache(key_b, _plain("Song B", "artist-b"), [])

    manager.fetch_lyrics("artist-a", "Song A")
    manager.fetch_alternatives("artist-a", "Song A")
    stale_req = manager._req_id
    emitted.clear()

    # Track change served from the cache bumps the generation.
    manager.fetch_lyrics("artist-b", "Song B")

    manager._on_alternatives_done(
        [LrcLibResult(track_name="Song A alt", artist_name="artist-a")],
        key_a,
        stale_req,
    )

    # A terminal signal is delivered so the UI can leave its loading state,
    # but the stale results are neither emitted nor cached.
    assert emitted == [[]]
    assert manager._cached_alternatives == []
    assert manager._get_cached(key_a)[1] == []


def test_force_refresh_bypasses_cache(isolated_cache_dir, monkeypatch):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.starts = 0
    _FakePrimaryThread.next_error = None
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    manager = LyricsManager()
    key = manager._make_key("artist", "Song", "")
    manager._put_cache(key, _plain("Song"), [])

    manager.fetch_lyrics("artist", "Song", force_refresh=True)

    assert _FakePrimaryThread.starts == 1


def test_stale_success_and_error_do_not_change_current_state(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    manager = LyricsManager()
    ready = []
    not_found = []
    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    manager.lyrics_not_found.connect(lambda artist, title: not_found.append((artist, title)))

    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(key_b, _plain("Song B", "artist-b"), [])
    manager.fetch_lyrics("artist-a", "Song A")
    stale_request = manager._req_id
    manager.fetch_lyrics("artist-b", "Song B")

    manager._on_fetch_done(None, "artist-a", "Song A", "", stale_request)
    manager._on_fetch_error("artist-a", "Song A", "", stale_request, "boom")

    assert ready == ["Song B"]
    assert not_found == []
