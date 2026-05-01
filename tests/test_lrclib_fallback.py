"""Unit tests for the title-only fallback in search_all (no network needed)."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "src")

from lyrics.lrclib import search_all


def _make_hit(track_id: int, track_name: str, artist_name: str) -> dict:
    return {
        "id": track_id,
        "trackName": track_name,
        "artistName": artist_name,
        "albumName": "",
        "duration": 240,
        "instrumental": False,
        "plainLyrics": "some lyrics",
        "syncedLyrics": "[00:01.00]some lyrics",
    }


def _make_response(json_data, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def test_search_all_uses_artist_first():
    """When artist+title returns results, those are returned and no retry happens."""
    hit = _make_hit(1, "ヒーロー", "FUNKY MONKEY BABY'S")

    with patch("lyrics.lrclib.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        # First search (artist+title) returns a hit; second GET by id returns details.
        search_resp = _make_response([hit])
        get_resp = _make_response(hit)
        client.get = AsyncMock(side_effect=[search_resp, get_resp])

        results = search_all("FUNKY MONKEY BABY'S", "ヒーロー")

    assert len(results) == 1
    assert results[0].track_name == "ヒーロー"
    # Only two calls: 1 search + 1 get/{id}
    assert client.get.call_count == 2


def test_search_all_falls_back_to_title_only_when_artist_empty():
    """When the artist+title search returns [], a title-only retry is made."""
    hit = _make_hit(42, "ヒーロー", "FUNKY MONKEY BABY'S")

    with patch("lyrics.lrclib.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        # First search (artist+title) → empty; second (title-only) → hit; third GET by id.
        empty_resp = _make_response([])
        search_resp = _make_response([hit])
        get_resp = _make_response(hit)
        client.get = AsyncMock(side_effect=[empty_resp, search_resp, get_resp])

        results = search_all("FUNKY MONKEY BΛBY'S", "ヒーロー")

    assert len(results) == 1
    assert results[0].track_name == "ヒーロー"
    assert results[0].artist_name == "FUNKY MONKEY BABY'S"
    # Three calls: artist search → empty, title-only search, get/{id}
    assert client.get.call_count == 3


def test_search_all_no_fallback_when_artist_is_empty():
    """When artist is empty, no double-search happens."""
    hit = _make_hit(7, "Let It Be", "The Beatles")

    with patch("lyrics.lrclib.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        search_resp = _make_response([hit])
        get_resp = _make_response(hit)
        client.get = AsyncMock(side_effect=[search_resp, get_resp])

        results = search_all("", "Let It Be")

    assert len(results) == 1
    assert client.get.call_count == 2  # 1 search + 1 get, no retry


def test_search_all_returns_empty_when_title_only_also_fails():
    """If both searches return empty, result is []."""
    with patch("lyrics.lrclib.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        empty = _make_response([])
        client.get = AsyncMock(side_effect=[empty, empty])

        results = search_all("SomeArtist", "zzzxxx_no_such_song")

    assert results == []
    assert client.get.call_count == 2  # artist search + title-only retry


if __name__ == "__main__":
    import traceback

    tests = [
        ("uses_artist_first", test_search_all_uses_artist_first),
        ("falls_back_title_only", test_search_all_falls_back_to_title_only_when_artist_empty),
        ("no_fallback_when_artist_empty", test_search_all_no_fallback_when_artist_is_empty),
        ("empty_when_title_only_fails", test_search_all_returns_empty_when_title_only_also_fails),
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
