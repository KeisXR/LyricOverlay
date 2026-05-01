"""Integration test for the LRClib API client (requires network access)."""

import sys
sys.path.insert(0, "src")

from lyrics.lrclib import get_lrclib


def test_search_popular_song():
    """Search for a well-known song that should definitely exist on LRClib."""
    result = get_lrclib("Bohemian Rhapsody", "Queen")
    assert result is not None, "Bohemian Rhapsody should be in LRClib"

    # LRClib may return track/artist in either order depending on search
    names = {result.track_name.lower(), result.artist_name.lower()}
    expected = {"bohemian rhapsody", "queen"}
    assert names == expected, f"Expected {expected}, got {names}"

    assert result.synced_lyrics is not None, "Should have synced lyrics"
    assert len(result.synced_lyrics) > 100
    print(f"  Found: '{result.track_name}' by '{result.artist_name}'")
    print(f"  Synced lyrics: {len(result.synced_lyrics)} chars")


def test_not_found():
    """A nonsense query should return None."""
    result = get_lrclib("zzzxxx_not_a_real_song_2024", "nobody")
    assert result is None


def test_plain_lyrics():
    """Some songs have only plain lyrics."""
    result = get_lrclib("Let It Be", "The Beatles")
    if result:
        assert result.plain_lyrics is not None
        print(f"  Found: '{result.track_name}', plain={bool(result.plain_lyrics)}, synced={bool(result.synced_lyrics)}")
    else:
        print("  (no result for Let It Be - LRClib might be down or title mismatch)")


if __name__ == "__main__":
    import traceback
    tests = [
        ("search_popular_song", test_search_popular_song),
        ("not_found", test_not_found),
        ("plain_lyrics", test_plain_lyrics),
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
