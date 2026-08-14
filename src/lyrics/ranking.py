"""Pure metadata matching and lyrics-candidate ranking helpers."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class TrackQuery:
    artist: str
    title: str
    album: str = ""
    duration_ms: int = 0


@dataclass(frozen=True)
class CandidateMetadata:
    provider: str
    provider_id: str
    artist: str
    title: str
    album: str = ""
    duration_ms: int = 0
    synced: bool = False
    plain: bool = False
    instrumental: bool = False


@dataclass(frozen=True)
class RankedCandidate:
    candidate: CandidateMetadata
    score: float
    acceptable: bool
    reasons: tuple[str, ...]
    matched_query: TrackQuery


def normalise_match_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = _PUNCT_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def text_similarity(left: str, right: str) -> float:
    a = normalise_match_text(left)
    b = normalise_match_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def artist_similarity(left: str, right: str) -> float:
    a = normalise_match_text(left)
    b = normalise_match_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sequence = SequenceMatcher(None, a, b).ratio()
    left_tokens = set(a.split())
    right_tokens = set(b.split())
    union = left_tokens | right_tokens
    token_overlap = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return sequence * 0.55 + token_overlap * 0.45


def duration_similarity(query_ms: int, candidate_ms: int) -> float:
    if query_ms <= 0 or candidate_ms <= 0:
        return 0.5
    delta = abs(query_ms - candidate_ms)
    if delta <= 2000:
        return 1.0
    if delta >= 30000:
        return 0.0
    return 1.0 - (delta - 2000) / 28000


def rank_candidate(query: TrackQuery, candidate: CandidateMetadata) -> RankedCandidate:
    title_sim = text_similarity(query.title, candidate.title)
    artist_sim = artist_similarity(query.artist, candidate.artist) if query.artist else 1.0
    album_sim = (
        text_similarity(query.album, candidate.album)
        if query.album and candidate.album
        else 0.5
    )
    duration_sim = duration_similarity(query.duration_ms, candidate.duration_ms)

    quality_bonus = (
        12.0
        if candidate.synced
        else 4.0
        if candidate.plain
        else 2.0
        if candidate.instrumental
        else 0.0
    )
    score = (
        title_sim * 48.0
        + artist_sim * 28.0
        + duration_sim * 9.0
        + album_sim * 3.0
        + quality_bonus
    )
    reasons = [
        f"title={title_sim:.2f}",
        f"artist={artist_sim:.2f}",
        f"duration={duration_sim:.2f}",
        f"album={album_sim:.2f}",
        "synced"
        if candidate.synced
        else "plain"
        if candidate.plain
        else "instrumental"
        if candidate.instrumental
        else "empty",
    ]

    acceptable = title_sim >= 0.62 and score >= 62.0
    if query.artist and artist_sim < 0.58:
        acceptable = False
        reasons.append("artist-below-threshold")
    if query.duration_ms > 0 and candidate.duration_ms > 0 and duration_sim == 0.0:
        acceptable = False
        reasons.append("duration-below-threshold")
    if not (candidate.synced or candidate.plain or candidate.instrumental):
        acceptable = False
        reasons.append("no-lyrics-payload")
    return RankedCandidate(
        candidate=candidate,
        score=round(score, 4),
        acceptable=acceptable,
        reasons=tuple(reasons),
        matched_query=query,
    )


def rank_against_queries(
    queries: Sequence[TrackQuery], candidate: CandidateMetadata
) -> RankedCandidate:
    if not queries:
        raise ValueError("at least one TrackQuery is required")
    ranked = [rank_candidate(query, candidate) for query in queries]
    return max(
        ranked,
        key=lambda item: (
            item.acceptable,
            item.score,
            item.candidate.synced,
        ),
    )


def rank_candidates(
    query_or_queries: TrackQuery | Sequence[TrackQuery],
    candidates: list[CandidateMetadata],
) -> list[RankedCandidate]:
    queries = (
        [query_or_queries]
        if isinstance(query_or_queries, TrackQuery)
        else list(query_or_queries)
    )
    deduplicated: dict[tuple, RankedCandidate] = {}
    for candidate in candidates:
        ranked = rank_against_queries(queries, candidate)
        key = (
            normalise_match_text(candidate.artist),
            normalise_match_text(candidate.title),
            normalise_match_text(candidate.album),
            round(candidate.duration_ms / 1000) if candidate.duration_ms else 0,
            candidate.synced,
            candidate.plain,
            candidate.instrumental,
        )
        previous = deduplicated.get(key)
        if previous is None or ranked.score > previous.score:
            deduplicated[key] = ranked
    return sorted(
        deduplicated.values(),
        key=lambda item: (
            item.acceptable,
            item.score,
            item.candidate.synced,
            item.candidate.provider == "lrclib",
            item.candidate.provider_id,
        ),
        reverse=True,
    )
