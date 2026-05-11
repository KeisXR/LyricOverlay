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


def _sub_strip_if_changed(pattern: re.Pattern, value: str) -> str:
    cleaned = pattern.sub("", value)
    return cleaned.strip() if cleaned != value else value


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
