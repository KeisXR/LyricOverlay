# Agent Instructions for Lyricaod

このファイルは、Lyricaodを変更する自動化agentと開発者が守るべき不変条件を定義します。現在のarchitectureは`docs/architecture.md`、実務手順は`docs/handoff.md`を参照してください。

## Scope

- 1 Issueにつき1 PRを原則とする
- unrelatedなformat変更・rename・機能追加を混ぜない
- behavior変更には回帰testを追加する
- private memberを他classから直接参照しない。public method／Signalを追加する
- platform固有処理は、対象外platformのpackage importを壊さない

## Request lifecycle

1. 後から開始したlyrics requestが必ず勝つ。
2. cache hit、空title、source無効を含むすべてのfetch経路でgenerationを更新する。
3. stale workerの成功・失敗Signalは現在のUI状態を変更しない。
4. GUI threadをnetwork workerの`wait()`でblockしない。
5. user reloadはpositive／negative cacheを迂回できる。
6. reload失敗時のpositive cache fallbackと、通常のnot-foundを区別する。

## Player state

1. 非active playerはactive timelineを変更しない。
2. position anchor、observation time、rateはplayer／sourceごとに所有する。
3. active player変更Signalの受信時点で、新しいmetadataが読める状態にする。
4. WebSocket接続だけではactive mediaとみなさない。
5. browser stateはfreshなtitle付きPlaying／Pausedに限定する。
6. pause中のwall timeをpositionへ加算しない。
7. rate変更は旧rateの経過を確定してから新anchorを作る。

## Lyrics and metadata

1. canonical metadataを検索用heuristicで破壊しない。
2. `title / artist`等は追加query候補として扱う。
3. synced／plainだけでなくtitle、artist、duration、albumの一致を評価する。
4. title-only fallbackの先頭結果を無条件採用しない。
5. lyrics本文をINFO logへ出さない。
6. LRC global offsetを一度だけ適用する。
7. 安全に解釈できないword timingは誤った値として保持せず、line表示へfallbackする。

## Cache

1. TTLはread時にも検証する。
2. LRUはaccess時刻を更新する。
3. 一時的track IDを唯一のcanonical keyにしない。
4. raw lyricsを可能な限りverbatim保存する。
5. corrupt rowは局所無効化し、DB全体を起動不能にしない。
6. not-foundだけをnegative cacheへ保存し、network errorは保存しない。
7. selected candidateと旧primaryを候補集合として保持する。
8. migrationはtransactionalかつ旧dataを不用意に破壊しない。

## Settings

1. 設定値は読み込み時と`set()`時に型・範囲検証する。
2. settings saveはsame-directory temporary fileとatomic replaceを使う。
3. internal changeとexternal reloadを同じ変更通知経路へ流す。
4. sliderの各tickでdisk write／DB cleanupを同期実行しない。
5. programmatic moveとuser dragを区別する。
6. 保存失敗時は最後の有効fileを維持する。

## Browser protocol

1. protocol version、source identity、sequence、timestampを検証する。
2. message size、文字列長、finite数値、rate／duration／position範囲を制限する。
3. tab／frameごとにstateを管理する。
4. tab close、navigation、pagehide、stale timeoutでstateを削除する。
5. 1接続の切断で他connectionまで未接続扱いしない。
6. port変更はappとextensionの双方で明示的に行う。

## UI

1. loading／empty／track change時に古いhit rectangleをclearする。
2. screen geometryを超えるwindowを作らない。
3. animation QObjectを無制限に生成しない。
4. 60Hz position pathで不要なlayout・DB・file I/Oを行わない。
5. plain lyricsを最後まで閲覧可能にする。
6. track末尾でも可能な限り設定行数を表示する。
7. trayがなくてもuserが表示または終了できる。

## Security and logging

1. Trusted Rootへ自己署名certificateを追加しない。
2. private key、certificate password、API key、pairing tokenをcommitしない。
3. credential／token／lyricsをlogでredactする。
4. checksumはpublisher identityではなくintegrity確認として説明する。
5. recoverable exceptionを無言で握り潰さずcontext付きで記録する。
6. logging failure自体でappを起動不能にしない。

## Tests and CI

1. default unit testは外部networkへ接続しない。
2. live service testは`integration` markerへ分離する。
3. platform state machineはfake clock／mock backendでtestする。
4. Qt UI testはoffscreenで再現可能にする。
5. package artifactはupload前に`--self-test`を通す。
6. test失敗時にartifactを公開しない。
7. lock file変更は意図と影響をPR本文へ記載する。

## Documentation

実装変更時は、次のうち影響する文書を同じPRまたは後続docs PRで更新します。

```text
README.md
docs/architecture.md
docs/handoff.md
docs/troubleshooting.md
CONTRIBUTING.md
SECURITY.md
```

実装されていない機能を現在利用可能であるように記載しないでください。
