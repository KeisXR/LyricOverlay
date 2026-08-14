"""Tests for conservative player metadata normalisation."""

import sys

sys.path.insert(0, "src")

from meta_utils import enrich_missing_meta, normalise_yt_meta, search_query_candidates


def test_strips_topic_suffix_case_insensitively():
    assert normalise_yt_meta("Mewhan - TOPIC", "A Song") == (
        "Mewhan",
        "A Song",
    )


def test_does_not_strip_non_topic_artist_text():
    assert normalise_yt_meta("FUNKY MONKEY BΛBY'S", "ヒーロー") == (
        "FUNKY MONKEY BΛBY'S",
        "ヒーロー",
    )


def test_artist_present_preserves_valid_slash_title():
    assert normalise_yt_meta("Actual Artist", "Love / Hate") == (
        "Actual Artist",
        "Love / Hate",
    )


def test_slash_interpretation_is_an_additional_search_candidate():
    assert search_query_candidates("Uploader", "Song / Real Artist") == [
        ("Uploader", "Song / Real Artist"),
        ("Real Artist", "Song"),
    ]


def test_artist_missing_can_use_clear_slash_metadata():
    assert normalise_yt_meta("", "Song / Real Artist") == (
        "Real Artist",
        "Song",
    )


def test_does_not_split_slash_without_spaces():
    assert normalise_yt_meta("AC/DC", "Highway to Hell") == (
        "AC/DC",
        "Highway to Hell",
    )


def test_empty_or_promotional_slash_parts_are_not_candidates():
    assert search_query_candidates("Artist", "Song / ") == [("Artist", "Song / ")]
    assert search_query_candidates("Artist", " / Other") == [("Artist", " / Other")]
    assert search_query_candidates("Artist", "Song / Official Video") == [
        ("Artist", "Song / Official Video")
    ]


def test_multiple_slashes_keep_canonical_and_offer_one_fallback():
    assert search_query_candidates("Uploader", "Song / Real Artist / Extra") == [
        ("Uploader", "Song / Real Artist / Extra"),
        ("Real Artist / Extra", "Song"),
    ]


def test_strips_site_suffixes_and_video_noise():
    assert normalise_yt_meta("Artist", "Song | YouTube Music") == (
        "Artist",
        "Song",
    )
    assert normalise_yt_meta("Artist", "Song (Official Music Video)") == (
        "Artist",
        "Song",
    )
    assert normalise_yt_meta("Artist", "Song [Lyric Video]") == (
        "Artist",
        "Song",
    )


def test_keeps_musical_version_label():
    assert normalise_yt_meta("Artist", "Song (acoustic ver.)") == (
        "Artist",
        "Song (acoustic ver.)",
    )


def test_splits_browser_artist_title_when_artist_is_empty():
    assert normalise_yt_meta(
        "", "Official髭男dism - らしさ [Official Audio]"
    ) == ("Official髭男dism", "らしさ")


def test_strips_repeated_artist_prefix_when_artist_present():
    assert normalise_yt_meta(
        "Official髭男dism",
        "Official髭男dism - らしさ [Official Audio]",
    ) == ("Official髭男dism", "らしさ")


def test_does_not_split_subtitle_dash_when_artist_present():
    assert normalise_yt_meta("Artist", "Song - Subtitle") == (
        "Artist",
        "Song - Subtitle",
    )


def test_drops_placeholder_site_title_only_without_artist():
    assert normalise_yt_meta("", "YouTube Music") == ("", "")
    assert normalise_yt_meta("Artist", "YouTube Music") == (
        "Artist",
        "YouTube Music",
    )


def test_empty_strings_are_safe():
    assert normalise_yt_meta("", "") == ("", "")
    assert search_query_candidates("", "") == [("", "")]


def test_enriches_missing_artist_and_album_for_same_track():
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


def test_does_not_enrich_different_title_or_duration():
    base = {
        "title": "らしさ",
        "artist": "",
        "album": "",
        "trackid": "/",
        "length_ms": 254000,
    }
    different_title = {
        "title": "Pretender",
        "artist": "Official髭男dism",
        "album": "Traveler",
        "trackid": "/",
        "length_ms": 254000,
    }
    different_duration = {
        "title": "らしさ",
        "artist": "Official髭男dism",
        "album": "Traveler",
        "trackid": "/",
        "length_ms": 300000,
    }

    assert enrich_missing_meta(base, [different_title])[1] is False
    assert enrich_missing_meta(base, [different_duration])[1] is False
