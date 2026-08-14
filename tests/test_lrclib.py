"""Live LRClib integration tests.

These tests are excluded from the default pytest run. Execute explicitly with
``pytest -m integration tests/test_lrclib.py`` when validating the external
service contract.
"""

import sys

import pytest

sys.path.insert(0, "src")

from lyrics.lrclib import get_lrclib


pytestmark = pytest.mark.integration


def test_search_popular_song():
    result = get_lrclib("Queen", "Bohemian Rhapsody")
    assert result is not None, "Bohemian Rhapsody should be in LRClib"
    assert result.track_name.casefold() == "bohemian rhapsody"
    assert result.artist_name.casefold() == "queen"
    assert result.synced_lyrics is not None
    assert len(result.synced_lyrics) > 100


def test_not_found():
    result = get_lrclib("nobody", "zzzxxx_not_a_real_song_2024")
    assert result is None


def test_plain_lyrics():
    result = get_lrclib("The Beatles", "Let It Be")
    if result:
        assert result.plain_lyrics is not None
