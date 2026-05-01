"""Unit tests for normalise_yt_meta and normalise_unicode_variants in meta_utils.py."""

import sys

sys.path.insert(0, "src")

from meta_utils import normalise_yt_meta, normalise_unicode_variants


# ---------------------------------------------------------------------------
# normalise_unicode_variants — standalone unit tests
# ---------------------------------------------------------------------------

def test_wave_dash_to_fullwidth_tilde():
    # 〜 (U+301C WAVE DASH) → ～ (U+FF5E FULLWIDTH TILDE)
    assert normalise_unicode_variants("友よ 〜 この先もずっと") == "友よ ～ この先もずっと"


def test_fullwidth_tilde_unchanged():
    # ～ is already canonical; must not be double-converted
    assert normalise_unicode_variants("友よ ～ この先もずっと") == "友よ ～ この先もずっと"


def test_horizontal_ellipsis_to_dots():
    # … (U+2026) → ...
    assert normalise_unicode_variants("この先もずっと…") == "この先もずっと..."


def test_midline_ellipsis_to_dots():
    # ⋯ (U+22EF) → ...
    assert normalise_unicode_variants("A\u22efB") == "A...B"


def test_two_dot_leader_to_dots():
    # ‥ (U+2025) → ..
    assert normalise_unicode_variants("A\u2025B") == "A..B"


def test_katakana_middle_dot_triple_to_dots():
    # ・・・ (U+30FB × 3) → ...
    assert normalise_unicode_variants("A\u30fb\u30fb\u30fbB") == "A...B"


def test_katakana_middle_dot_single_unchanged():
    # A single ・ is a legitimate separator and must not be removed
    assert normalise_unicode_variants("A・B") == "A・B"


def test_ketsumeis_song_title():
    # The title as reported in the issue: YouTube sends 〜 and …, LRClib has ～ and ···
    yt_title = "友よ 〜 この先もずっと…"
    lrclib_title = "友よ ～ この先もずっと\u30fb\u30fb\u30fb"
    assert normalise_unicode_variants(yt_title) == normalise_unicode_variants(lrclib_title)


def test_plain_ascii_unchanged():
    assert normalise_unicode_variants("Hello World") == "Hello World"


def test_empty_string():
    assert normalise_unicode_variants("") == ""


# ---------------------------------------------------------------------------
# " - Topic" stripping
# ---------------------------------------------------------------------------

def test_strips_topic_suffix():
    artist, title = normalise_yt_meta("Mewhan - Topic", "ぼくのフレンド（acoustic ver.）")
    assert artist == "Mewhan"
    assert title == "ぼくのフレンド（acoustic ver.）"


def test_strips_topic_suffix_case_insensitive():
    artist, title = normalise_yt_meta("Some Artist - TOPIC", "A Song")
    assert artist == "Some Artist"
    assert title == "A Song"


def test_strips_topic_suffix_with_extra_spaces():
    artist, title = normalise_yt_meta("Some Artist  -  Topic", "A Song")
    assert artist == "Some Artist"


def test_does_not_strip_non_topic_suffix():
    artist, title = normalise_yt_meta("FUNKY MONKEY BΛBY'S", "ヒーロー")
    assert artist == "FUNKY MONKEY BΛBY'S"
    assert title == "ヒーロー"


def test_strips_topic_with_band_name():
    artist, title = normalise_yt_meta("FUNKY MONKEY BΛBY'S - Topic", "ヒーロー")
    assert artist == "FUNKY MONKEY BΛBY'S"
    assert title == "ヒーロー"


# ---------------------------------------------------------------------------
# " / original-artist" extraction from title
# ---------------------------------------------------------------------------

def test_splits_title_slash_artist():
    artist, title = normalise_yt_meta("Steven Mak", "ぼくのフレンド / みゆはん")
    assert title == "ぼくのフレンド"
    assert artist == "みゆはん"


def test_splits_title_slash_and_topic():
    # Both patterns together: "- Topic" in artist AND "/ artist" in title.
    # The embedded artist from the title should win.
    artist, title = normalise_yt_meta("SomeUploader - Topic", "Song / RealArtist")
    assert title == "Song"
    assert artist == "RealArtist"


def test_does_not_split_on_slash_without_spaces():
    # "AC/DC" style — no spaces around slash, must not be split.
    artist, title = normalise_yt_meta("AC/DC", "Highway to Hell")
    assert artist == "AC/DC"
    assert title == "Highway to Hell"


def test_does_not_split_title_without_slash():
    artist, title = normalise_yt_meta("Artist", "Plain Song Title")
    assert artist == "Artist"
    assert title == "Plain Song Title"


def test_empty_right_of_slash_not_used():
    # Degenerate case: " / " present but nothing after it.
    artist, title = normalise_yt_meta("Original Artist", "Song / ")
    assert artist == "Original Artist"
    assert title == "Song / "


def test_multiple_slash_separators():
    # Only the first " / " is used; everything after it becomes the artist.
    artist, title = normalise_yt_meta("Uploader", "Song / Real Artist / Extra")
    assert title == "Song"
    assert artist == "Real Artist / Extra"


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_passthrough_when_no_patterns():
    artist, title = normalise_yt_meta("みゆはん", "ぼくのフレンド")
    assert artist == "みゆはん"
    assert title == "ぼくのフレンド"


def test_empty_strings():
    artist, title = normalise_yt_meta("", "")
    assert artist == ""
    assert title == ""


if __name__ == "__main__":
    import traceback

    tests = [
        # normalise_unicode_variants
        ("wave_dash_to_fullwidth_tilde", test_wave_dash_to_fullwidth_tilde),
        ("fullwidth_tilde_unchanged", test_fullwidth_tilde_unchanged),
        ("horizontal_ellipsis_to_dots", test_horizontal_ellipsis_to_dots),
        ("midline_ellipsis_to_dots", test_midline_ellipsis_to_dots),
        ("two_dot_leader_to_dots", test_two_dot_leader_to_dots),
        ("katakana_middle_dot_triple_to_dots", test_katakana_middle_dot_triple_to_dots),
        ("katakana_middle_dot_single_unchanged", test_katakana_middle_dot_single_unchanged),
        ("ketsumeis_song_title", test_ketsumeis_song_title),
        ("plain_ascii_unchanged", test_plain_ascii_unchanged),
        ("empty_string", test_empty_string),
        # normalise_yt_meta
        ("strips_topic_suffix", test_strips_topic_suffix),
        ("strips_topic_suffix_case_insensitive", test_strips_topic_suffix_case_insensitive),
        ("strips_topic_suffix_with_extra_spaces", test_strips_topic_suffix_with_extra_spaces),
        ("does_not_strip_non_topic_suffix", test_does_not_strip_non_topic_suffix),
        ("strips_topic_with_band_name", test_strips_topic_with_band_name),
        ("splits_title_slash_artist", test_splits_title_slash_artist),
        ("splits_title_slash_and_topic", test_splits_title_slash_and_topic),
        ("does_not_split_on_slash_without_spaces", test_does_not_split_on_slash_without_spaces),
        ("does_not_split_title_without_slash", test_does_not_split_title_without_slash),
        ("empty_right_of_slash_not_used", test_empty_right_of_slash_not_used),
        ("passthrough_when_no_patterns", test_passthrough_when_no_patterns),
        ("empty_strings", test_empty_strings),
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
