import sys

sys.path.insert(0, "src")

from lyrics.manager import LyricsData, LyricsManager
import lyrics.manager as manager_module


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
