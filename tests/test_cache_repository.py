import json
import sqlite3
import sys

sys.path.insert(0, "src")

from lyrics.cache_repository import CacheRepository


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_cache_hit_updates_accessed_at_and_true_lru(tmp_path):
    clock = Clock()
    repo = CacheRepository(tmp_path / "cache.db", max_entries=2, clock=clock)
    repo.put("a", {"value": "a"}, ttl_seconds=100)
    clock.advance(1)
    repo.put("b", {"value": "b"}, ttl_seconds=100)
    clock.advance(1)
    assert repo.get("a").accessed_at == clock.value
    clock.advance(1)
    repo.put("c", {"value": "c"}, ttl_seconds=100)

    assert repo.get("a") is not None
    assert repo.get("b") is None
    assert repo.get("c") is not None


def test_expired_record_is_rejected_without_restart(tmp_path):
    clock = Clock()
    repo = CacheRepository(tmp_path / "cache.db", clock=clock)
    repo.put("a", {"value": "a"}, ttl_seconds=5)
    clock.advance(6)
    assert repo.get("a") is None


def test_canonical_key_uses_album_and_duration_not_track_id(tmp_path):
    repo = CacheRepository(tmp_path / "cache.db")
    base = repo.canonical_key("Artist", "Song", "Album", 180000)
    assert base == repo.canonical_key("artist", "song", "album", 180900)
    assert base != repo.canonical_key("Artist", "Song", "Other", 180000)
    assert base != repo.canonical_key("Artist", "Song", "Album", 240000)


def test_alias_resolves_unstable_source_identifier(tmp_path):
    repo = CacheRepository(tmp_path / "cache.db")
    repo.put("canonical", {"value": 1}, ttl_seconds=100, aliases=["track-a"])
    assert repo.get("missing", aliases=["track-a"]).key == "canonical"


def test_corrupt_row_isolated_from_other_entries(tmp_path):
    repo = CacheRepository(tmp_path / "cache.db")
    repo.put("good", {"value": 1}, ttl_seconds=100)
    now = repo._clock()
    repo._conn.execute(
        "INSERT INTO cache_entries VALUES (?, ?, 0, ?, ?, ?)",
        ("bad", "{not-json", now, now, now + 100),
    )
    repo._conn.commit()

    assert repo.get("bad") is None
    assert repo.get("good").payload == {"value": 1}


def test_negative_cache_has_independent_ttl(tmp_path):
    clock = Clock()
    repo = CacheRepository(tmp_path / "cache.db", clock=clock)
    repo.put_negative("missing", ttl_seconds=10)
    assert repo.get("missing").negative
    clock.advance(11)
    assert repo.get("missing") is None


def test_v2_rows_migrate_without_destroying_old_table(tmp_path):
    path = tmp_path / "cache.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE cache (key TEXT PRIMARY KEY, data TEXT, fetched_at REAL)"
    )
    payload = {
        "version": 2,
        "primary": {
            "artist": "Artist",
            "title": "Song",
            "synced": False,
            "plain_text": "lyrics",
            "lrc_raw": "",
            "source": "lrclib",
        },
        "alternatives": [],
    }
    connection.execute(
        "INSERT INTO cache VALUES (?, ?, ?)",
        ("legacy-key", json.dumps(payload), 1000.0),
    )
    connection.commit()
    connection.close()

    repo = CacheRepository(path, clock=Clock(1001.0))
    record = repo.get("new-key", aliases=["legacy-key"])

    assert record is not None
    assert record.payload["candidates"][0]["title"] == "Song"
    assert repo._table_exists("cache")


def test_corrupt_database_is_preserved_and_recreated(tmp_path):
    path = tmp_path / "cache.db"
    path.write_bytes(b"not a sqlite database")
    repo = CacheRepository(path, clock=Clock(1234.0))
    repo.put("good", {"value": 1}, ttl_seconds=10)

    assert repo.get("good") is not None
    assert (tmp_path / "cache.db.corrupt-1234").exists()
