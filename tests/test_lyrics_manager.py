"""Tests for lyrics manager cache serialization helpers."""

import sys

sys.path.insert(0, "src")

from lyrics.lrc_parser import LyricLine, LyricWord, ParsedLRC, parse_lrc
from lyrics.lrclib import LrcLibResult
from lyrics.manager import LyricsManager, _FetchAltThread, _FetchPrimaryThread
import lyrics.manager as manager_module


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


def test_lrc_to_raw_preserves_enhanced_word_timing():
    lrc = ParsedLRC(
        lines=[
            LyricLine(
                timestamp_ms=1000,
                text="全力少年",
                words=[
                    LyricWord(timestamp_ms=1000, text="全"),
                    LyricWord(timestamp_ms=1200, text="力"),
                    LyricWord(timestamp_ms=1400, text="少年"),
                ],
            )
        ],
    )

    raw = LyricsManager._lrc_to_raw(lrc)
    reparsed = parse_lrc(raw)

    assert "<00:01.00>全" in raw
    assert reparsed.lines[0].text == "全力少年"
    assert reparsed.lines[0].words is not None
    assert [w.timestamp_ms for w in reparsed.lines[0].words] == [1000, 1200, 1400]


def test_fetch_alt_thread_constructor_matches_fetch_alternatives_call_shape():
    thread = _FetchAltThread("artist", "title", "album", 123000)

    assert thread is not None


def test_primary_thread_falls_back_to_lrclib_when_syncedlyrics_errors():
    original_syncedlyrics = manager_module.get_syncedlyrics
    original_lrclib = manager_module.get_lrclib
    emitted = []

    def failing_syncedlyrics(*args, **kwargs):
        raise RuntimeError("syncedlyrics unavailable")

    def fake_lrclib(*args, **kwargs):
        return LrcLibResult(track_name="Title", artist_name="Artist")

    try:
        manager_module.get_syncedlyrics = failing_syncedlyrics
        manager_module.get_lrclib = fake_lrclib
        thread = _FetchPrimaryThread(
            "Artist",
            "Title",
            "",
            0,
            True,
            True,
            True,
        )
        thread.finished_ok.connect(emitted.append)
        thread.run()
    finally:
        manager_module.get_syncedlyrics = original_syncedlyrics
        manager_module.get_lrclib = original_lrclib

    assert emitted
    assert emitted[0][0] == "lrclib"


if __name__ == "__main__":
    import traceback

    tests = [
        ("lrc_to_raw_preserves_offset_once", test_lrc_to_raw_preserves_offset_once),
        (
            "lrc_to_raw_preserves_enhanced_word_timing",
            test_lrc_to_raw_preserves_enhanced_word_timing,
        ),
        (
            "fetch_alt_thread_constructor_matches_fetch_alternatives_call_shape",
            test_fetch_alt_thread_constructor_matches_fetch_alternatives_call_shape,
        ),
        (
            "primary_thread_falls_back_to_lrclib_when_syncedlyrics_errors",
            test_primary_thread_falls_back_to_lrclib_when_syncedlyrics_errors,
        ),
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
