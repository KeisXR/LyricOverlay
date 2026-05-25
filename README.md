# Lyricaod

KDE Plasma 向けのデスクトップ歌詞オーバーレイ。MPRIS/SMTC とブラウザ拡張から再生情報を取得し、Syncedlyrics / LRClib で同期歌詞を表示します。

## 特長

- **同期歌詞表示** — LRC 行ハイライト + 単語単位ハイライト (Enhanced LRC)
- **歌詞ソース** — Syncedlyrics 優先、失敗時は LRClib にフォールバック。LRClib から代替候補を取得可能
- **プレイヤー連携** — Linux: MPRIS (D-Bus)、Windows: SMTC。ブラウザ拡張 (WebSocket) で高精度の再生位置も取得
- **透明オーバーレイ** — フレームレス・常に手前・フォーカスを奪わない
- **ホバー操作** — 閉じる/再同期/代替歌詞のボタンがマウスホバーで表示
- **自動リサイズ** — 表示テキスト量に合わせて最小化
- **シークバー** — 再生位置の進捗バー
- **SQLite キャッシュ** — TTL + LRU のキャッシュ
- **設定ダイアログ** — 表示/位置/同期/動作/歌詞ソースを GUI で調整
- **設定のホットリロード** — settings.json の編集が即時反映
- **システムトレイ** — 表示切替やフォント変更などをトレイから操作

## 対応環境

- **KDE Plasma 6** (Wayland / X11)
- **Windows 10/11** (実験的)
- **Python 3.12+**

### 依存関係

- Python 依存は `requirements.txt`
- Linux の追加パッケージ: `pyside6`, `python-dbus`, `python-gobject`
- ブラウザ連携には `websockets` が必要 (requirements に含まれます)

## インストール

### 配布ビルド

GitHub Actions / Releases のビルドには Python と依存が含まれます。

- **Windows**: `Lyricaod-windows` を展開して `Lyricaod.exe`
- **Linux**: `Lyricaod-linux` を展開して `Lyricaod`

Linux はデスクトップの D-Bus / GObject ランタイムが必要です。Ubuntu/Debian なら `python3-dbus python3-gi gir1.2-glib-2.0` を入れてください。

### ローカルでパッケージを作る

```powershell
# Windows PowerShell
.\scripts\build_windows.ps1
```

```bash
# Linux
bash scripts/build_linux.sh
```

出力は `dist/Lyricaod/`。`build/` は PyInstaller の一時ファイルのみです。

### ソースから実行

#### Windows

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
.\.venv\Scripts\python.exe src\main.py
```

#### Arch Linux / Manjaro

```bash
sudo pacman -S pyside6 python-dbus python-gobject python-httpx
git clone https://github.com/KeisXR/lyricaod.git
cd lyricaod
python src/main.py
```

#### その他の Linux

```bash
git clone https://github.com/KeisXR/lyricaod.git
cd lyricaod
bash setup.sh
.venv/bin/python src/main.py
```

## ブラウザ連携

`extension/` を Chrome/Edge/Brave などで「パッケージ化されていない拡張機能」として読み込むと、YouTube Music などの再生位置を高精度に取得できます。

- 既定ポート: `56789` (`settings.json` の `behavior.ws_port`)
- 接続状態は設定画面の「ブラウザ連携」とトレイで確認できます
- ポート変更はアプリ再起動が必要

## 使い方

```bash
# 通常起動
python src/main.py

# トレイのみで起動
python src/main.py --minimized
```

### トレイメニュー

| 項目 | 内容 |
|------|------|
| **歌詞を表示** | オーバーレイ表示の切り替え |
| **設定...** | 設定ダイアログを開く |
| **有効なプレイヤー** | 検出されたプレイヤーの切り替え |
| **ブラウザ接続: ...** | ブラウザ拡張の接続状態表示 |
| **表示行数** | 3 / 5 / 7 / 10 行 |
| **歌詞オフセット** | ±1000 ms の同期調整 |
| **フォントを選択...** | フォント選択ダイアログ |
| **文字背景** | 文字背景のオン/オフ |
| **常に手前に表示** | 常に手前に表示（Wayland は再起動が必要） |
| **位置を記憶** | X11 での位置保存 |
| **シークバーを表示** | 再生位置バーの表示 |
| **歌詞を再読み込み** | 現在曲の歌詞を再取得 |
| **終了** | アプリ終了 |

### オーバーレイ操作

ホバーすると以下の操作ボタンが表示されます。

| ボタン | 動作 |
|--------|------|
| **✕** (右上) | オーバーレイを閉じる |
| **⟳** | 歌詞を再同期 (再取得) |
| **⇄** | 代替歌詞の取得/切り替え |
| **ドラッグ** | ウィンドウ移動 (X11 では位置保存) |

### KDE Plasma Wayland で常に手前に表示

Wayland では通常のウィンドウヒントで常に手前表示できないため、KWin ルールを設定します。

```bash
python scripts/install_kwin_rule.py
```

無効化:

```bash
python scripts/install_kwin_rule.py --disable
```

ルール変更後は Lyricaod を再起動してください。アプリ ID は `lyricaod`、オーバーレイのウィンドウ名は `Lyricaod Overlay` です。

## 設定

設定ファイルは以下に保存されます。

- Linux/macOS: `$XDG_CONFIG_HOME/lyricaod/settings.json` (未設定なら `~/.config/lyricaod/settings.json`)
- Windows: `%APPDATA%\\lyricaod\\settings.json`

変更は自動で反映されます。

```jsonc
{
  "window": {
    "screen_index": 0,
    "anchor": "bottom-center", // top-left | top-center | top-right | center | bottom-left | bottom-center | bottom-right
    "offset_x": 0,
    "offset_y": -100,
    "width_pct": 60,
    "height_px": 300,
    "font_size": 24,
    "font_family": "sans-serif",
    "text_color": "#ffffff",
    "text_shadow_color": "rgba(0,0,0,0.6)",
    "highlight_color": "#ffcc00",
    "visible_lines": 5,
    "background_enabled": false,
    "background_color": "rgba(0,0,0,0.45)",
    "text_shadow": true,
    "show_seekbar": true,
    "karaoke_enabled": true,
    "lyrics_offset_ms": 0,
    "user_x": null,
    "user_y": null
  },
  "behavior": {
    "start_minimized": false,
    "auto_hide_controls": true,
    "hide_delay_ms": 2000,
    "pinned_player": null,
    "remember_position": true,
    "always_on_top": true,
    "smtc_position_fallback": true,
    "ws_port": 56789
  },
  "sources": {
    "syncedlyrics": { "enabled": true, "enhanced": true },
    "lrclib": { "enabled": true },
    "musixmatch": { "enabled": false, "api_key": "" }
  },
  "cache": {
    "ttl_days": 30,
    "max_entries": 10000
  }
}
```

キャッシュは `~/.cache/lyricaod/cache.db`（Windows は `%LOCALAPPDATA%\\lyricaod\\cache.db`）に保存されます。

## 既知の制限

- **Wayland の絶対座標** — Wayland ではウィンドウ座標の固定ができないため、`remember_position` は無視されます。
- **Wayland の常に手前表示** — KWin ルールが必要です。他の Wayland コンポジタでは対応が必要です。
- **Musixmatch は未実装** — UI に項目はありますが動作しません。
- **Windows のブラウザ再生位置** — SMTC だけでは更新頻度が低い場合があります。ブラウザ拡張または `smtc_position_fallback` を有効にしてください。
- **グローバルホットキー** — 未実装です。

## 技術スタック

| 項目 | 使用ライブラリ |
|------|----------------|
| UI | PySide6 |
| プレイヤー連携 | D-Bus MPRIS / Windows SMTC / Browser WebSocket |
| 歌詞取得 | syncedlyrics / LRClib |
| HTTP | httpx |
| キャッシュ | sqlite3 (stdlib) |
| 設定 | json (stdlib) + QFileSystemWatcher |

## License

MIT
