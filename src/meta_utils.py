"""Metadata normalisation helpers.

Cleans up platform-specific noise in track metadata (artist, title)
before lyrics searches.
"""

import re

# Matches YouTube Music's auto-generated " - Topic" channel suffix.
_TOPIC_RE = re.compile(r"\s*-\s*Topic$", re.IGNORECASE)

# Platform/video-page noise that is often appended to browser titles.
_SITE_SUFFIX_RE = re.compile(
    r"\s*(?:\|\s*(?:YouTube(?: Music)?|Spotify)|-\s*YouTube)$",
    re.IGNORECASE,
)

# Remove only clearly promotional video labels at the end of the title.  Keep
# musical version labels such as "acoustic ver." because LRClib may index them.
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


def _strip_repeated_artist_prefix(artist: str, title: str) -> str:
    for sep in (" - ", " – ", " — "):
        prefix = f"{artist}{sep}"
        if title.casefold().startswith(prefix.casefold()):
            stripped = title[len(prefix):].strip()
            if stripped:
                return stripped
    return title


def _sub_strip_if_changed(pattern: re.Pattern, value: str) -> str:
    cleaned = pattern.sub("", value)
    return cleaned.strip() if cleaned != value else value


_PLACEHOLDER_TITLES = {"youtube music", "youtube", "spotify"}


def _is_placeholder_trackid(value: str) -> bool:
    return not value or value == "/"


def _is_same_track_for_enrichment(base: dict, candidate: dict) -> bool:
    base_trackid = str(base.get("trackid", ""))
    cand_trackid = str(candidate.get("trackid", ""))
    if (
        not _is_placeholder_trackid(base_trackid)
        and not _is_placeholder_trackid(cand_trackid)
        and base_trackid == cand_trackid
    ):
        return True

    base_title = str(base.get("title", "")).strip().casefold()
    cand_title = str(candidate.get("title", "")).strip().casefold()
    if not base_title or base_title != cand_title:
        return False

    base_len = int(base.get("length_ms", 0) or 0)
    cand_len = int(candidate.get("length_ms", 0) or 0)
    if base_len > 0 and cand_len > 0 and abs(base_len - cand_len) > 3000:
        return False

    base_album = str(base.get("album", "")).strip().casefold()
    cand_album = str(candidate.get("album", "")).strip().casefold()
    if base_album and cand_album and base_album != cand_album:
        return False
    return True


def enrich_missing_meta(base_meta: dict, candidate_metas: list[dict]) -> tuple[dict, bool]:
    """Fill missing artist/album (artist is preferred over album/title) for the same track."""
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
    """Remove YouTube Music noise from artist/title before a lyrics search.

    Two patterns are handled:

    1. ``"Artist - Topic"`` — YouTube auto-generates a channel with this
       suffix for artists.  LRClib knows the artist without the suffix.

    2. ``"Song Title / Original Artist"`` — YouTube Music sometimes embeds
       the original artist inside the title when the uploader is different
       from the credited artist.  In that case we extract the embedded
       artist and use the left-hand part as the clean title.
    """
    artist = _TOPIC_RE.sub("", artist).strip()
    title = _sub_strip_if_changed(_SITE_SUFFIX_RE, title)
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

    if " / " in title:
        # Split only on the first " / " so titles like
        # "Song / Artist / Extra" become title="Song", artist="Artist / Extra"
        # rather than stopping at the wrong boundary.
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
            artist = artist_part
            title = title_part

    return artist, title
