import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "src")

from lyrics.lrclib import search_all


def response(data):
    result = MagicMock()
    result.json.return_value = data
    result.raise_for_status = MagicMock()
    return result


def hit(track_id, artist, duration, album="Album"):
    return {
        "id": track_id,
        "trackName": "Song",
        "artistName": artist,
        "albumName": album,
        "duration": duration,
        "instrumental": False,
        "plainLyrics": "plain",
        "syncedLyrics": "[00:01.00]line",
    }


def test_search_ranks_metadata_before_detail_fetches():
    wrong = hit(1, "Wrong Artist", 250)
    right = hit(2, "Artist", 180)
    with patch("lyrics.lrclib.httpx.AsyncClient") as factory:
        client = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=client)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(
            side_effect=[response([wrong, right]), response(right), response(wrong)]
        )

        results = search_all(
            "Artist", "Song", "Album", 180000, max_results=2
        )

    assert [result.id for result in results] == [2, 1]
    assert client.get.call_args_list[0].kwargs["params"]["album_name"] == "Album"
    assert "/get/2" in client.get.call_args_list[1].args[0]
