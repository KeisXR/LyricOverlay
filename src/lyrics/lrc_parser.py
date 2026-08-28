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


def _parse_timestamp_ms(mins: str, secs: str) -> int:
    seconds, dot, fraction = secs.partition(".")
    frac_ms = int((fraction + "000")[:3]) if dot else 0
    return int(mins) * 60000 + int(seconds) * 1000 + frac_ms


def _parse_enhanced_words(content: str) -> tuple[str, list[LyricWord] | None]:
    matches = list(_WORDTIME_RE.finditer(content))
    if not matches:
        return content.strip(), None

    prefix = content[:matches[0].start()]
    words: list[LyricWord] = []
    text_parts: list[str] = [prefix]
    for i, match in enumerate(matches):
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        segment = content[match.end():next_start]
        if not segment:
            continue
        words.append(
            LyricWord(
                timestamp_ms=_parse_timestamp_ms(match.group(1), match.group(2)),
                text=segment,
            )
        )
        text_parts.append(segment)

    if not words:
        return content.strip(), None
    return "".join(text_parts).strip(), words


def _is_safe_multi_timestamp_enhanced(
    words: list[LyricWord], base_timestamp: int
) -> bool:
    return (
        all(word.timestamp_ms >= base_timestamp for word in words)
        and all(words[i].timestamp_ms <= words[i + 1].timestamp_ms for i in range(len(words) - 1))
    )


def parse_lrc(lrc_text: str) -> ParsedLRC:
    """Parse an LRC-formatted string into a ``ParsedLRC``."""
    result = ParsedLRC()

    normalized = lrc_text.lstrip("\ufeff")

    for raw in normalized.splitlines():
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
        stamp_matches = list(_TIMESTAMP_RE.finditer(raw))
        stamps = [(_parse_timestamp_ms(m.group(1), m.group(2))) for m in stamp_matches]
        if not stamps:
            continue

        content = _TIMESTAMP_RE.sub("", raw)
        text, parsed_words = _parse_enhanced_words(content)

        base_timestamp = stamps[0]
        can_shift_words = parsed_words is not None and _is_safe_multi_timestamp_enhanced(
            parsed_words, base_timestamp
        )
        for ts_ms in stamps:
            words_for_line = parsed_words
            if parsed_words is not None and len(stamps) > 1:
                if can_shift_words:
                    words_for_line = [
                        LyricWord(
                            timestamp_ms=ts_ms + (word.timestamp_ms - base_timestamp),
                            text=word.text,
                        )
                        for word in parsed_words
                    ]
                else:
                    words_for_line = None
            result.lines.append(
                LyricLine(
                    timestamp_ms=ts_ms,
                    text=text,
                    words=words_for_line,
                )
            )

    if result.offset_ms:
        for line in result.lines:
            line.timestamp_ms += result.offset_ms
            if line.words is not None:
                for word in line.words:
                    word.timestamp_ms += result.offset_ms

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
