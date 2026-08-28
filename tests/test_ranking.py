import sys

sys.path.insert(0, "src")

from lyrics.ranking import CandidateMetadata, TrackQuery, rank_candidate, rank_candidates


def candidate(**kwargs):
    values = dict(
        provider="test",
        provider_id="1",
        artist="Artist",
        title="Song",
        album="Album",
        duration_ms=180000,
        synced=False,
        plain=True,
        instrumental=False,
    )
    values.update(kwargs)
    return CandidateMetadata(**values)


def test_synced_beats_plain_for_same_metadata():
    query = TrackQuery("Artist", "Song", "Album", 180000)
    ranked = rank_candidates(
        query,
        [
            candidate(provider_id="plain"),
            candidate(provider_id="synced", plain=False, synced=True),
        ],
    )
    assert ranked[0].candidate.provider_id == "synced"
    assert ranked[0].acceptable


def test_wrong_artist_is_not_acceptable_for_title_only_match():
    ranked = rank_candidate(
        TrackQuery("Expected Artist", "Common Song", "", 0),
        candidate(
            artist="Other Artist",
            title="Common Song",
            synced=True,
            plain=False,
        ),
    )
    assert not ranked.acceptable
    assert "artist-below-threshold" in ranked.reasons


def test_large_duration_difference_is_rejected():
    ranked = rank_candidate(
        TrackQuery("Artist", "Song", "", 180000),
        candidate(duration_ms=240000, synced=True, plain=False),
    )
    assert not ranked.acceptable
    assert "duration-below-threshold" in ranked.reasons


def test_version_label_title_matches_by_containment():
    ranked = rank_candidate(
        TrackQuery("みゆはん", "ぼくのフレンド（acoustic ver.）", "", 180000),
        candidate(
            artist="みゆはん",
            title="ぼくのフレンド",
            album="",
            duration_ms=190000,
            synced=True,
            plain=False,
        ),
    )
    assert ranked.acceptable
    assert "relaxed-title-threshold" not in ranked.reasons


def test_relaxed_title_threshold_accepts_notation_variant():
    ranked = rank_candidate(
        TrackQuery("YOASOBI", "アイドル (Idol)", "", 233000),
        candidate(
            artist="YOASOBI",
            title="Idol",
            album="",
            duration_ms=233000,
            synced=True,
            plain=False,
        ),
    )
    assert ranked.acceptable
    assert "relaxed-title-threshold" in ranked.reasons


def test_relaxed_title_threshold_requires_agreeing_duration():
    ranked = rank_candidate(
        TrackQuery("YOASOBI", "アイドル (Idol)", "", 233000),
        candidate(
            artist="YOASOBI",
            title="Idol",
            album="",
            duration_ms=253000,
            synced=True,
            plain=False,
        ),
    )
    assert not ranked.acceptable


def test_partial_word_title_overlap_is_not_containment():
    ranked = rank_candidate(
        TrackQuery("Artist", "Go", "", 0),
        candidate(
            artist="Artist",
            title="Going Under",
            album="",
            duration_ms=0,
            synced=True,
            plain=False,
        ),
    )
    assert not ranked.acceptable


def test_alternate_query_can_match_without_mutating_canonical_query():
    canonical = TrackQuery("Uploader", "Song / Real Artist", "", 180000)
    alternate = TrackQuery("Real Artist", "Song", "", 180000)
    ranked = rank_candidates(
        [canonical, alternate],
        [
            candidate(
                artist="Real Artist",
                title="Song",
                synced=True,
                plain=False,
            )
        ],
    )
    assert ranked[0].acceptable
    assert ranked[0].matched_query == alternate
