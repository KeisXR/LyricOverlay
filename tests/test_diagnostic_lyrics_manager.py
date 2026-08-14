import sys
from pathlib import Path

sys.path.insert(0, "src")

from diagnostic_lyrics_manager import DiagnosticLyricsManager
from lyrics.manager import LyricsData
import lyrics.manager as manager_module


def test_error_code_classification():
    classify = DiagnosticLyricsManager._classify_error
    assert classify("HTTP 429 Too Many Requests") == "rate_limited"
    assert classify("ReadTimeout") == "network_timeout"
    assert classify("DNS connection failed") == "network_error"
    assert classify("unexpected provider response") == "provider_error"


def test_sources_disabled_emit_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_module, "CACHE_DIR", Path(tmp_path))
    manager = DiagnosticLyricsManager(
        lrclib_enabled=False,
        syncedlyrics_enabled=False,
    )
    errors = []
    manager.lyrics_error.connect(lambda *args: errors.append(args))

    manager.fetch_lyrics("Artist", "Song")

    assert errors[-1][2] == "sources_disabled"
    assert manager.delivery_status == "sources_disabled"


def test_sources_disabled_can_still_show_cached_lyrics(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_module, "CACHE_DIR", Path(tmp_path))
    manager = DiagnosticLyricsManager(
        lrclib_enabled=False,
        syncedlyrics_enabled=False,
    )
    key = manager._make_key("Artist", "Song", "")
    manager._put_cache(
        key,
        LyricsData("Artist", "Song", False, None, "lyrics", "lrclib"),
        [],
    )
    ready = []
    manager.lyrics_ready.connect(ready.append)

    manager.fetch_lyrics("Artist", "Song")

    assert ready[-1].primary.title == "Song"
    assert manager.delivery_status == "cache_hit"
