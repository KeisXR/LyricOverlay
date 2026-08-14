"""Typed runtime states suitable for UI and diagnostic reporting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuntimeStatus(str, Enum):
    STARTING = "starting"
    WAITING_FOR_PLAYER = "waiting_for_player"
    LOADING_LYRICS = "loading_lyrics"
    READY = "ready"
    CACHE_HIT = "cache_hit"
    STALE_CACHE = "stale_cache"
    NO_LYRICS = "no_lyrics"
    SOURCES_DISABLED = "sources_disabled"
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"
    BROWSER_BRIDGE_ERROR = "browser_bridge_error"


@dataclass(frozen=True)
class RuntimeState:
    status: RuntimeStatus
    message: str
    detail: str = ""


_MESSAGES = {
    RuntimeStatus.STARTING: "起動中...",
    RuntimeStatus.WAITING_FOR_PLAYER: "メディアの再生待ち...",
    RuntimeStatus.LOADING_LYRICS: "歌詞を取得中...",
    RuntimeStatus.READY: "歌詞を表示中",
    RuntimeStatus.CACHE_HIT: "キャッシュ済みの歌詞を表示中",
    RuntimeStatus.STALE_CACHE: "取得に失敗したため保存済みの歌詞を表示中",
    RuntimeStatus.NO_LYRICS: "歌詞が見つかりません",
    RuntimeStatus.SOURCES_DISABLED: "歌詞ソースがすべて無効です",
    RuntimeStatus.NETWORK_TIMEOUT: "歌詞サービスへの接続がタイムアウトしました",
    RuntimeStatus.NETWORK_ERROR: "歌詞サービスへ接続できません",
    RuntimeStatus.RATE_LIMITED: "歌詞サービスの利用上限に達しました",
    RuntimeStatus.PROVIDER_ERROR: "歌詞サービスでエラーが発生しました",
    RuntimeStatus.BROWSER_BRIDGE_ERROR: "ブラウザ連携を開始できませんでした",
}


def state(status: RuntimeStatus, *, detail: str = "") -> RuntimeState:
    return RuntimeState(status, _MESSAGES[status], detail)


def classify_fetch_error(message: str) -> RuntimeState:
    normalized = (message or "").casefold()
    if "429" in normalized or "rate limit" in normalized or "too many requests" in normalized:
        return state(RuntimeStatus.RATE_LIMITED, detail=message)
    if "timeout" in normalized or "timed out" in normalized:
        return state(RuntimeStatus.NETWORK_TIMEOUT, detail=message)
    if any(
        token in normalized
        for token in (
            "connect",
            "network",
            "dns",
            "name resolution",
            "connection reset",
            "unreachable",
        )
    ):
        return state(RuntimeStatus.NETWORK_ERROR, detail=message)
    return state(RuntimeStatus.PROVIDER_ERROR, detail=message)
