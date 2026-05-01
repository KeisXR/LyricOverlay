"""Tests for lyrics manager cache serialization helpers."""

import sys

sys.path.insert(0, "src")

from lyrics.lrc_parser import LyricLine, ParsedLRC, parse_lrc
from lyrics.manager import LyricsManager


def test_lrc_to_raw_preserves_offset_once():
    lrc = ParsedLRC(
        offset_ms=1500,
        lines=[LyricLine(timestamp_ms=2500, text="Line")],
    )

    raw = LyricsManager._lrc_to_raw(lrc)
    reparsed = parse_lrc(raw)

    assert "[offset:1500]" in raw
    assert reparsed.offset_ms == 1500
    assert reparsed.lines[0].timestamp_ms == 2500


if __name__ == "__main__":
    import traceback

    tests = [
        ("lrc_to_raw_preserves_offset_once", test_lrc_to_raw_preserves_offset_once),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
