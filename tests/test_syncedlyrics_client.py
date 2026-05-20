"""Tests for the syncedlyrics wrapper."""

import sys

sys.path.insert(0, "src")

from lyrics.syncedlyrics_client import get_syncedlyrics


class _FakeSyncedLyrics:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, search_term, **kwargs):
        self.calls.append((search_term, kwargs))
        return self.responses.pop(0)


def _with_fake_module(fake, fn):
    original = sys.modules.get("syncedlyrics")
    sys.modules["syncedlyrics"] = fake
    try:
        return fn()
    finally:
        if original is None:
            sys.modules.pop("syncedlyrics", None)
        else:
            sys.modules["syncedlyrics"] = original


def test_get_syncedlyrics_prefers_synced_enhanced_result():
    fake = _FakeSyncedLyrics(["[00:01.00]Line"])

    def run():
        result = get_syncedlyrics("Artist", "Title", enhanced=True)
        assert result is not None
        assert result.synced_lyrics == "[00:01.00]Line"
        assert result.plain_lyrics is None

    _with_fake_module(fake, run)

    assert fake.calls == [
        ("Artist Title", {"enhanced": True, "synced_only": True}),
    ]


def test_get_syncedlyrics_uses_plain_only_for_plain_fallback():
    fake = _FakeSyncedLyrics([None, "plain lyrics"])

    def run():
        result = get_syncedlyrics("Artist", "Title", enhanced=True)
        assert result is not None
        assert result.synced_lyrics is None
        assert result.plain_lyrics == "plain lyrics"

    _with_fake_module(fake, run)

    assert fake.calls == [
        ("Artist Title", {"enhanced": True, "synced_only": True}),
        ("Artist Title", {"plain_only": True}),
    ]


if __name__ == "__main__":
    import traceback

    tests = [
        (
            "get_syncedlyrics_prefers_synced_enhanced_result",
            test_get_syncedlyrics_prefers_synced_enhanced_result,
        ),
        (
            "get_syncedlyrics_uses_plain_only_for_plain_fallback",
            test_get_syncedlyrics_uses_plain_only_for_plain_fallback,
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
