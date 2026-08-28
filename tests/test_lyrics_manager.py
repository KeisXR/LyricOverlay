"""Tests for lyrics manager cache serialization helpers."""

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
        _FakePrimaryThread.starts += 1

    def start(self):
        self._running = True
        if _FakePrimaryThread.next_error is not None:
            self.finished_error.emit(_FakePrimaryThread.next_error)
            self._running = False
            self.finished.emit()
        elif _FakePrimaryThread.next_result is not _FakePrimaryThread._NO_EMIT:
            self.finished_ok.emit(_FakePrimaryThread.next_result)
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
        self.finished_ok.emit(list(_FakeAltThread.next_results))
        self._running = False
        self.finished.emit()

    def isRunning(self):
        return self._running

    def cancel(self):
        self.cancel_called = True

    def deleteLater(self):
        pass


def test_lrc_to_raw_preserves_offset_once():
    lrc = ParsedLRC(
        offset_ms=1500,
        lines=[LyricLine(timestamp_ms=2500, text="Line")],
    )

    raw = LyricsManager._lrc_to_raw(lrc)
    reparsed = parse_lrc(raw)

    assert "[offset:1500]" in raw
    assert reparsed.offset_ms == 1500
    assert reparsed.lines[0].timestamp_ms == 2500


def test_lrc_to_raw_preserves_enhanced_word_timing():
    lrc = ParsedLRC(
        lines=[
            LyricLine(
                timestamp_ms=1000,
                text="全力少年",
                words=[
                    LyricWord(timestamp_ms=1000, text="全"),
                    LyricWord(timestamp_ms=1200, text="力"),
                    LyricWord(timestamp_ms=1400, text="少年"),
                ],
            )
        ],
    )

    raw = LyricsManager._lrc_to_raw(lrc)
    reparsed = parse_lrc(raw)

    assert "<00:01.00>全" in raw
    assert reparsed.lines[0].text == "全力少年"
    assert reparsed.lines[0].words is not None
    assert [w.timestamp_ms for w in reparsed.lines[0].words] == [1000, 1200, 1400]


def test_fetch_alt_thread_constructor_matches_fetch_alternatives_call_shape():
    thread = _FetchAltThread("artist", "title", "album", 123000)

    assert thread is not None


def test_primary_thread_falls_back_to_lrclib_when_syncedlyrics_errors():
    original_syncedlyrics = manager_module.get_syncedlyrics
    original_lrclib = manager_module.get_lrclib
    emitted = []

    def failing_syncedlyrics(*args, **kwargs):
        raise RuntimeError("syncedlyrics unavailable")

    def fake_lrclib(*args, **kwargs):
        return LrcLibResult(track_name="Title", artist_name="Artist")

    try:
        manager_module.get_syncedlyrics = failing_syncedlyrics
        manager_module.get_lrclib = fake_lrclib
        thread = _FetchPrimaryThread(
            "Artist",
            "Title",
            "",
            0,
            True,
            True,
            True,
        )
        thread.finished_ok.connect(emitted.append)
        thread.run()
    finally:
        manager_module.get_syncedlyrics = original_syncedlyrics
        manager_module.get_lrclib = original_lrclib

    assert emitted
    assert emitted[0][0] == "lrclib"


def test_stale_slow_a_does_not_overwrite_cache_hit_b(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    _FakePrimaryThread.next_error = None
    manager = LyricsManager()
    ready = []

    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(
        key_b,
        LyricsData(
            artist="artist-b",
            title="Song B",
            synced=False,
            lrc=None,
            plain_text="cache-b",
            source="cache",
        ),
        [],
    )

    manager.fetch_lyrics("artist-a", "Song A")
    req_a = manager._req_id
    key_a = manager._make_key("artist-a", "Song A", "")
    manager.fetch_lyrics("artist-b", "Song B")

    manager._on_fetch_done(
        ("lrclib", LrcLibResult(track_name="Song A", artist_name="artist-a")),
        "artist-a",
        "Song A",
        key_a,
        req_a,
    )

    assert ready == ["Song B"]


def test_stale_result_ignored_after_empty_title_transition(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    _FakePrimaryThread.next_error = None
    manager = LyricsManager()
    ready = []
    not_found = []

    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    manager.lyrics_not_found.connect(lambda a, t: not_found.append((a, t)))

    manager.fetch_lyrics("artist-a", "Song A")
    req_a = manager._req_id
    key_a = manager._make_key("artist-a", "Song A", "")
    manager.fetch_lyrics("", "")

    manager._on_fetch_done(
        ("lrclib", LrcLibResult(track_name="Song A", artist_name="artist-a")),
        "artist-a",
        "Song A",
        key_a,
        req_a,
    )

    assert ready == []
    assert not_found == [("", "")]


def test_stale_result_ignored_when_sources_disabled(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    _FakePrimaryThread.next_error = None
    manager = LyricsManager()
    ready = []
    not_found = []

    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    manager.lyrics_not_found.connect(lambda a, t: not_found.append((a, t)))

    manager.fetch_lyrics("artist-a", "Song A")
    req_a = manager._req_id
    key_a = manager._make_key("artist-a", "Song A", "")

    manager.set_lrclib_enabled(False)
    manager.set_syncedlyrics_enabled(False)
    manager.fetch_lyrics("artist-b", "Song B")

    manager._on_fetch_done(
        ("lrclib", LrcLibResult(track_name="Song A", artist_name="artist-a")),
        "artist-a",
        "Song A",
        key_a,
        req_a,
    )

    assert ready == []
    assert not_found == [("artist-b", "Song B")]


def test_alternatives_after_cache_hit_use_current_track_key(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchAltThread", _FakeAltThread)
    manager = LyricsManager()
    key_a = manager._make_key("artist-a", "Song A", "")
    key_b = manager._make_key("artist-b", "Song B", "")

    manager._put_cache(
        key_a,
        LyricsData(
            artist="artist-a",
            title="Song A",
            synced=False,
            lrc=None,
            plain_text="A",
            source="cache",
        ),
        [],
    )
    manager._put_cache(
        key_b,
        LyricsData(
            artist="artist-b",
            title="Song B",
            synced=False,
            lrc=None,
            plain_text="B",
            source="cache",
        ),
        [],
    )

    manager.fetch_lyrics("artist-a", "Song A")
    manager.fetch_lyrics("artist-b", "Song B")

    _FakeAltThread.next_results = [
        LrcLibResult(track_name="Song B alt", artist_name="artist-b")
    ]
    manager.fetch_alternatives("artist-b", "Song B")
    manager._on_alternatives_done(
        list(_FakeAltThread.next_results),
        key_b,
        manager._req_id,
    )

    _, alts_a = manager._get_cached(key_a)
    _, alts_b = manager._get_cached(key_b)
    assert alts_a == []
    assert [a.title for a in alts_b] == ["Song B alt"]


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
    for key, artist, title in (
        (key_a, "artist-a", "Song A"),
        (key_b, "artist-b", "Song B"),
    ):
        manager._put_cache(
            key,
            LyricsData(
                artist=artist,
                title=title,
                synced=False,
                lrc=None,
                plain_text=title,
                source="cache",
            ),
            [],
        )

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


def test_force_refresh_bypasses_cache_and_calls_provider(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.starts = 0
    manager = LyricsManager()
    key = manager._make_key("artist", "Song", "")

    manager._put_cache(
        key,
        LyricsData(
            artist="artist",
            title="Song",
            synced=False,
            lrc=None,
            plain_text="cached",
            source="cache",
        ),
        [],
    )

    _FakePrimaryThread.next_error = None
    _FakePrimaryThread.next_result = (
        "lrclib",
        LrcLibResult(track_name="Song", artist_name="artist", plain_lyrics="fresh"),
    )
    ready = []
    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.plain_text))

    manager.fetch_lyrics("artist", "Song", force_refresh=True)
    manager._on_fetch_done(
        _FakePrimaryThread.next_result,
        "artist",
        "Song",
        key,
        manager._req_id,
    )

    assert _FakePrimaryThread.starts == 1
    assert ready == ["fresh"]


def test_stale_success_and_error_signals_do_not_change_current_state(
    isolated_cache_dir, monkeypatch
):
    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", _FakePrimaryThread)
    _FakePrimaryThread.next_result = _FakePrimaryThread._NO_EMIT
    _FakePrimaryThread.next_error = None
    manager = LyricsManager()
    ready = []
    not_found = []

    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(
        key_b,
        LyricsData(
            artist="artist-b",
            title="Song B",
            synced=False,
            lrc=None,
            plain_text="cache-b",
            source="cache",
        ),
        [],
    )

    manager.lyrics_ready.connect(lambda result: ready.append(result.primary.title))
    manager.lyrics_not_found.connect(lambda a, t: not_found.append((a, t)))

    manager.fetch_lyrics("artist-a", "Song A")
    stale_req = manager._req_id
    manager.fetch_lyrics("artist-b", "Song B")

    manager._on_fetch_done(
        ("lrclib", LrcLibResult(track_name="Song A", artist_name="artist-a")),
        "artist-a",
        "Song A",
        manager._make_key("artist-a", "Song A", ""),
        stale_req,
    )
    manager._on_fetch_error("artist-a", "Song A", "", stale_req, "boom")

    assert ready == ["Song B"]
    assert not_found == []


if __name__ == "__main__":
    import traceback

    tests = [
        ("lrc_to_raw_preserves_offset_once", test_lrc_to_raw_preserves_offset_once),
        (
            "lrc_to_raw_preserves_enhanced_word_timing",
            test_lrc_to_raw_preserves_enhanced_word_timing,
        ),
        (
            "fetch_alt_thread_constructor_matches_fetch_alternatives_call_shape",
            test_fetch_alt_thread_constructor_matches_fetch_alternatives_call_shape,
        ),
        (
            "primary_thread_falls_back_to_lrclib_when_syncedlyrics_errors",
            test_primary_thread_falls_back_to_lrclib_when_syncedlyrics_errors,
        ),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
