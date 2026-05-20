"""Lightweight wrapper around the syncedlyrics package."""

from dataclasses import dataclass


@dataclass
class SyncedLyricsResult:
    artist_name: str
    track_name: str
    synced_lyrics: str | None
    plain_lyrics: str | None


def get_syncedlyrics(
    artist: str,
    title: str,
    *,
    enhanced: bool = True,
    providers: tuple[str, ...] | None = None,
) -> SyncedLyricsResult | None:
    """Fetch lyrics using the external syncedlyrics package.

    Returns a best-effort result with synced LRC (enhanced when available),
    otherwise plain lyrics. Returns ``None`` when no lyrics are found.
    """
    try:
        import syncedlyrics  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency issue
        raise RuntimeError(
            "syncedlyrics package is not installed. Run: pip install syncedlyrics"
        ) from exc

    query = f"{artist} {title}".strip()
    if not query:
        return None

    opts: dict = {"enhanced": enhanced}
    if providers:
        opts["providers"] = list(providers)

    synced = syncedlyrics.search(query, synced_only=True, **opts)
    if synced:
        return SyncedLyricsResult(
            artist_name=artist,
            track_name=title,
            synced_lyrics=synced,
            plain_lyrics=None,
        )

    plain = syncedlyrics.search(query, synced_only=False, **opts)
    if plain:
        return SyncedLyricsResult(
            artist_name=artist,
            track_name=title,
            synced_lyrics=None,
            plain_lyrics=plain,
        )
    return None
