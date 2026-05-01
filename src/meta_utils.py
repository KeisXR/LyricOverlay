"""Metadata normalisation helpers.

Cleans up platform-specific noise in track metadata (artist, title)
before lyrics searches.
"""

import re

# Matches YouTube Music's auto-generated " - Topic" channel suffix.
_TOPIC_RE = re.compile(r"\s*-\s*Topic$", re.IGNORECASE)

# Maps commonly confused Unicode punctuation to canonical forms so that
# notation variants (表記ゆれ) don't prevent a lyrics match.  For example,
# YouTube Music titles often contain 〜 (U+301C WAVE DASH) while LRClib
# stores the same song with ～ (U+FF5E FULLWIDTH TILDE).
_UNICODE_VARIANT_MAP = str.maketrans({
    "\u301c": "\uff5e",  # 〜 WAVE DASH → ～ FULLWIDTH TILDE
    "\u2026": "...",     # … HORIZONTAL ELLIPSIS → ...
    "\u22ef": "...",     # ⋯ MIDLINE HORIZONTAL ELLIPSIS → ...
    "\u2025": "..",      # ‥ TWO DOT LEADER → ..
})


def normalise_unicode_variants(text: str) -> str:
    """Canonicalise Unicode notation variants (表記ゆれ) in a metadata string.

    Replaces visually similar punctuation characters with a single canonical
    form so that small typographic differences between streaming service
    metadata and the lyrics database do not prevent a match.

    Examples of normalised pairs:

    - ``〜`` (U+301C WAVE DASH) → ``～`` (U+FF5E FULLWIDTH TILDE)
    - ``…`` (U+2026 HORIZONTAL ELLIPSIS) → ``...``
    - ``・・・`` (three U+30FB KATAKANA MIDDLE DOTs) → ``...``
    """
    text = text.translate(_UNICODE_VARIANT_MAP)
    # Three consecutive katakana middle dots are a common Japanese substitute
    # for an ellipsis and must be handled as a unit.
    text = text.replace("\u30fb\u30fb\u30fb", "...")
    return text


def normalise_yt_meta(artist: str, title: str) -> tuple[str, str]:
    """Remove YouTube Music noise from artist/title before a lyrics search.

    Three patterns are handled:

    1. Unicode notation variants (表記ゆれ) — e.g. ``〜`` vs ``～`` or
       ``…`` vs ``···``.  Both fields are passed through
       :func:`normalise_unicode_variants` first.

    2. ``"Artist - Topic"`` — YouTube auto-generates a channel with this
       suffix for artists.  LRClib knows the artist without the suffix.

    3. ``"Song Title / Original Artist"`` — YouTube Music sometimes embeds
       the original artist inside the title when the uploader is different
       from the credited artist.  In that case we extract the embedded
       artist and use the left-hand part as the clean title.
    """
    artist = normalise_unicode_variants(artist)
    title = normalise_unicode_variants(title)

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
