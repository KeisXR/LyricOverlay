"""LRC format parser.

Supports:
  - Basic timestamps:   [mm:ss.xx]text
  - Multi-timestamps:   [00:01.00][00:20.00]repeated text
  - Enhanced word-level: <mm:ss.xx>word1 <mm:ss.yy>word2
  - Header tags:        [ti:...] [ar:...] [offset:...]
"""

import re
from dataclasses import dataclass, field


@dataclass
class LyricWord:
    timestamp_ms: int
    text: str


@dataclass
class LyricLine:
    timestamp_ms: int
    text: str
    words: list[LyricWord] | None = None


@dataclass
class ParsedLRC:
    title: str | None = None
    artist: str | None = None
    offset_ms: int = 0
    lines: list[LyricLine] = field(default_factory=list)


_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:\.\d+)?)\]")
_HEADER_RE = re.compile(r"\[(ti|ar|al|offset|length):\s*(.+)\]", re.IGNORECASE)
_WORDTIME_RE = re.compile(r"<(\d{1,3}):(\d{1,2}(?:\.\d+)?)>")


def parse_lrc(lrc_text: str) -> ParsedLRC:
    """Parse an LRC-formatted string into a ``ParsedLRC``."""
    result = ParsedLRC()

    for raw in lrc_text.strip().split("\n"):
        raw = raw.strip()
        if not raw:
            continue

        # Header tags
        h = _HEADER_RE.match(raw)
        if h:
            key, value = h.group(1).lower(), h.group(2).strip()
            if key == "ti":
                result.title = value
            elif key == "ar":
                result.artist = value
            elif key == "offset":
                try:
                    result.offset_ms = int(value)
                except ValueError:
                    pass
            # "al" and "length" are ignored for now
            continue

        # Timestamps
        stamps = _TIMESTAMP_RE.findall(raw)
        if not stamps:
            continue

        text = re.sub(r"\[.*?\]", "", raw).strip()

        # Word-level enhanced timing
        word_matches = _WORDTIME_RE.findall(raw)
        parsed_words: list[LyricWord] | None = None
        if word_matches:
            word_text = re.sub(r"\[.*?\]|<.*?>", "", raw).strip()
            parts = word_text.split()
            parsed_words = []
            for i, wm in enumerate(word_matches):
                mins, secs = wm
                ts = int(mins) * 60000 + round(float(secs) * 1000)
                if i < len(parts):
                    parsed_words.append(LyricWord(timestamp_ms=ts, text=parts[i]))
            # Override text to remove both bracket and angle-bracket tags
            text = word_text

        for mins, secs in stamps:
            ts_ms = int(mins) * 60000 + round(float(secs) * 1000)
            result.lines.append(
                LyricLine(
                    timestamp_ms=ts_ms + result.offset_ms,
                    text=text,
                    words=parsed_words,
                )
            )

    result.lines.sort(key=lambda ln: ln.timestamp_ms)
    return result


def find_current_line(lrc: ParsedLRC, position_ms: int) -> int:
    """Binary search: return the index of the last line whose timestamp
    is <= *position_ms*.  Returns -1 if no line qualifies."""
    if not lrc.lines:
        return -1
    lo, hi = 0, len(lrc.lines) - 1
    found = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lrc.lines[mid].timestamp_ms <= position_ms:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return found
