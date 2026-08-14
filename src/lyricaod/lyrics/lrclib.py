"""LRClib API client — fetch synced and plain lyrics from lrclib.net."""

import asyncio
from dataclasses import dataclass

import httpx

LRCLIB_BASE = "https://lrclib.net/api"


@dataclass
class LrcLibResult:
    id: int = 0
    track_name: str = ""
    artist_name: str = ""
    album_name: str = ""
    duration: int = 0
    instrumental: bool = False
    plain_lyrics: str | None = None
    synced_lyrics: str | None = None


def _result_from_json(data: dict) -> LrcLibResult:
    return LrcLibResult(
        id=data.get("id", 0),
        track_name=data.get("trackName", ""),
        artist_name=data.get("artistName", ""),
        album_name=data.get("albumName", ""),
        duration=data.get("duration", 0),
        instrumental=data.get("instrumental", False),
        plain_lyrics=data.get("plainLyrics"),
        synced_lyrics=data.get("syncedLyrics"),
    )


def get_lrclib(
    artist_name: str,
    track_name: str,
    album_name: str = "",
    duration_ms: int = 0,
    timeout: float = 10.0,
) -> LrcLibResult | None:
    """Fetch the best-match lyrics from LRClib via a single direct GET /api/get.

    This is the fast path — one HTTP request, no search, no alternatives.
    """
    params: dict[str, str] = {
        "artist_name": artist_name,
        "track_name": track_name,
    }
    if album_name:
        params["album_name"] = album_name
    if duration_ms:
        params["duration"] = str(duration_ms // 1000)

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(f"{LRCLIB_BASE}/get", params=params)
        if resp.status_code == 200:
            return _result_from_json(resp.json())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return None


def search_all(
    artist_name: str,
    track_name: str,
    album_name: str = "",
    duration_ms: int = 0,
    timeout: float = 10.0,
    max_results: int = 6,
) -> list[LrcLibResult]:
    """Search for lyrics matches (including alternatives) from LRClib.

    Performs /api/search followed by parallel /api/get/{id} calls for
    each result.  Use :func:`get_lrclib` for the fast single-result path.
    """

    async def _fetch():
        async with httpx.AsyncClient(timeout=timeout) as client:
            search_params: dict[str, str] = {"track_name": track_name}
            if artist_name:
                search_params["artist_name"] = artist_name
            resp = await client.get(
                f"{LRCLIB_BASE}/search", params=search_params
            )
            resp.raise_for_status()
            hits = resp.json()

            # If the artist-scoped search returned nothing (e.g. because the
            # artist name contains non-ASCII chars like Greek Lambda Λ that
            # LRClib doesn't index), retry with the track name only.
            if artist_name and (not isinstance(hits, list) or not hits):
                print(
                    f"[LRClib] artist search empty, retrying title-only for"
                    f" \"{track_name}\""
                )
                resp = await client.get(
                    f"{LRCLIB_BASE}/search", params={"track_name": track_name}
                )
                resp.raise_for_status()
                hits = resp.json()

            if not isinstance(hits, list) or not hits:
                return []

            ordered = []
            for h in hits:
                if h.get("id"):
                    ordered.append(h)
                if len(ordered) >= max_results:
                    break

            if not ordered:
                return []

            fetch_tasks = [
                client.get(f"{LRCLIB_BASE}/get/{entry['id']}")
                for entry in ordered
            ]
            responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            results: list[LrcLibResult] = []
            for resp in responses:
                if isinstance(resp, BaseException):
                    continue
                try:
                    resp.raise_for_status()
                    results.append(_result_from_json(resp.json()))
                except httpx.HTTPError:
                    continue
                if len(results) >= max_results:
                    break

            return results

    return asyncio.run(_fetch())
