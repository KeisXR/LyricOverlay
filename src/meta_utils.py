"""Metadata normalisation and search-query helpers.

Canonical player metadata is cleaned conservatively. Potentially destructive
interpretations, such as treating ``"title / artist"`` as two fields, are
returned as additional search candidates instead of replacing a valid artist
and title.
"""

from __future__ import annotations

import re

_TOPIC_RE = re.compile(r"\s*-\s*Topic$", re.IGNORECASE)
_SITE_SUFFIX_RE = re.compile(
    r"\s*(?:\|\s*(?:YouTube(?: Music)?|Spotify)|-\s*YouTube)$",
    re.IGNORECASE,
)
_VIDEO_NOISE_RE = re.compile(
    r"\s*[\[(]\s*(?:"
    r"official\s+(?:music\s+)?video|"
    r"official\s+audio|"
    r"music\s+video|"
    r"lyric\s+video|"
    r"lyrics?"
    r")\s*[\])]\s*$",
    re.IGNORECASE,
)
_TITLE_NOISE_WORD_RE = re.compile(
    r"\b(?:official|video|lyrics?|youtube|spotify)\b",
    re.IGNORECASE,
)
_ARTIST_TITLE_RE = re.compile(r"^(.{1,80}?)\s+[-–—]\s+(.{2,120})$")
_TRACK_LENGTH_TOLERANCE_MS = 3000
_PLACEHOLDER_TITLES = {"youtube music", "youtube", "spotify"}


def _strip_repeated_artist_prefix(artist: str, title: str) -> str:
    for separator in (" - ", " – ", " — "):
        prefix = f"{artist}{separator}"
        if title.casefold().startswith(prefix.casefold()):
            stripped = title[len(prefix):].strip()
            if stripped:
                return stripped
    return title


def _sub_strip_if_changed(pattern: re.Pattern, value: str) -> str:
    cleaned = pattern.sub("", value)
    return cleaned.strip() if cleaned != value else value


def _valid_slash_candidate(title: str) -> tuple[str, str] | None:
    if " / " not in title:
        return None
    title_part, artist_part = title.split(" / ", 1)
    title_part = title_part.strip()
    artist_part = artist_part.strip()
    if (
        title_part
        and artist_part
        and 2 <= len(title_part) <= 120
        and 1 <= len(artist_part) <= 80
        and not _TITLE_NOISE_WORD_RE.search(artist_part)
    ):
        return artist_part, title_part
    return None


def _is_placeholder_trackid(value: str) -> bool:
    return not value or value == "/"


def _is_same_track_for_enrichment(base: dict, candidate: dict) -> bool:
    base_trackid = str(base.get("trackid", ""))
    candidate_trackid = str(candidate.get("trackid", ""))
    if (
        not _is_placeholder_trackid(base_trackid)
        and not _is_placeholder_trackid(candidate_trackid)
        and base_trackid == candidate_trackid
    ):
        return True

    base_title = str(base.get("title", "")).strip().casefold()
    candidate_title = str(candidate.get("title", "")).strip().casefold()
    if not base_title or base_title != candidate_title:
        return False

    base_length = int(base.get("length_ms", 0) or 0)
    candidate_length = int(candidate.get("length_ms", 0) or 0)
    if (
        base_length > 0
        and candidate_length > 0
        and abs(base_length - candidate_length) > _TRACK_LENGTH_TOLERANCE_MS
    ):
        return False

    base_album = str(base.get("album", "")).strip().casefold()
    candidate_album = str(candidate.get("album", "")).strip().casefold()
    if base_album and candidate_album and base_album != candidate_album:
        return False
    return True


def enrich_missing_meta(
    base_meta: dict, candidate_metas: list[dict]
) -> tuple[dict, bool]:
    """Fill missing artist/album/title from another view of the same track."""
    artist = str(base_meta.get("artist", "")).strip()
    album = str(base_meta.get("album", "")).strip()
    title = str(base_meta.get("title", "")).strip()
    if artist and album and title:
        return base_meta, False

    best = None
    best_score = -1
    for candidate in candidate_metas:
        if not candidate or not _is_same_track_for_enrichment(base_meta, candidate):
            continue
        score = 0
        if not artist and str(candidate.get("artist", "")).strip():
            score += 2
        if not album and str(candidate.get("album", "")).strip():
            score += 1
        if not title and str(candidate.get("title", "")).strip():
            score += 1
        if score > best_score:
            best = candidate
            best_score = score

    if not best or best_score <= 0:
        return base_meta, False

    merged = dict(base_meta)
    if not artist:
        merged["artist"] = str(best.get("artist", "")).strip()
    if not album:
        merged["album"] = str(best.get("album", "")).strip()
    if not title:
        merged["title"] = str(best.get("title", "")).strip()
    return merged, True


def normalise_yt_meta(artist: str, title: str) -> tuple[str, str]:
    """Conservatively clean browser/player noise from canonical metadata."""
    artist = _TOPIC_RE.sub("", artist or "").strip()
    title = _sub_strip_if_changed(_SITE_SUFFIX_RE, title or "")
    title = _sub_strip_if_changed(_VIDEO_NOISE_RE, title)

    if artist:
        title = _strip_repeated_artist_prefix(artist, title)
    else:
        match = _ARTIST_TITLE_RE.match(title)
        if match:
            artist_part = match.group(1).strip()
            title_part = match.group(2).strip()
            if (
                artist_part
                and title_part
                and not _TITLE_NOISE_WORD_RE.search(artist_part)
                and not _TITLE_NOISE_WORD_RE.search(title_part)
            ):
                artist = artist_part
                title = title_part

    if not artist and title.strip().casefold() in _PLACEHOLDER_TITLES:
        return "", ""

    # Only replace canonical metadata when artist is absent. When an artist is
    # already present, slash parsing is exposed by search_query_candidates().
    if not artist:
        slash_candidate = _valid_slash_candidate(title)
        if slash_candidate:
            artist, title = slash_candidate

    return artist, title


def search_query_candidates(artist: str, title: str) -> list[tuple[str, str]]:
    """Return stable lookup variants without mutating canonical metadata.

    The first entry is always the conservative canonical pair. A plausible
    ``title / artist`` interpretation is appended as a fallback so browser
    uploader metadata can still resolve while valid titles such as
    ``"Love / Hate"`` remain intact for display and caching.
    """
    canonical_artist, canonical_title = normalise_yt_meta(artist, title)
    candidates = [(canonical_artist, canonical_title)]

    slash_candidate = _valid_slash_candidate(canonical_title)
    if slash_candidate and slash_candidate not in candidates:
        candidates.append(slash_candidate)

    deduplicated: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate_artist, candidate_title in candidates:
        key = (candidate_artist.casefold(), candidate_title.casefold())
        if candidate_title and key not in seen:
            seen.add(key)
            deduplicated.append((candidate_artist, candidate_title))
    return deduplicated or [("", "")]
