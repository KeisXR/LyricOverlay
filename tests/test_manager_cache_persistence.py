import sys

sys.path.insert(0, "src")

from PySide6.QtCore import QObject, Signal

from lyrics.lrclib import LrcLibResult
from lyrics.manager import LyricsData, LyricsManager
import lyrics.manager as manager_module


class _SignalProxy(QObject):
    finished_ok = Signal(object)
    finished_error = Signal(str)
    finished = Signal()


class _RecordingPrimaryThread:
    """Fake primary worker that records fetches instead of hitting the network."""

    starts: list[tuple[str, str]] = []

    def __init__(self, artist, title, *args, **kwargs):
        proxy = _SignalProxy()
        self.finished_ok = proxy.finished_ok
        self.finished_error = proxy.finished_error
        self.finished = proxy.finished
        self._proxy = proxy
        type(self).starts.append((artist, title))

    def start(self):
        pass

    def isRunning(self):
        return False

    def cancel(self):
        pass

    def deleteLater(self):
        pass


def _use_recording_worker(monkeypatch):
    monkeypatch.setattr(
        manager_module, "_FetchPrimaryThread", _RecordingPrimaryThread
    )
    _RecordingPrimaryThread.starts = []
    return _RecordingPrimaryThread.starts


def lyrics_data(
    title,
    *,
    source="lrclib",
    raw="plain",
    provider_id="1",
    synced=False,
):
    return LyricsData(
        "Artist",
        title,
        synced,
        None,
        None if synced else raw,
        source,
        raw,
        provider_id,
    )


def test_selected_alternative_persists_and_swap_is_reversible(tmp_path):
    path = tmp_path / "cache.db"
    manager = LyricsManager(cache_path=path)
    key = manager._make_key("Artist", "Song", "", "Album", 180000)
    primary = lyrics_data("Song", provider_id="primary")
    alternative = lyrics_data("Song alt", provider_id="alt")
    manager._put_cache(key, primary, [alternative])
    manager.shutdown()

    manager = LyricsManager(cache_path=path)
    ready = []
    manager.lyrics_ready.connect(ready.append)
    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000)
    assert ready[-1].primary.title == "Song"
    assert manager.select_alternative(0).title == "Song alt"
    manager.shutdown()

    manager = LyricsManager(cache_path=path)
    ready = []
    manager.lyrics_ready.connect(ready.append)
    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000)
    assert ready[-1].primary.title == "Song alt"
    assert [item.title for item in ready[-1].alternatives] == ["Song"]
    manager.shutdown()


def test_positive_cache_is_available_with_all_sources_disabled(tmp_path):
    manager = LyricsManager(
        cache_path=tmp_path / "cache.db",
        lrclib_enabled=False,
        syncedlyrics_enabled=False,
    )
    key = manager._make_key("Artist", "Song", "", "", 0)
    manager._put_cache(key, lyrics_data("Song"), [])
    ready = []
    manager.lyrics_ready.connect(ready.append)

    manager.fetch_lyrics("Artist", "Song")

    assert ready[-1].primary.title == "Song"
    manager.shutdown()


def test_negative_cache_suppresses_normal_fetch_but_force_refresh_bypasses(
    tmp_path, monkeypatch
):
    manager = LyricsManager(cache_path=tmp_path / "cache.db")
    key = manager._make_key("Artist", "Missing", "", "", 0)
    aliases = manager._aliases("Artist", "Missing", "")
    manager._cache.put_negative(key, ttl_seconds=100, aliases=aliases)
    starts = []

    class Worker:
        def __init__(self, *args, **kwargs):
            from PySide6.QtCore import QObject, Signal

            class Proxy(QObject):
                finished_ok = Signal(object)
                finished_error = Signal(str)
                finished = Signal()

            proxy = Proxy()
            self.finished_ok = proxy.finished_ok
            self.finished_error = proxy.finished_error
            self.finished = proxy.finished
            self._proxy = proxy
            starts.append(1)

        def start(self):
            pass

        def isRunning(self):
            return False

        def deleteLater(self):
            pass

    monkeypatch.setattr(manager_module, "_FetchPrimaryThread", Worker)
    missing = []
    manager.lyrics_not_found.connect(lambda _artist, title: missing.append(title))

    manager.fetch_lyrics("Artist", "Missing")
    assert missing == ["Missing"]
    assert starts == []

    manager.fetch_lyrics("Artist", "Missing", force_refresh=True)
    assert starts == [1]
    manager.shutdown()


def test_constant_trackid_does_not_leak_cache_between_tracks(tmp_path, monkeypatch):
    starts = _use_recording_worker(monkeypatch)
    manager = LyricsManager(cache_path=tmp_path / "cache.db")
    key_a = manager._make_key("Artist A", "Song A", "browser-ws", "Album A", 180000)
    aliases_a = manager._aliases("Artist A", "Song A", "browser-ws")
    manager._put_cache(key_a, lyrics_data("Song A"), [], aliases_a)
    ready = []
    manager.lyrics_ready.connect(ready.append)

    manager.fetch_lyrics("Artist A", "Song A", "Album A", "browser-ws", 180000)
    assert [item.primary.title for item in ready] == ["Song A"]
    assert starts == []

    manager.fetch_lyrics("Artist B", "Song B", "Album B", "browser-ws", 200000)

    assert [item.primary.title for item in ready] == ["Song A"]
    assert starts == [("Artist B", "Song B")]
    manager.shutdown()


def test_constant_trackid_negative_cache_does_not_block_other_tracks(
    tmp_path, monkeypatch
):
    starts = _use_recording_worker(monkeypatch)
    manager = LyricsManager(cache_path=tmp_path / "cache.db")
    missing = []
    manager.lyrics_not_found.connect(lambda _artist, title: missing.append(title))

    manager.fetch_lyrics("Artist A", "Missing A", "Album A", "browser-ws", 180000)
    manager._on_fetch_done(
        None,
        "Artist A",
        "Missing A",
        manager._make_key("Artist A", "Missing A", "browser-ws", "Album A", 180000),
        manager._req_id,
        manager._aliases("Artist A", "Missing A", "browser-ws"),
    )
    assert missing == ["Missing A"]

    manager.fetch_lyrics("Artist B", "Song B", "Album B", "browser-ws", 200000)

    assert missing == ["Missing A"]
    assert starts == [("Artist A", "Missing A"), ("Artist B", "Song B")]
    manager.shutdown()


def test_legacy_v2_alias_still_resolves_after_alias_restriction(tmp_path):
    manager = LyricsManager(cache_path=tmp_path / "cache.db")
    key = manager._make_key("Artist", "Song", "org/mpris/1", "Album", 180000)
    aliases = manager._aliases("Artist", "Song", "org/mpris/1")
    manager._put_cache(key, lyrics_data("Song"), [], aliases)

    record = manager._cache.get(
        "unknown-key",
        aliases=(manager._legacy_key("Artist", "Song", "org/mpris/1"),),
    )

    assert record is not None
    assert record.key == key
    manager.shutdown()


def test_force_refresh_keeps_selected_candidate_and_alternatives(
    tmp_path, monkeypatch
):
    _use_recording_worker(monkeypatch)
    provider_result = LrcLibResult(
        id=1,
        track_name="Song",
        artist_name="Artist",
        plain_lyrics="plain",
    )
    path = tmp_path / "cache.db"
    manager = LyricsManager(cache_path=path)
    key = manager._make_key("Artist", "Song", "", "Album", 180000)
    aliases = manager._aliases("Artist", "Song", "")
    rejected = manager._convert_lrclib_result(provider_result)
    chosen = lyrics_data("Song alt", provider_id="alt")
    manager._put_cache(key, rejected, [chosen], aliases)
    ready = []
    manager.lyrics_ready.connect(ready.append)

    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000)
    assert manager.select_alternative(0).title == "Song alt"

    cached_result, _negative = manager._load_record(key, aliases)
    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000, force_refresh=True)
    manager._on_fetch_done(
        ("lrclib", provider_result),
        "Artist",
        "Song",
        key,
        manager._req_id,
        aliases,
        cached_result,
        True,
    )

    assert ready[-1].primary.title == "Song alt"
    assert [item.title for item in ready[-1].alternatives] == ["Song"]
    manager.shutdown()

    manager = LyricsManager(cache_path=path)
    reopened = []
    manager.lyrics_ready.connect(reopened.append)
    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000)

    assert reopened[-1].primary.title == "Song alt"
    assert [item.title for item in reopened[-1].alternatives] == ["Song"]
    manager.shutdown()


def test_force_refresh_adds_new_primary_as_alternative(tmp_path, monkeypatch):
    _use_recording_worker(monkeypatch)
    manager = LyricsManager(cache_path=tmp_path / "cache.db")
    key = manager._make_key("Artist", "Song", "", "Album", 180000)
    aliases = manager._aliases("Artist", "Song", "")
    manager._put_cache(key, lyrics_data("Song", provider_id="old"), [], aliases)
    ready = []
    manager.lyrics_ready.connect(ready.append)

    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000)
    cached_result, _negative = manager._load_record(key, aliases)
    manager.fetch_lyrics("Artist", "Song", "Album", "", 180000, force_refresh=True)
    manager._on_fetch_done(
        (
            "lrclib",
            LrcLibResult(
                id=7,
                track_name="Song remaster",
                artist_name="Artist",
                plain_lyrics="fresh",
            ),
        ),
        "Artist",
        "Song",
        key,
        manager._req_id,
        aliases,
        cached_result,
        True,
    )

    assert ready[-1].primary.title == "Song"
    assert [item.title for item in ready[-1].alternatives] == ["Song remaster"]
    manager.shutdown()


def test_raw_synced_lyrics_are_stored_verbatim(tmp_path):
    raw = "[re:unknown]\n[00:01.123]line"
    manager = LyricsManager(cache_path=tmp_path / "cache.db")
    item = lyrics_data(
        "Song",
        raw=raw,
        provider_id="raw",
        synced=True,
    )
    key = manager._make_key("Artist", "Song")
    manager._put_cache(key, item, [])

    record = manager._cache.get(key)

    assert record.payload["candidates"][0]["raw_lyrics"] == raw
    manager.shutdown()
