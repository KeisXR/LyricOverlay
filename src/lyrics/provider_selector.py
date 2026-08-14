"""Collect and rank primary lyrics results from enabled providers."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from meta_utils import search_query_candidates

from .lrclib import LrcLibResult, get_lrclib, search_all
from .ranking import CandidateMetadata, RankedCandidate, TrackQuery, rank_candidates
from .syncedlyrics_client import SyncedLyricsResult, get_syncedlyrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderResult:
    source: str
    payload: object
    metadata: CandidateMetadata


def _syncedlyrics_candidate(result: SyncedLyricsResult) -> CandidateMetadata:
    return CandidateMetadata(
        provider="syncedlyrics",
        provider_id=f"{result.artist_name}|{result.track_name}",
        artist=result.artist_name,
        title=result.track_name,
        synced=bool(result.synced_lyrics),
        plain=bool(result.plain_lyrics),
    )


def _lrclib_candidate(result: LrcLibResult) -> CandidateMetadata:
    return CandidateMetadata(
        provider="lrclib",
        provider_id=str(result.id or f"{result.artist_name}|{result.track_name}"),
        artist=result.artist_name,
        title=result.track_name,
        album=result.album_name,
        duration_ms=max(0, int(result.duration or 0) * 1000),
        synced=bool(result.synced_lyrics),
        plain=bool(result.plain_lyrics),
        instrumental=bool(result.instrumental),
    )


def _queries(
    artist: str, title: str, album: str, duration_ms: int
) -> list[TrackQuery]:
    return [
        TrackQuery(
            artist=query_artist,
            title=query_title,
            album=album,
            duration_ms=duration_ms,
        )
        for query_artist, query_title in search_query_candidates(artist, title)
    ]


def _pick(
    queries: list[TrackQuery],
    results: list[ProviderResult],
) -> tuple[ProviderResult | None, list[RankedCandidate]]:
    by_id = {
        (item.metadata.provider, item.metadata.provider_id): item for item in results
    }
    ranked = rank_candidates(queries, [item.metadata for item in results])
    for candidate in ranked:
        logger.debug(
            "lyrics candidate provider=%s id=%s score=%.2f acceptable=%s "
            "query_artist=%r query_title=%r reasons=%s",
            candidate.candidate.provider,
            candidate.candidate.provider_id,
            candidate.score,
            candidate.acceptable,
            candidate.matched_query.artist,
            candidate.matched_query.title,
            ",".join(candidate.reasons),
        )
        if candidate.acceptable:
            key = (candidate.candidate.provider, candidate.candidate.provider_id)
            return by_id[key], ranked
    return None, ranked


def _append_unique(results: list[ProviderResult], candidate: ProviderResult) -> None:
    if any(
        existing.source == candidate.source
        and existing.metadata.provider_id == candidate.metadata.provider_id
        for existing in results
    ):
        return
    results.append(candidate)


def select_primary_result(
    artist: str,
    title: str,
    album: str,
    duration_ms: int,
    *,
    use_lrclib: bool,
    use_syncedlyrics: bool,
    syncedlyrics_enhanced: bool,
    cancelled=lambda: False,
) -> tuple[str, object] | None:
    """Return the highest-quality acceptable provider result.

    A high-confidence synced result may use the fast path. A plain result never
    stops provider traversal, so a later provider can still supply synced
    lyrics. Search variants are used for lookup and ranking without mutating the
    canonical metadata received from the player.
    """
    queries = _queries(artist, title, album, duration_ms)
    collected: list[ProviderResult] = []

    if use_syncedlyrics:
        for query in queries:
            if cancelled():
                return None
            try:
                result = get_syncedlyrics(
                    query.artist,
                    query.title,
                    enhanced=syncedlyrics_enhanced,
                )
            except Exception as exc:
                logger.warning("Syncedlyrics provider failed: %s", exc)
                result = None
            if not result:
                continue
            provider_result = ProviderResult(
                "syncedlyrics", result, _syncedlyrics_candidate(result)
            )
            _append_unique(collected, provider_result)
            ranked = rank_candidates(queries, [provider_result.metadata])[0]
            if result.synced_lyrics and ranked.acceptable and ranked.score >= 90.0:
                return "syncedlyrics", result
            if result.synced_lyrics:
                break

    if use_lrclib:
        direct_synced_found = False
        for query in queries:
            if cancelled():
                return None
            direct = get_lrclib(
                query.artist,
                query.title,
                query.album,
                query.duration_ms,
            )
            if direct:
                _append_unique(
                    collected,
                    ProviderResult("lrclib", direct, _lrclib_candidate(direct)),
                )
                direct_synced_found = direct_synced_found or bool(direct.synced_lyrics)

        selected, _ranked = _pick(queries, collected)
        if selected and selected.metadata.synced and direct_synced_found:
            return selected.source, selected.payload

        for query in queries:
            if cancelled():
                return None
            for result in search_all(
                query.artist,
                query.title,
                query.album,
                query.duration_ms,
                max_results=6,
            ):
                _append_unique(
                    collected,
                    ProviderResult("lrclib", result, _lrclib_candidate(result)),
                )

    if cancelled():
        return None
    selected, _ranked = _pick(queries, collected)
    return (selected.source, selected.payload) if selected else None


def rank_lrclib_results(
    artist: str,
    title: str,
    album: str,
    duration_ms: int,
    results: list[LrcLibResult],
) -> list[LrcLibResult]:
    queries = _queries(artist, title, album, duration_ms)
    wrapped = [
        ProviderResult("lrclib", item, _lrclib_candidate(item)) for item in results
    ]
    by_id = {item.metadata.provider_id: item.payload for item in wrapped}
    ranked = rank_candidates(queries, [item.metadata for item in wrapped])
    return [by_id[item.candidate.provider_id] for item in ranked]
