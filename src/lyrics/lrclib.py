"""LRClib API client — fetch synced and plain lyrics from lrclib.net."""

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
    """Fetch the best-match lyrics from LRClib.  For multiple results use
    :func:`search_all`."""
    results = search_all(artist_name, track_name, album_name, duration_ms, timeout, max_results=1)
    return results[0] if results else None


def search_all(
    artist_name: str,
    track_name: str,
    album_name: str = "",
    duration_ms: int = 0,
    timeout: float = 10.0,
    max_results: int = 6,
) -> list[LrcLibResult]:
    """Fetch up to *max_results* lyrics matches from LRClib.

    Each result includes full ``synced_lyrics`` / ``plain_lyrics`` so
    callers can show previews without extra requests.
    """
    with httpx.Client(timeout=timeout) as client:
        # 1. Direct get (best match when artist is known)
        direct_hit: LrcLibResult | None = None
        if artist_name:
            params: dict[str, str] = {
                "artist_name": artist_name,
                "track_name": track_name,
            }
            if album_name:
                params["album_name"] = album_name
            if duration_ms:
                params["duration"] = str(duration_ms // 1000)

            resp = client.get(f"{LRCLIB_BASE}/get", params=params)
            if resp.status_code == 200:
                direct_hit = _result_from_json(resp.json())
            elif resp.status_code != 404:
                resp.raise_for_status()

        # 2. Search (always, so we can build the alternatives list)
        search_params: dict[str, str] = {"track_name": track_name}
        if artist_name:
            search_params["artist_name"] = artist_name
        resp = client.get(f"{LRCLIB_BASE}/search", params=search_params)
        resp.raise_for_status()
        hits = resp.json()
        if not isinstance(hits, list) or not hits:
            return [direct_hit] if direct_hit else []

        # Collect IDs — direct hit first (deduplicated)
        seen_ids: set[int] = set()
        ordered: list[dict] = []
        if direct_hit and direct_hit.id:
            seen_ids.add(direct_hit.id)
            ordered.append({
                "id": direct_hit.id,
                "trackName": direct_hit.track_name,
                "artistName": direct_hit.artist_name,
            })

        for h in hits:
            rid = h.get("id", 0)
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                ordered.append(h)
            if len(ordered) >= max_results:
                break

        # 3. Fetch full lyrics for each ID
        results: list[LrcLibResult] = []
        for entry in ordered:
            try:
                r = client.get(f"{LRCLIB_BASE}/get/{entry['id']}")
                r.raise_for_status()
                results.append(_result_from_json(r.json()))
            except httpx.HTTPError:
                # Skip broken entries; keep going
                continue
            if len(results) >= max_results:
                break

        return results
