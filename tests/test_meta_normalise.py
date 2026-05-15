"""Unit tests for normalise_yt_meta in meta_utils.py."""

import sys

sys.path.insert(0, "src")

from meta_utils import enrich_missing_meta, normalise_yt_meta


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


def test_empty_left_of_slash_not_used():
    artist, title = normalise_yt_meta("Original Artist", " / Artist")
    assert artist == "Original Artist"
    assert title == " / Artist"


def test_video_word_right_of_slash_not_used_as_artist():
    artist, title = normalise_yt_meta("Original Artist", "Song / Official Video")
    assert artist == "Original Artist"
    assert title == "Song / Official Video"


def test_multiple_slash_separators():
    # Only the first " / " is used; everything after it becomes the artist.
    artist, title = normalise_yt_meta("Uploader", "Song / Real Artist / Extra")
    assert title == "Song"
    assert artist == "Real Artist / Extra"


# ---------------------------------------------------------------------------
# Browser/video title cleanup
# ---------------------------------------------------------------------------

def test_strips_site_suffixes_from_title():
    artist, title = normalise_yt_meta("Artist", "Song | YouTube Music")
    assert artist == "Artist"
    assert title == "Song"


def test_strips_official_video_noise():
    artist, title = normalise_yt_meta("Artist", "Song (Official Music Video)")
    assert artist == "Artist"
    assert title == "Song"


def test_strips_lyric_video_noise_in_brackets():
    artist, title = normalise_yt_meta("Artist", "Song [Lyric Video]")
    assert artist == "Artist"
    assert title == "Song"


def test_keeps_musical_version_label():
    artist, title = normalise_yt_meta("Artist", "Song (acoustic ver.)")
    assert artist == "Artist"
    assert title == "Song (acoustic ver.)"


def test_splits_browser_artist_title_when_artist_empty():
    artist, title = normalise_yt_meta("", "Official髭男dism - らしさ [Official Audio]")
    assert artist == "Official髭男dism"
    assert title == "らしさ"


def test_strips_repeated_artist_prefix_when_artist_present():
    artist, title = normalise_yt_meta(
        "Official髭男dism",
        "Official髭男dism - らしさ [Official Audio]",
    )
    assert artist == "Official髭男dism"
    assert title == "らしさ"


def test_does_not_split_dash_title_when_artist_present():
    artist, title = normalise_yt_meta("Artist", "Song - Subtitle")
    assert artist == "Artist"
    assert title == "Song - Subtitle"


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_passthrough_when_no_patterns():
    artist, title = normalise_yt_meta("みゆはん", "ぼくのフレンド")
    assert artist == "みゆはん"
    assert title == "ぼくのフレンド"




def test_drops_placeholder_site_title_when_artist_empty():
    artist, title = normalise_yt_meta("", "YouTube Music")
    assert artist == ""
    assert title == ""

def test_empty_strings():
    artist, title = normalise_yt_meta("", "")
    assert artist == ""
    assert title == ""


def test_enriches_missing_artist_and_album_from_same_title():
    base = {
        "title": "らしさ",
        "artist": "",
        "album": "",
        "trackid": "/org/mpris/MediaPlayer2/track/123",
        "length_ms": 254000,
    }
    candidate = {
        "title": "らしさ",
        "artist": "Official髭男dism",
        "album": "Traveler",
        "trackid": "/",
        "length_ms": 254500,
    }
    merged, changed = enrich_missing_meta(base, [candidate])
    assert changed is True
    assert merged["artist"] == "Official髭男dism"
    assert merged["album"] == "Traveler"
    assert merged["title"] == "らしさ"


def test_does_not_enrich_when_title_differs():
    base = {
        "title": "らしさ",
        "artist": "",
        "album": "",
        "trackid": "/org/mpris/MediaPlayer2/track/123",
        "length_ms": 254000,
    }
    candidate = {
        "title": "Pretender",
        "artist": "Official髭男dism",
        "album": "Traveler",
        "trackid": "/",
        "length_ms": 254000,
    }
    merged, changed = enrich_missing_meta(base, [candidate])
    assert changed is False
    assert merged["artist"] == ""
    assert merged["album"] == ""


if __name__ == "__main__":
    import traceback

    tests = [
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
        ("empty_left_of_slash_not_used", test_empty_left_of_slash_not_used),
        ("video_word_right_of_slash_not_used_as_artist", test_video_word_right_of_slash_not_used_as_artist),
        ("passthrough_when_no_patterns", test_passthrough_when_no_patterns),
        ("empty_strings", test_empty_strings),
        ("strips_site_suffixes_from_title", test_strips_site_suffixes_from_title),
        ("strips_official_video_noise", test_strips_official_video_noise),
        ("strips_lyric_video_noise_in_brackets", test_strips_lyric_video_noise_in_brackets),
        ("keeps_musical_version_label", test_keeps_musical_version_label),
        ("splits_browser_artist_title_when_artist_empty", test_splits_browser_artist_title_when_artist_empty),
        ("strips_repeated_artist_prefix_when_artist_present", test_strips_repeated_artist_prefix_when_artist_present),
        ("does_not_split_dash_title_when_artist_present", test_does_not_split_dash_title_when_artist_present),
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
