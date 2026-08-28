"""Tests for the LRC parser."""

import sys
sys.path.insert(0, "src")

from lyrics.lrc_parser import parse_lrc, find_current_line, LyricLine, ParsedLRC


def test_basic_timestamps():
    lrc = parse_lrc("[01:23.45]First line\n[02:30.00]Second line\n[03:00.00]Third")
    assert len(lrc.lines) == 3
    assert lrc.lines[0].timestamp_ms == 83450   # 1*60000 + 23*1000 + 450
    assert lrc.lines[0].text == "First line"
    assert lrc.lines[1].timestamp_ms == 150000   # 2*60000 + 30*1000
    assert lrc.lines[1].text == "Second line"


def test_multi_timestamp():
    """Lines with multiple timestamps produce multiple LyricLine entries."""
    lrc = parse_lrc("[00:01.00][00:10.00]Repeated line")
    assert len(lrc.lines) == 2
    assert lrc.lines[0].timestamp_ms == 1000
    assert lrc.lines[1].timestamp_ms == 10000
    assert lrc.lines[0].text == "Repeated line"
    assert lrc.lines[1].text == "Repeated line"


def test_header_tags():
    lrc = parse_lrc("[ti:Test Title]\n[ar:Test Artist]\n[offset:1500]\n[00:01.00]Line")
    assert lrc.title == "Test Title"
    assert lrc.artist == "Test Artist"
    assert lrc.offset_ms == 1500
    # offset is applied
    assert lrc.lines[0].timestamp_ms == 2500  # 1000 + 1500


def test_negative_offset():
    lrc = parse_lrc("[offset:-500]\n[00:02.00]Line")
    assert lrc.offset_ms == -500
    assert lrc.lines[0].timestamp_ms == 1500  # 2000 - 500


def test_empty_input():
    lrc = parse_lrc("")
    assert len(lrc.lines) == 0
    assert lrc.title is None


def test_no_timestamps():
    """Lines without timestamps are ignored."""
    lrc = parse_lrc("Just some text\n[ti:Title]\nMore text without tags")
    assert len(lrc.lines) == 0
    assert lrc.title == "Title"


def test_find_current_line():
    lrc = ParsedLRC(
        lines=[
            LyricLine(1000, "A"),
            LyricLine(5000, "B"),
            LyricLine(10000, "C"),
        ]
    )
    assert find_current_line(lrc, 0) == -1
    assert find_current_line(lrc, 1000) == 0
    assert find_current_line(lrc, 4999) == 0
    assert find_current_line(lrc, 5000) == 1
    assert find_current_line(lrc, 9999) == 1
    assert find_current_line(lrc, 10000) == 2
    assert find_current_line(lrc, 99999) == 2


def test_sorting():
    """Lines are always sorted by timestamp, regardless of input order."""
    lrc = parse_lrc("[00:10.00]B\n[00:01.00]A\n[00:05.00]C")
    stamps = [l.timestamp_ms for l in lrc.lines]
    assert stamps == [1000, 5000, 10000]


def test_float_seconds():
    lrc = parse_lrc("[00:01.5]Line")
    assert lrc.lines[0].timestamp_ms == 1500


def test_enhanced_word_timing():
    """Enhanced LRC with <mm:ss.xx> word-level tags is parsed."""
    synced = (
        "[00:18.32]<00:18.32>I <00:18.55>just <00:18.74>wanna "
        "<00:19.12>be <00:19.55>part <00:19.93>of <00:20.34>your "
        "<00:20.74>symphony"
    )
    lrc = parse_lrc(synced)
    assert len(lrc.lines) == 1
    assert lrc.lines[0].timestamp_ms == 18320
    assert lrc.lines[0].text == "I just wanna be part of your symphony"
    assert lrc.lines[0].words is not None
    assert len(lrc.lines[0].words) == 8
    assert lrc.lines[0].words[0].text == "I "
    assert lrc.lines[0].words[0].timestamp_ms == 18320
    assert lrc.lines[0].words[-1].text == "symphony"
    assert lrc.lines[0].words[-1].timestamp_ms == 20740


def test_enhanced_word_timing_without_spaces():
    """Enhanced LRC can represent Japanese per-character timing."""
    synced = "[00:01.00]<00:01.00>全<00:01.20>力<00:01.40>少年"
    lrc = parse_lrc(synced)

    assert lrc.lines[0].text == "全力少年"
    assert lrc.lines[0].words is not None
    assert [w.text for w in lrc.lines[0].words] == ["全", "力", "少年"]
    assert [w.timestamp_ms for w in lrc.lines[0].words] == [1000, 1200, 1400]


def test_enhanced_word_timing_applies_offset():
    synced = "[offset:500]\n[00:01.00]<00:01.00>A <00:01.25>B"
    lrc = parse_lrc(synced)

    assert lrc.lines[0].timestamp_ms == 1500
    assert lrc.lines[0].words is not None
    assert [w.timestamp_ms for w in lrc.lines[0].words] == [1500, 1750]


def test_offset_order_independent():
    cases = [
        "[offset:500]\n[00:01.00]A\n[00:02.00]B",
        "[00:01.00]A\n[offset:500]\n[00:02.00]B",
        "[00:01.00]A\n[00:02.00]B\n[offset:500]",
    ]

    snapshots = []
    for text in cases:
        lrc = parse_lrc(text)
        snapshots.append([(line.timestamp_ms, line.text) for line in lrc.lines])

    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert snapshots[0] == [(1500, "A"), (2500, "B")]


def test_multi_timestamp_enhanced_words_shift_per_line():
    lrc = parse_lrc("[00:01.00][00:11.00]<00:01.00>A <00:01.25>B")
    assert len(lrc.lines) == 2
    assert lrc.lines[0].words is not None
    assert lrc.lines[1].words is not None
    assert [w.timestamp_ms for w in lrc.lines[0].words] == [1000, 1250]
    assert [w.timestamp_ms for w in lrc.lines[1].words] == [11000, 11250]


def test_multi_timestamp_enhanced_unsafe_disables_words():
    lrc = parse_lrc("[00:05.00][00:10.00]pre<00:04.00>A <00:04.50>B")
    assert len(lrc.lines) == 2
    assert lrc.lines[0].text == "preA B"
    assert lrc.lines[0].words is None
    assert lrc.lines[1].words is None


def test_enhanced_prefix_text_preserved():
    lrc = parse_lrc("[00:01.00]Hey, <00:01.20>you")
    assert lrc.lines[0].text == "Hey, you"
    assert lrc.lines[0].words is not None
    assert [w.text for w in lrc.lines[0].words] == ["you"]


def test_bom_crlf_empty_and_invalid_lines_are_safe():
    synced = "\ufeff[offset:oops]\r\n[offset:-250]\r\n\r\n[00:01.123]\r\n[00:aa.bb]bad\r\n[00:02.000]Line\r\n"
    lrc = parse_lrc(synced)
    assert lrc.offset_ms == -250
    assert [line.timestamp_ms for line in lrc.lines] == [873, 1750]
    assert lrc.lines[0].text == ""
    assert lrc.lines[1].text == "Line"


def test_same_timestamp_order_is_stable():
    lrc = parse_lrc("[00:01.00]A\n[00:01.00]B\n[00:01.00]C")
    assert [line.text for line in lrc.lines] == ["A", "B", "C"]


if __name__ == "__main__":
    import traceback
    tests = [
        ("basic_timestamps", test_basic_timestamps),
        ("multi_timestamp", test_multi_timestamp),
        ("header_tags", test_header_tags),
        ("negative_offset", test_negative_offset),
        ("empty_input", test_empty_input),
        ("no_timestamps", test_no_timestamps),
        ("find_current_line", test_find_current_line),
        ("sorting", test_sorting),
        ("float_seconds", test_float_seconds),
        ("enhanced_word_timing", test_enhanced_word_timing),
        ("enhanced_word_timing_without_spaces", test_enhanced_word_timing_without_spaces),
        ("enhanced_word_timing_applies_offset", test_enhanced_word_timing_applies_offset),
        ("offset_order_independent", test_offset_order_independent),
        ("multi_timestamp_enhanced_words_shift_per_line", test_multi_timestamp_enhanced_words_shift_per_line),
        ("multi_timestamp_enhanced_unsafe_disables_words", test_multi_timestamp_enhanced_unsafe_disables_words),
        ("enhanced_prefix_text_preserved", test_enhanced_prefix_text_preserved),
        ("bom_crlf_empty_and_invalid_lines_are_safe", test_bom_crlf_empty_and_invalid_lines_are_safe),
        ("same_timestamp_order_is_stable", test_same_timestamp_order_is_stable),
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
