# Troubleshooting

## 1. まず診断を実行する

```bash
lyricaod --diagnose
lyricaod --self-test
```

`--diagnose`はJSONを出力し、version、platform、Python、Qt platform、config/cache/log path、dependency import状態、設定されたsourceやbrowser portを確認できます。

`--self-test`は外部APIや実playerへ接続せず、主要module、Qt offscreen、SQLite、設定保存領域、file loggingを確認します。終了code 0が成功、1が失敗です。

GUIが起動する場合はtray menuから次を利用できます。

- **ログフォルダを開く**
- **診断情報をコピー**

## 2. ログの場所

Linux:

```text
$XDG_STATE_HOME/lyricaod/logs/lyricaod.log
```

`XDG_STATE_HOME`未設定時は通常:

```text
~/.local/state/lyricaod/logs/lyricaod.log
```

Windows:

```text
%LOCALAPPDATA%\lyricaod\logs\lyricaod.log
```

logは2 MiB・5世代でrotateします。通常のINFO logにはartist／titleを残さず、credentialやtimestamp付きlyrics lineはredactします。

## 3. Playerが見つからない

### Linux / MPRIS

```bash
gdbus call \
  --session \
  --dest org.freedesktop.DBus \
  --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ListNames
```

出力に`org.mpris.MediaPlayer2.*`があるか確認します。

確認事項:

- desktop sessionのD-Bus上でLyricaodを起動しているか
- player側がMPRISを有効にしているか
- browserとPlasma Browser Integrationが同じ曲を重複報告していないか
- trayのactive playerが固定されていないか。「自動」へ戻して確認する

非active playerのposition eventはactive timelineへ影響しない設計です。再現する場合は、active player、player list、発生時刻を診断情報とともに報告してください。

### Windows / SMTC

確認事項:

- Windowsのmedia overlayに再生中appが表示されるか
- 対象appがSMTCへtitle／artist／timelineを公開しているか
- 設定の「再生位置を自己計算」を切り替えた場合の差
- browser extensionを利用した場合に改善するか

SMTCはWindowsが選んだcurrent sessionを利用するため、任意のsessionを強制的にactiveへできない場合があります。

## 4. ブラウザ拡張が接続しない

1. Lyricaodを起動する
2. 拡張機能の設定画面を開く
3. Lyricaodの`behavior.ws_port`と同じportを設定する
4. defaultは`56789`
5. port変更後はLyricaodを再起動する

確認:

```bash
lyricaod --diagnose
```

診断JSONのbrowser portを確認します。

拡張が接続済みでも、browserに新鮮なtitle付きPlaying／Paused mediaがない場合はactive playerになりません。これは正常です。idle browserがMPRIS／SMTCを奪わないための挙動です。

複数tabで誤った曲が選ばれる場合は、次を確認します。

- 実際に音を出しているtabが`audible`か
- 広告／埋め込みframeがPlaying状態を持っていないか
- tabを閉じた後、stale timeoutを待つとnative playerへ戻るか
- extension service workerのconsoleにprotocol validation errorがないか

## 5. 歌詞が見つからない／別の曲になる

Lyricaodはtitle、artist、album、duration、同期有無を評価します。title-only fallbackの先頭結果は無条件採用しません。

確認事項:

- player metadataのtitle／artistが正しいか
- YouTube uploader名と実artistが異なるか
- live、remix、acoustic等でdurationが大きく違わないか
- `⇄`に代替候補があるか
- sourceがすべて無効になっていないか

表示される状態の意味:

| 表示 | 意味 |
|---|---|
| 歌詞が見つかりません | providerは正常応答したがaccept可能な候補がない |
| 歌詞ソースがすべて無効です | providerを呼べない。positive cacheがあれば表示 |
| タイムアウト | network requestが時間内に完了しなかった |
| 接続できません | DNS／connection等のnetwork error |
| 利用上限 | HTTP 429等 |
| provider error | response parseやprovider固有error |
| キャッシュ済みの歌詞を表示中 | networkを使わずpositive cacheを使用 |
| 保存済みの歌詞を表示中 | refresh失敗後にpositive cacheへfallback |

## 6. 再読み込みしても直らない

「再読み込み」はcacheを迂回してproviderへ問い合わせます。取得失敗時は既存positive cacheへ戻る場合があります。

cacheを完全に確認したい場合は、Lyricaodを終了してからcache DBをbackupしてください。いきなり削除せず、診断用に保存します。

Linux:

```text
~/.cache/lyricaod/cache.db
```

Windows:

```text
%LOCALAPPDATA%\lyricaod\cache.db
```

negative cacheは短時間で、force refreshにより迂回されます。network errorはnegative cacheへ保存しません。

## 7. 歌詞の時刻がずれる

順に確認します。

1. player position自体が正しいか
2. browser extensionを使うと改善するか
3. manual lyrics offsetを±50〜500 msで調整する
4. seek後だけずれるか
5. rate変更後だけずれるか
6. pause/resume後だけずれるか

`[offset:]`はLRC全体へ一度だけ適用され、manual offsetは表示時に追加されます。

## 8. 平文歌詞を最後まで読めない

wheelまたはPageUp／PageDownでpage移動します。overlayにfocusを与えにくい環境ではwheelを使用してください。

## 9. Overlayが大きすぎる／画面外に出る

- `window.width_pct`
- `window.height_px`
- font size
- visible lines
- screen index
- anchor／offset

を確認します。overlayはscreenのavailable geometryを上限とし、長行はelideします。

X11では保存位置をresetできます。programmatic moveはuser dragとして再保存しません。

## 10. KDE Waylandで最前面にならない

```bash
python scripts/install_kwin_rule.py
```

確認事項:

- `kreadconfig6`または`kreadconfig5`
- `kwriteconfig6`または`kwriteconfig5`
- `qdbus6`または`qdbus`
- KWinを使用しているか

rule IDは`lyricaod-overlay-v1`です。Lyricaodはこの専用groupだけを管理します。

compositorによってはLyricaod再起動やsession再loginが必要です。KDE以外のWayland compositorではこのrule機能は使用できません。

## 11. Trayが表示されない

trayがない環境では、Lyricaodはoverlayを表示状態へ戻して操作不能を防ぎます。`--minimized`でもtray作成に失敗した場合は表示可能なUIを残します。

trayを期待する場合は、desktop panelのsystem tray設定とQt platform pluginを確認してください。

## 12. 設定fileが壊れた

Settingsは不正JSONや型不正を安全なdefaultへ戻します。保存はatomic replaceです。

設定path:

```text
Linux:  ~/.config/lyricaod/settings.json
Windows: %APPDATA%\lyricaod\settings.json
```

修復前にfileをbackupし、logのload errorを確認してください。未知keyはforward compatibilityのため保持されます。

## 13. Packageが起動しない

package内で:

```bash
./Lyricaod --self-test
```

Windows:

```powershell
.\Lyricaod.exe --self-test
```

Linux packageではD-Bus／GLib runtimeがhost側に必要です。

packageの整合性:

```bash
python scripts/write_checksums.py verify dist/Lyricaod
```

download済みpackageでは、同梱`SHA256SUMS`と各fileのSHA-256をtrusted toolで比較します。checksum manifest自体を信用できない場合、publisher identityは確認できません。

## 14. 不具合報告に含める情報

- Lyricaod version／commit
- OS、desktop、Wayland／X11
- player名とbackend種別
- 再現手順
- `--diagnose`出力
- 関連するlogの短い範囲
- network／provider／browser extensionを使ったか
- 期待結果と実際の結果

API key、token、歌詞本文、個人情報は削除してください。security issueは[SECURITY.md](../SECURITY.md)に従ってprivateに報告します。
