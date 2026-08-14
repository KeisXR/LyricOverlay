"""Durable SQLite repository for lyrics candidates and cache selection."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ranking import normalise_match_text

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 3


@dataclass(frozen=True)
class CacheRecord:
    key: str
    payload: dict | None
    negative: bool
    created_at: float
    accessed_at: float
    expires_at: float


class CacheRepository:
    """Store cache records with read-time TTL and true LRU semantics."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_entries: int = 10000,
        clock: Callable[[], float] | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time
        self._max_entries = max(1, int(max_entries))
        self._closed = False
        self._conn = self._open_connection()
        self._migrate()
        self.prune()

    def _open_connection(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.path))
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("SELECT 1")
            return connection
        except sqlite3.DatabaseError:
            try:
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
            corrupt = self.path.with_name(
                f"{self.path.name}.corrupt-{int(self._clock())}"
            )
            try:
                if self.path.exists():
                    self.path.replace(corrupt)
            except OSError:
                logger.exception("Unable to preserve corrupt cache database")
                self.path.unlink(missing_ok=True)
            connection = sqlite3.connect(str(self.path))
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection

    @staticmethod
    def canonical_key(
        artist: str,
        title: str,
        album: str = "",
        duration_ms: int = 0,
    ) -> str:
        duration_bucket = round(max(0, int(duration_ms)) / 5000) if duration_ms else 0
        raw = "|".join(
            (
                normalise_match_text(artist),
                normalise_match_text(title),
                normalise_match_text(album),
                str(duration_bucket),
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def alias_key(value: str) -> str:
        return hashlib.sha256(f"alias|{value}".encode("utf-8")).hexdigest()

    def _migrate(self):
        try:
            with self._conn:
                self._create_schema()
                self._migrate_v2_rows()
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except sqlite3.DatabaseError as exc:
            logger.exception("Cache migration failed; old tables were left intact: %s", exc)
            self._conn.rollback()
            with self._conn:
                self._create_schema()
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _create_schema(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                negative INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                accessed_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_aliases (
                alias TEXT PRIMARY KEY,
                key TEXT NOT NULL REFERENCES cache_entries(key) ON DELETE CASCADE
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_accessed ON cache_entries(accessed_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at)"
        )

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return bool(row)

    def _migrate_v2_rows(self):
        if not self._table_exists("cache"):
            return
        marker = (
            self._conn.execute(
                "SELECT value FROM cache_metadata WHERE key='v2_migrated'"
            ).fetchone()
            if self._table_exists("cache_metadata")
            else None
        )
        if marker:
            return
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache_metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        rows = self._conn.execute("SELECT key, data, fetched_at FROM cache").fetchall()
        for legacy_key, raw_data, fetched_at in rows:
            try:
                old = json.loads(raw_data)
                if not isinstance(old, dict) or "primary" not in old:
                    continue
                entries = [old["primary"], *old.get("alternatives", [])]
                candidates = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    migrated = dict(entry)
                    raw_lyrics = (
                        migrated.get("lrc_raw")
                        or migrated.get("plain_text")
                        or ""
                    )
                    candidate_id = hashlib.sha256(
                        json.dumps(
                            migrated, sort_keys=True, ensure_ascii=False
                        ).encode("utf-8")
                    ).hexdigest()
                    migrated.setdefault("candidate_id", candidate_id)
                    migrated.setdefault("provider_id", "")
                    migrated.setdefault("raw_lyrics", raw_lyrics)
                    candidates.append(migrated)
                if not candidates:
                    continue
                primary = candidates[0]
                key = self.canonical_key(
                    primary.get("artist", ""),
                    primary.get("title", ""),
                )
                payload = {
                    "version": SCHEMA_VERSION,
                    "candidates": candidates,
                    "selected_id": primary["candidate_id"],
                }
                created = float(fetched_at or self._clock())
                expires = created + 30 * 86400
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO cache_entries
                    (key, data, negative, created_at, accessed_at, expires_at)
                    VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (
                        key,
                        json.dumps(payload, ensure_ascii=False),
                        created,
                        created,
                        expires,
                    ),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO cache_aliases(alias, key) VALUES (?, ?)",
                    (self.alias_key(str(legacy_key)), key),
                )
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
        self._conn.execute(
            "INSERT OR REPLACE INTO cache_metadata(key, value) VALUES ('v2_migrated', '1')"
        )

    def _resolve_key(self, key: str, aliases: list[str] | tuple[str, ...]) -> str:
        row = self._conn.execute(
            "SELECT key FROM cache_entries WHERE key=?", (key,)
        ).fetchone()
        if row:
            return key
        for alias in aliases:
            row = self._conn.execute(
                "SELECT key FROM cache_aliases WHERE alias=?", (self.alias_key(alias),)
            ).fetchone()
            if row:
                return str(row[0])
        return key

    def get(
        self,
        key: str,
        *,
        aliases: list[str] | tuple[str, ...] = (),
    ) -> CacheRecord | None:
        resolved = self._resolve_key(key, aliases)
        row = self._conn.execute(
            """
            SELECT data, negative, created_at, accessed_at, expires_at
            FROM cache_entries WHERE key=?
            """,
            (resolved,),
        ).fetchone()
        if not row:
            return None
        raw_data, negative, created, accessed, expires = row
        now = self._clock()
        if float(expires) <= now:
            self.delete(resolved)
            return None
        payload = None
        if not negative:
            try:
                decoded = json.loads(raw_data)
                if not isinstance(decoded, dict):
                    raise ValueError("cache payload is not an object")
                payload = decoded
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Discarding corrupt cache row %s", resolved)
                self.delete(resolved)
                return None
        self._conn.execute(
            "UPDATE cache_entries SET accessed_at=? WHERE key=?", (now, resolved)
        )
        self._conn.commit()
        return CacheRecord(
            key=resolved,
            payload=payload,
            negative=bool(negative),
            created_at=float(created),
            accessed_at=now,
            expires_at=float(expires),
        )

    def put(
        self,
        key: str,
        payload: dict,
        *,
        ttl_seconds: float,
        aliases: list[str] | tuple[str, ...] = (),
    ) -> None:
        self._put_record(
            key,
            json.dumps(payload, ensure_ascii=False),
            negative=False,
            ttl_seconds=ttl_seconds,
            aliases=aliases,
        )

    def put_negative(
        self,
        key: str,
        *,
        ttl_seconds: float = 900,
        aliases: list[str] | tuple[str, ...] = (),
    ) -> None:
        self._put_record(
            key,
            "{}",
            negative=True,
            ttl_seconds=ttl_seconds,
            aliases=aliases,
        )

    def _put_record(
        self,
        key: str,
        data: str,
        *,
        negative: bool,
        ttl_seconds: float,
        aliases: list[str] | tuple[str, ...],
    ) -> None:
        now = self._clock()
        expires = now + max(1.0, float(ttl_seconds))
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO cache_entries
                    (key, data, negative, created_at, accessed_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    data=excluded.data,
                    negative=excluded.negative,
                    accessed_at=excluded.accessed_at,
                    expires_at=excluded.expires_at
                """,
                (key, data, int(negative), now, now, expires),
            )
            for alias in aliases:
                if alias:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO cache_aliases(alias, key) VALUES (?, ?)",
                        (self.alias_key(alias), key),
                    )
        self.enforce_max_entries()

    def delete(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM cache_entries WHERE key=?", (key,))

    def prune(self) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM cache_entries WHERE expires_at<=?", (self._clock(),)
            )
        self.enforce_max_entries()

    def enforce_max_entries(self) -> None:
        row = self._conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()
        count = int(row[0]) if row else 0
        overflow = count - self._max_entries
        if overflow <= 0:
            return
        with self._conn:
            self._conn.execute(
                """
                DELETE FROM cache_entries WHERE key IN (
                    SELECT key FROM cache_entries
                    ORDER BY accessed_at ASC, created_at ASC
                    LIMIT ?
                )
                """,
                (overflow,),
            )

    def set_max_entries(self, value: int) -> None:
        self._max_entries = max(1, int(value))
        self.enforce_max_entries()

    def set_positive_ttl(self, ttl_seconds: float) -> None:
        ttl = max(1.0, float(ttl_seconds))
        with self._conn:
            self._conn.execute(
                """
                UPDATE cache_entries
                SET expires_at = created_at + ?
                WHERE negative = 0
                """,
                (ttl,),
            )
        self.prune()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._conn.close()
