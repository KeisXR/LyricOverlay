# Lyricaod Architecture

この文書は、現在のLyricaodの責務境界とruntime flowを説明します。歴史的な初期案は`docs/design.md`に残していますが、実装判断ではこの文書とコードを優先してください。

## 1. Repository layout

```text
src/lyricaod/
  __init__.py
  __main__.py
  _version.py
  main.py
  diagnostics.py
  logging_setup.py
  runtime_status.py
  meta_utils.py
  config/
    settings.py
  player/
    __init__.py
    mpris.py
    smtc.py
    browser_ws.py
  lyrics/
    lrc_parser.py
    lrclib.py
    syncedlyrics_client.py
    ranking.py
    provider_selector.py
    cache_repository.py
    manager.py
  ui/
    overlay.py
    tray.py
    settings_dialog.py
    color_utils.py
    kwin_rules.py
extension/
packaging/
scripts/
tests/
```

一部のmoduleは対応PRのmerge前にはまだ存在しません。docs PRは、#32〜#45をすべてmergeした最終状態を対象にしています。

## 2. Runtime ownership

`lyricaod.main.Application`がlong-lived serviceを所有します。

```text
QApplication
Settings
Native player listener (MPRIS or SMTC)
BrowserWsListener
UnifiedPlayerListener
LyricsManager
OverlayWindow
TrayIcon or TraylessFallback
```

各backendは自身の状態だけを更新し、UIや他backendのprivate stateを直接変更しません。

## 3. Playback state

### Linux MPRIS

`MprisListener`はMPRIS playerごとに次を保持します。

- playback status
- metadata
- position anchor
- monotonic observation time
- playback rate
- measured sync latency

非active playerのPosition、Rate、Seekedはactive timelineを変更しません。active playerが切り替わり、対象がPlayingなら直ちにPositionを同期します。

### Windows SMTC

SMTCはsnapshotをpollします。snapshot適用時は、metadata、player list、active player、status、timelineを先に確定し、その後Signalを発火します。

fallback interpolationでは、SMTCの報告位置を前回のraw報告値ではなくmonotonic clockから計算した予測位置と比較します。pause中のwall timeは位置へ加算せず、rate変更時は旧rateで経過を確定してから新anchorを作ります。

### Browser bridge

browser extensionはloopback WebSocketへversion 1 payloadを送信します。

```json
{
  "protocol_version": 1,
  "sequence": 42,
  "observed_at_ms": 1700000000000,
  "source": {
    "instance_id": "...",
    "tab_id": 12,
    "frame_id": 0
  },
  "state": {
    "title": "...",
    "artist": "...",
    "album": "...",
    "status": "Playing",
    "position": 12.34,
    "duration": 180.0,
    "rate": 1.0
  }
}
```

接続状態とactive media状態は別です。WebSocket接続だけではbrowser playerを選択しません。titleを持つPlaying／Paused stateが新鮮な間だけ候補となり、stale、Stopped、空metadata、全connection切断でnative backendへ戻ります。

extension service workerはtab／frame単位でstateを保持し、Playing、audible、active tab、top frame、鮮度を評価して代表sourceを選びます。

## 4. Player arbitration

`UnifiedPlayerListener`がUIへ1つのplayer interfaceを提供します。

優先順位:

1. 利用可能なpinned player
2. activeかつ新鮮なbrowser media
3. native backendが選んだactive player
4. playerなし

browser transportが接続済みでもmediaがidleならnative playerを維持します。

## 5. Metadata normalization

`normalise_yt_meta()`はcanonical metadataを保守的にcleanします。

- ` - Topic`
- YouTube／Spotify site suffix
- Official Video／Lyric Video等
- artistが空の場合の明確な`Artist - Title`

`Love / Hate`のような正しいtitleを破壊しないため、artistが既に存在する`title / artist`形式はcanonical値を置換しません。可能性のある解釈は`search_query_candidates()`が追加queryとして返します。

## 6. Lyrics acquisition

### Request lifecycle

`LyricsManager`はfetch開始ごとにgenerationを更新します。空title、source無効、cache hitを含む全経路で前のworker resultはstaleとなり、UIへ反映されません。

workerは協調cancelを受け、GUI threadは`wait()`でnetwork完了を待ちません。結果採用はgeneration一致で決定します。

### Candidate ranking

provider resultは`LyricsCandidate`相当のmetadataへ変換し、pure ranking functionで評価します。

```text
title similarity
artist similarity + token overlap
duration proximity
album similarity
synced / plain / instrumental quality
```

高信頼の同期歌詞はfast pathで確定できます。Syncedlyricsがplainのみの場合はLRClibのsynced候補も確認します。title-only fallbackも同じthresholdを通します。

### Alternatives

alternativesはprimaryと同じcandidate集合に保存されます。ユーザーが選択するとselected candidate IDを更新し、旧primaryは候補として残るため可逆です。

## 7. Cache

`CacheRepository`がSQLiteを所有し、`LyricsManager`からDB操作を分離します。

schema version 3の主な項目:

```text
key
data
negative
created_at
accessed_at
expires_at
```

canonical keyは正規化済みartist、title、album、5秒単位のduration bucketから生成します。MPRIS／browserの一時的track IDはaliasとして使用できます。

- 読み出し時TTL
- hit時`accessed_at`更新
- `accessed_at`順の真のLRU
- positive cacheのoffline表示
- not-foundのみ短いnegative TTL
- force refreshでpositive／negative cacheを迂回
- corrupt rowの局所削除
- corrupt databaseの退避と再作成
- version 2 rowの安全なmigration
- raw LRCのverbatim保存

## 8. LRC parsing

parserはheaderとline timestampを分離して処理し、global `[offset:]`を最終段階で一度だけline／wordへ適用します。

対応:

- basic timestamp
- multi-timestamp
- Enhanced word timing
- BOM／CRLF
- 最大millisecond精度
- 同一timestampのstable order

multi-timestamp Enhanced LRCでは、word timingを最初のline timestampからの相対差で各複製lineへ移動します。安全に解釈できない非単調・baseより前のtimingは`words=None`として行単位表示へfallbackします。

## 9. UI

`OverlayWindow`は`QPainter`で描画します。

- current lineを可能な範囲で中央に配置
- 曲末尾では最終pageへclampし、設定行数を維持
- plain lyricsはwheel／PageUp／PageDownで移動
- window幅・高さをscreen available geometryで制限
- 長行はelide
- control hit rectangleはloading／歌詞切替時にclear
- animation objectを再利用
- 最終word durationを近傍timingからbounded推定

60Hzのposition更新では、line／word progressが変化した場合だけ必要な再描画を行います。

## 10. Settings

`Settings`は既知keyをdefault schemaに基づいて型・範囲検証します。未知keyはforward compatibilityのため保持します。

保存:

```text
same-directory temporary file
json write
flush + fsync
os.replace
```

内部`set()`と外部file reloadは同じ差分Signalへ流れます。fileと親directoryを監視し、editorのatomic replace後もwatch pathを再登録します。UI previewは即時、disk writeは短時間debounceします。

## 11. KWin and tray

system tray作成に失敗した場合は`TraylessFallback`がoverlayを表示状態へ戻し、ユーザーを操作不能にしません。

KWin ruleは専用ID`lyricaod-overlay-v1`だけを`kreadconfig`／`kwriteconfig`経由で管理します。`kwinrulesrc`全体を一般的なINI parserで再serializeしません。

## 12. Logging and diagnostics

`logging_setup.py`は2 MiB・5世代のrotating logをplatform-appropriateなstate directoryへ保存します。

- credential／token redaction
- timestamp付きlyrics line redaction
- INFOではartist／titleを記録しない
- packaged GUIのlegacy `print()`をruntime hookでfile logへ転送
- source実行ではstderrも使用可能

`--diagnose`はevent loopを開始せずJSONを出力します。`--self-test`は外部serviceなしでimports、Qt、SQLite、config、loggingを確認します。

## 13. Packaging and CI

正式entry point:

```text
lyricaod
python -m lyricaod
```

PyInstallerも`lyricaod.__main__`をentryにします。

CI順序:

```text
compile
Ruff
incremental mypy
unit / offscreen Qt tests
platform tests
PyInstaller build
packaged --self-test
checksum verification
artifact upload
```

runtime、build、development dependencyは別のlock fileで固定します。外部LRClib testは`integration` markerでdefault runから分離します。

## 14. Shutdown

`aboutToQuit`で次を実行します。

- lyrics workerのgeneration invalidationとcancel
- cache connection close
- player listener停止
- WebSocket server停止
- pending settings flush

recoverableな停止失敗はlogへ記録し、他serviceのcleanupを継続します。
