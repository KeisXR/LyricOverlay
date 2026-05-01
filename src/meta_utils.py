"""Metadata normalisation helpers.

Cleans up platform-specific noise in track metadata (artist, title)
before lyrics searches.
"""

import re

# Matches YouTube Music's auto-generated " - Topic" channel suffix.
_TOPIC_RE = re.compile(r"\s*-\s*Topic$", re.IGNORECASE)


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

    if " / " in title:
        # Split only on the first " / " so titles like
        # "Song / Artist / Extra" become title="Song", artist="Artist / Extra"
        # rather than stopping at the wrong boundary.
        title_part, artist_part = title.split(" / ", 1)
        title_part = title_part.strip()
        artist_part = artist_part.strip()
        if artist_part:
            artist = artist_part
            title = title_part

    return artist, title
