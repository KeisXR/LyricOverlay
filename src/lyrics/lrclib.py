"""LRClib API client for synced and plain lyrics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .ranking import CandidateMetadata, TrackQuery, rank_candidate

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


def _metadata_rank(
    hit: dict,
    *,
    artist_name: str,
    track_name: str,
    album_name: str,
    duration_ms: int,
) -> tuple:
    result = _result_from_json(hit)
    ranked = rank_candidate(
        TrackQuery(artist_name, track_name, album_name, duration_ms),
        CandidateMetadata(
            provider="lrclib",
            provider_id=str(result.id),
            artist=result.artist_name,
            title=result.track_name,
            album=result.album_name,
            duration_ms=max(0, int(result.duration or 0) * 1000),
            synced=bool(result.synced_lyrics),
            plain=bool(result.plain_lyrics),
            instrumental=bool(result.instrumental),
        ),
    )
    return ranked.acceptable, ranked.score, result.id


def get_lrclib(
    artist_name: str,
    track_name: str,
    album_name: str = "",
    duration_ms: int = 0,
    timeout: float = 10.0,
) -> LrcLibResult | None:
    """Fetch the direct LRClib match via one ``GET /api/get`` request."""
    params: dict[str, str] = {
        "artist_name": artist_name,
        "track_name": track_name,
    }
    if album_name:
        params["album_name"] = album_name
    if duration_ms:
        params["duration"] = str(duration_ms // 1000)

    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{LRCLIB_BASE}/get", params=params)
        if response.status_code == 200:
            return _result_from_json(response.json())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return None


def search_all(
    artist_name: str,
    track_name: str,
    album_name: str = "",
    duration_ms: int = 0,
    timeout: float = 10.0,
    max_results: int = 6,
) -> list[LrcLibResult]:
    """Search, rank, and fetch alternative LRClib matches.

    Search hits are ranked locally with artist, title, album, duration, and
    lyrics quality before the more expensive ``GET /api/get/{id}`` requests.
    If an artist-scoped search is empty, a title-only retry is made, but its
    results still pass through the same ranking rather than using API order.
    """

    async def _fetch():
        async with httpx.AsyncClient(timeout=timeout) as client:
            search_params: dict[str, str] = {"track_name": track_name}
            if artist_name:
                search_params["artist_name"] = artist_name
            if album_name:
                search_params["album_name"] = album_name

            response = await client.get(
                f"{LRCLIB_BASE}/search", params=search_params
            )
            response.raise_for_status()
            hits = response.json()

            if artist_name and (not isinstance(hits, list) or not hits):
                fallback_params = {"track_name": track_name}
                if album_name:
                    fallback_params["album_name"] = album_name
                response = await client.get(
                    f"{LRCLIB_BASE}/search", params=fallback_params
                )
                response.raise_for_status()
                hits = response.json()

            if not isinstance(hits, list) or not hits:
                return []

            valid_hits = [hit for hit in hits if isinstance(hit, dict) and hit.get("id")]
            valid_hits.sort(
                key=lambda hit: _metadata_rank(
                    hit,
                    artist_name=artist_name,
                    track_name=track_name,
                    album_name=album_name,
                    duration_ms=duration_ms,
                ),
                reverse=True,
            )
            ordered = valid_hits[: max(0, max_results)]
            if not ordered:
                return []

            responses = await asyncio.gather(
                *[
                    client.get(f"{LRCLIB_BASE}/get/{entry['id']}")
                    for entry in ordered
                ],
                return_exceptions=True,
            )

            results: list[LrcLibResult] = []
            for item in responses:
                if isinstance(item, BaseException):
                    continue
                try:
                    item.raise_for_status()
                    data = item.json()
                    if isinstance(data, dict):
                        results.append(_result_from_json(data))
                except (httpx.HTTPError, ValueError, TypeError):
                    continue
            return results

    return asyncio.run(_fetch())
