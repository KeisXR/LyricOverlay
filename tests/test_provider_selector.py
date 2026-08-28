import sys

sys.path.insert(0, "src")

from lyrics.lrclib import LrcLibResult
from lyrics.syncedlyrics_client import SyncedLyricsResult
import lyrics.provider_selector as selector


def test_plain_syncedlyrics_does_not_block_lrclib_synced(monkeypatch):
    monkeypatch.setattr(
        selector,
        "get_syncedlyrics",
        lambda *args, **kwargs: SyncedLyricsResult(
            "Artist", "Song", None, "plain"
        ),
    )
    monkeypatch.setattr(
        selector,
        "get_lrclib",
        lambda *args, **kwargs: LrcLibResult(
            id=1,
            track_name="Song",
            artist_name="Artist",
            duration=180,
            synced_lyrics="[00:01.00]line",
        ),
    )
    monkeypatch.setattr(selector, "search_all", lambda *args, **kwargs: [])

    source, result = selector.select_primary_result(
        "Artist",
        "Song",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=True,
        syncedlyrics_enhanced=True,
    )
    assert source == "lrclib"
    assert result.synced_lyrics


def test_high_confidence_syncedlyrics_uses_fast_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        selector,
        "get_syncedlyrics",
        lambda *args, **kwargs: SyncedLyricsResult(
            "Artist", "Song", "[00:01.00]line", None
        ),
    )
    monkeypatch.setattr(
        selector,
        "get_lrclib",
        lambda *args, **kwargs: calls.append(args),
    )

    result = selector.select_primary_result(
        "Artist",
        "Song",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=True,
        syncedlyrics_enhanced=True,
    )
    assert result[0] == "syncedlyrics"
    assert calls == []


def test_wrong_artist_title_only_result_is_not_primary(monkeypatch):
    monkeypatch.setattr(selector, "get_syncedlyrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(selector, "get_lrclib", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        selector,
        "search_all",
        lambda *args, **kwargs: [
            LrcLibResult(
                id=2,
                track_name="Common Song",
                artist_name="Different Artist",
                duration=180,
                synced_lyrics="[00:01.00]wrong",
            )
        ],
    )

    assert (
        selector.select_primary_result(
            "Expected Artist",
            "Common Song",
            "",
            180000,
            use_lrclib=True,
            use_syncedlyrics=False,
            syncedlyrics_enhanced=True,
        )
        is None
    )


def test_lrclib_failure_keeps_collected_syncedlyrics_result(monkeypatch):
    monkeypatch.setattr(
        selector,
        "get_syncedlyrics",
        lambda *args, **kwargs: SyncedLyricsResult("Artist", "Song", None, "plain"),
    )

    def offline(*args, **kwargs):
        raise RuntimeError("connect timeout")

    monkeypatch.setattr(selector, "get_lrclib", offline)
    monkeypatch.setattr(selector, "search_all", offline)

    source, result = selector.select_primary_result(
        "Artist",
        "Song",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=True,
        syncedlyrics_enhanced=True,
    )
    assert source == "syncedlyrics"
    assert result.plain_lyrics == "plain"


def test_lrclib_search_failure_keeps_direct_result(monkeypatch):
    monkeypatch.setattr(selector, "get_syncedlyrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        selector,
        "get_lrclib",
        lambda *args, **kwargs: LrcLibResult(
            id=7,
            track_name="Song",
            artist_name="Artist",
            duration=180,
            plain_lyrics="plain",
        ),
    )

    def offline(*args, **kwargs):
        raise RuntimeError("search timeout")

    monkeypatch.setattr(selector, "search_all", offline)

    source, result = selector.select_primary_result(
        "Artist",
        "Song",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=True,
        syncedlyrics_enhanced=True,
    )
    assert source == "lrclib"
    assert result.id == 7


def test_ambiguous_metadata_denies_syncedlyrics_fast_path(monkeypatch):
    searched = []

    def synced(artist, title, **kwargs):
        searched.append((artist, title))
        return SyncedLyricsResult(artist, title, "[00:01.00]wrong", None)

    monkeypatch.setattr(selector, "get_syncedlyrics", synced)

    def direct(artist, title, *args):
        if (artist, title) == ("みゆはん", "ぼくのフレンド"):
            return LrcLibResult(
                id=11,
                track_name="ぼくのフレンド",
                artist_name="みゆはん",
                duration=180,
                synced_lyrics="[00:01.00]right",
            )
        return None

    monkeypatch.setattr(selector, "get_lrclib", direct)
    monkeypatch.setattr(selector, "search_all", lambda *args, **kwargs: [])

    source, result = selector.select_primary_result(
        "Steven Mak",
        "ぼくのフレンド / みゆはん",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=True,
        syncedlyrics_enhanced=True,
    )
    assert searched == [("Steven Mak", "ぼくのフレンド / みゆはん")]
    assert source == "lrclib"
    assert result.synced_lyrics == "[00:01.00]right"


def test_version_label_title_still_resolves_to_lyrics(monkeypatch):
    monkeypatch.setattr(selector, "get_syncedlyrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(selector, "get_lrclib", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        selector,
        "search_all",
        lambda *args, **kwargs: [
            LrcLibResult(
                id=12,
                track_name="ぼくのフレンド",
                artist_name="みゆはん",
                duration=180,
                synced_lyrics="[00:01.00]right",
            )
        ],
    )

    result = selector.select_primary_result(
        "みゆはん",
        "ぼくのフレンド（acoustic ver.）",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=False,
        syncedlyrics_enhanced=True,
    )
    assert result is not None
    assert result[0] == "lrclib"
    assert result[1].id == 12


def test_slash_fallback_query_can_find_real_artist(monkeypatch):
    calls = []
    monkeypatch.setattr(selector, "get_syncedlyrics", lambda *args, **kwargs: None)

    def direct(artist, title, *args):
        calls.append((artist, title))
        if (artist, title) == ("Real Artist", "Song"):
            return LrcLibResult(
                id=3,
                track_name="Song",
                artist_name="Real Artist",
                duration=180,
                synced_lyrics="[00:01.00]right",
            )
        return None

    monkeypatch.setattr(selector, "get_lrclib", direct)
    monkeypatch.setattr(selector, "search_all", lambda *args, **kwargs: [])

    result = selector.select_primary_result(
        "Uploader",
        "Song / Real Artist",
        "",
        180000,
        use_lrclib=True,
        use_syncedlyrics=False,
        syncedlyrics_enhanced=True,
    )
    assert ("Uploader", "Song / Real Artist") in calls
    assert ("Real Artist", "Song") in calls
    assert result[0] == "lrclib"
