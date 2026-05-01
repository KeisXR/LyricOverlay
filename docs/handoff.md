# Lyricaod Handoff Notes

This document is a practical handoff guide for agents or developers who need
to continue work on Lyricaod. It describes the current implementation, module
boundaries, runtime flow, settings model, UI behavior, and known risks.

## Project Summary

Lyricaod is a PySide6 desktop lyrics overlay for KDE Plasma. It listens to
MPRIS media players on the D-Bus session bus, fetches synced lyrics from
LRClib, caches results in SQLite, and renders lyrics in a transparent,
frameless overlay window. The overlay is controlled through hover controls,
the system tray, and a GUI settings dialog.

The app is intended to work on KDE Plasma 6 on both Wayland and X11. Wayland
support shapes several design choices: the overlay dynamically shrinks to the
lyrics text to reduce blocked desktop area, and absolute positioning /
always-on-top behavior must be treated as best-effort.

## How To Run

From the repository root:

```bash
python src/main.py
```

Start hidden in the tray:

```bash
python src/main.py --minimized
```

Core runtime dependencies are listed in `requirements.txt`:

- `PySide6`
- `dbus-python`
- `httpx`

Some distros need system packages for D-Bus and GLib integration, for example
`python-dbus` and `python-gobject`.

## Repository Layout

```text
src/
  main.py                 Application bootstrap and signal wiring
  config/
    settings.py           JSON settings load/save/watch logic
  player/
    mpris.py              MPRIS player discovery, active player selection,
                          metadata parsing, playback position interpolation
  lyrics/
    lrclib.py             LRClib HTTP API client
    lrc_parser.py         LRC parser and current-line lookup
    manager.py            Lyrics fetch orchestration, SQLite cache,
                          alternatives handling
  ui/
    overlay.py            Transparent lyrics overlay and hover controls
    tray.py               System tray icon and legacy quick actions
    settings_dialog.py    GUI settings dialog
    color_utils.py        CSS-like rgba()/QColor conversion helpers
tests/
  test_lrc_parser.py
  test_lrclib.py
  test_lyrics_manager.py
docs/
  design.md              Earlier design/architecture notes
  handoff.md             This document
```

## Runtime Architecture

`main.Application` owns all long-lived services:

- `QApplication`
- `Settings`
- `LyricsManager`
- `MprisListener`
- `OverlayWindow`
- `TrayIcon`
- lazily created `SettingsDialog`

High-level flow:

```text
MPRIS player
  -> player/mpris.py emits metadata_changed / position_changed
  -> main.py asks lyrics/manager.py for lyrics on metadata changes
  -> lyrics/manager.py checks SQLite cache, then LRClib if needed
  -> main.py sends ParsedLRC or plain text to ui/overlay.py
  -> overlay.py renders lyrics and updates current line from position_changed
```

Settings flow:

```text
settings_dialog.py or tray.py
  -> Settings.set("section.key", value)
  -> settings.json is saved
  -> affected UI/services are usually updated immediately by caller
  -> external file edits trigger Settings.changed
  -> main.Application._on_settings_changed reapplies broad settings
```

## Entry Point: `src/main.py`

`Application.__init__()` creates services in a specific order:

1. Create `QApplication`.
2. Create `Settings`.
3. Create `LyricsManager` using cache and source settings.
4. Connect lyrics signals.
5. Create `MprisListener`.
6. Apply persisted `behavior.pinned_player` if present.
7. Connect MPRIS signals.
8. Create `OverlayWindow`.
9. Create tray icon.
10. Show overlay unless `behavior.start_minimized` is true.

Important handlers:

- `_on_metadata_changed(metadata)`: caches metadata in `_current_meta`, resets seekbar, shows loading, fetches lyrics (primary only — fast path).
- `_on_position_changed(position_ms)`: updates current lyric line and seekbar.
- `_on_lyrics_ready(result)`: displays synced LRC, plain lyrics, or instrumental text.
- `_on_lyrics_not_found(artist, title)`: displays a fallback message.
- `_on_alternatives_requested()`: triggers on-demand alternative lyrics fetch when user clicks the ⇄ button.
- `_on_alternatives_ready(list)`: sets alternatives on the overlay and auto-opens the menu.
- `_on_settings_changed()`: reapplies LRClib enabled state, cache limits, hide delay,
  and layout/render updates.
- `_position_overlay()`: computes geometry from `window.screen_index`,
  `window.anchor`, `window.offset_x`, `window.offset_y`, `window.width_pct`,
  and `window.height_px`.

## Settings Model

Settings are stored at:

```text
$XDG_CONFIG_HOME/lyricaod/settings.json
```

If `XDG_CONFIG_HOME` is unset, the path is:

```text
~/.config/lyricaod/settings.json
```

The default schema lives in `config/settings.py` as `DEFAULT_SETTINGS`.
Settings are nested dictionaries accessed by dot-separated keys:

```python
settings.get("window.font_size", 24)
settings.set("window.font_size", 28)
```

Current top-level groups:

- `window`: overlay placement, typography, colors, visible lines, background,
  seekbar, saved X11 position, sync offset.
- `behavior`: startup, hover control behavior, active player pin, position memory,
  always-on-top.
- `sources`: LRClib enabled state and placeholder Musixmatch settings.
- `cache`: SQLite cache TTL and entry limit.

`Settings` uses `QFileSystemWatcher` to hot-reload external edits. Internal
`set()` calls save immediately and block watcher signals during the write.

### Important Settings

`window.lyrics_offset_ms`

- Global runtime sync adjustment applied in `OverlayWindow.set_position()`.
- Positive values make the lyric lookup use a later timestamp.
- Exposed in the tray as fixed presets and in Settings as a slider/spinbox.

`window.background_color`

- Stored as CSS-like `rgba(r,g,b,a)` with fractional alpha, for example
  `rgba(0,0,0,0.45)`.
- Parsed via `ui/color_utils.py` because `QColor` does not handle that exact
  format reliably.

`behavior.pinned_player`

- Stores a selected MPRIS bus name such as `org.mpris.MediaPlayer2.spotify`.
- Used by `MprisListener` to prefer that player while it exists.

`sources.lrclib.enabled`

- When false, `LyricsManager.fetch_lyrics()` returns not-found without calling
  LRClib.
- Cached lyrics are currently not returned when LRClib is disabled because the
  source-disabled check happens before cache lookup. Change this intentionally
  if offline cached display should still work with sources disabled.

## MPRIS Layer: `src/player/mpris.py`

`MprisListener` listens to the D-Bus session bus for names under:

```text
org.mpris.MediaPlayer2.*
```

It tracks:

- available players
- active player
- pinned player
- playback status
- metadata
- playback position
- playback rate

Signals emitted:

- `metadata_changed(dict)`
- `position_changed(int)`
- `playback_state_changed(str, str)`
- `players_changed(list)`
- `active_player_changed(str)`

Active player selection priority:

1. Pinned player, if available.
2. Any playing player, preferring `plasma-browser-integration`.
3. Existing active player, if still available.
4. First available player.
5. Empty string when none are available.

Position tracking:

- Reads `Position` synchronously from D-Bus when playback starts or rate changes.
- Measures D-Bus round-trip latency and adds it to the local position.
- Uses `time.monotonic()` and a 16 ms Qt timer to interpolate playback position.
- Clamps to track length when metadata provides `mpris:length`.

Metadata parsing:

- Reads `xesam:title`, `xesam:artist`, `xesam:album`, `mpris:length`,
  and `mpris:trackid`.
- Strips browser/player suffixes such as ` | YouTube Music`, ` - YouTube`,
  and ` | Spotify`.

## Lyrics Layer

### `lyrics/lrclib.py`

The LRClib client has two entry points with different performance profiles:

**Fast path** — one HTTP request, no alternatives:

```python
get_lrclib(artist_name, track_name, album_name="", duration_ms=0, timeout=10.0)
    -> LrcLibResult | None
```

This calls `GET /api/get` directly with query parameters. Used by
`_FetchPrimaryThread` on every metadata change for the primary lyrics
display.

**Search path** — search + parallel fetches (on-demand alternatives):

```python
search_all(artist_name, track_name, album_name="", duration_ms=0,
           timeout=10.0, max_results=6)
    -> list[LrcLibResult]
```

This uses `httpx.AsyncClient` + `asyncio.gather` to run `/api/search`
followed by parallel `/api/get/{id}` calls. Used by `_FetchAltThread`
only when the user clicks the alternatives button.

### `lyrics/lrc_parser.py`

Responsibilities:

- Parse header tags such as `[ti:]`, `[ar:]`, and `[offset:]`.
- Parse basic timestamps like `[01:23.45]`.
- Parse repeated timestamps on one line.
- Parse enhanced word-level timing markers into `words`.
- Provide `find_current_line(parsed_lrc, position_ms)` for display sync.

The overlay currently highlights only the current line. Word-level karaoke
rendering is not implemented even though parser data exists.

### `lyrics/manager.py`

`LyricsManager` coordinates cache lookup, worker-thread fetching, conversion,
and alternatives.

Cache:

- Stored at `~/.cache/lyricaod/cache.db`.
- Uses SQLite table `cache(key TEXT PRIMARY KEY, data TEXT, fetched_at REAL)`.
- Cache key is SHA-256 of `artist|title|trackid`.
- Payload format version is `CACHE_FORMAT_VERSION = 2`.
- Stores a primary lyrics result plus alternatives.
- Cleans up by TTL and LRU max entries.

Threading:

- Two QThread subclasses are used:
  - `_FetchPrimaryThread`: calls `get_lrclib()` — a single direct `/api/get` call, returns one `LrcLibResult | None`. This is the fast path used on every metadata change.
  - `_FetchAltThread`: calls `search_all()` — `/api/search` + parallel `/api/get/{id}` via `asyncio.gather`. This is used only on-demand when the user clicks the alternatives button.
- Only one active worker is allowed at a time (primary or alt).
- Starting a new fetch cancels and waits briefly for the previous worker.
- Request IDs prevent stale worker results from updating UI.

On-demand alternatives flow:

1. User clicks alternatives (⇄) button in overlay → overlay emits `alternatives_requested`.
2. `main.Application._on_alternatives_requested()` calls `lyrics.fetch_alternatives()`.
3. If alternatives are already in cache (`_cached_alternatives`), they are emitted immediately.
4. Otherwise `_FetchAltThread` fetches them, caches them via `_update_cache_alternatives()`, and emits `alternatives_ready`.
5. `_on_alternatives_ready()` sets the alternatives on the overlay and auto-opens the menu.

Alternative selection:

- `select_alternative(index)` swaps the chosen alternative into position 0 and
  returns it.
- `main.Application.on_select_alternative()` displays it and refreshes the
  overlay alternatives list.

## UI Layer

### Overlay: `ui/overlay.py`

`OverlayWindow` is a custom `QWidget` rendered with `QPainter`.

Window flags:

- `FramelessWindowHint`
- `WindowStaysOnTopHint`
- `Tool`
- `WindowDoesNotAcceptFocus`

Attributes:

- `WA_TranslucentBackground`
- `WA_ShowWithoutActivating`

Public signals:

- `closed`
- `alternative_selected(int)`
- `alternatives_requested` — emitted when user clicks the alternatives button and no alternatives are cached
- `resync_requested`

Primary public methods:

- `set_lyrics_data(lrc, synced=True)`
- `set_plain_text(text)`
- `set_position(position_ms)`
- `set_alternatives(alternatives)`
- `set_alternatives_loading(loading)` — show "…" in the alternatives button while fetching
- `set_loading(loading)`
- `set_seek(position_ms, length_ms)`
- `set_always_on_top(on)`
- `restore_position()`
- `set_hide_delay(delay_ms)` — update hide timer interval at runtime

Rendering behavior:

- Dynamically resizes to current lyric text using `QFontMetrics`.
- Shows a loading indicator while fetching.
- For synced lyrics, centers the current line within the visible line window.
- Current line color uses `window.highlight_color`.
- Other text uses `window.text_color`.
- Optional text shadow uses `window.text_shadow` and `window.text_shadow_color`.
- Optional background uses `window.background_enabled` and
  `window.background_color`.
- Optional seekbar uses `window.show_seekbar`.

Hover controls:

- Close button: hides overlay via `closed`.
- Resync button: emits `resync_requested`.
- Alternatives button: if alternatives are cached, opens a popup menu and emits `alternative_selected`. If no alternatives are cached, emits `alternatives_requested` to trigger an on-demand fetch (button shows "…" while loading).
- Dragging anywhere else starts a system move.

Wayland notes:

- `is_wayland()` checks `QGuiApplication.platformName().startswith("wayland")`.
- Saved manual position is ignored on Wayland.
- Runtime always-on-top changes return early on Wayland.

### Tray: `ui/tray.py`

`TrayIcon` owns the system tray icon and context menu.

Menu actions:

- `Show Lyrics`
- `Settings...`
- `Active Player`
- `Visible Lines`
- `Lyrics Offset`
- `Choose Font...`
- `Text Background`
- `Always on Top`
- `Remember Position`
- `Show Seekbar`
- `Reload Lyrics`
- `Quit`

`Settings...` opens a lazily created `SettingsDialog`. The dialog object is
stored on `Application` as `settings_dialog` so it is reused rather than
recreated each time.

The tray still contains quick actions that overlap with settings dialog fields.
When changing settings from the dialog, `SettingsDialog._set()` calls
`tray._update_checks()` to keep tray checkmarks in sync.

### Settings Dialog: `ui/settings_dialog.py`

`SettingsDialog` is immediate-apply. There is no Apply button; changing a
control persists to `settings.json` and updates affected runtime objects.

Tabs:

- `Display`
  - font picker
  - font size
  - visible lines
  - text color
  - current line color
  - text shadow
  - shadow color
  - text background enable
  - background color
  - background opacity slider/spinbox
  - show seekbar
- `Position`
  - screen
  - anchor
  - X/Y offset
  - width percentage
  - initial height
  - remember position
  - reset saved position
- `Sync`
  - active player selector
  - refresh players
  - reload lyrics
  - sync offset slider/spinbox from `-5000` to `+5000` ms
  - reset offset to 0 ms
- `Behavior`
  - show overlay
  - start minimized
  - always on top
  - auto-hide controls
  - hide delay slider/spinbox
- `Sources`
  - LRClib enabled
  - Musixmatch fields, currently disabled because Musixmatch is not implemented
  - cache TTL
  - cache max entries

Implementation notes:

- `_linked_slider_spin()` connects a `QSlider` and `QSpinBox` bidirectionally.
- `_set()` centralizes persistence and runtime refresh behavior.
- Background color and opacity are stored back into one
  `window.background_color` string.
- Player selection writes `behavior.pinned_player`.

### Color Utilities: `ui/color_utils.py`

The settings file uses CSS-like color strings such as:

```text
rgba(0,0,0,0.45)
```

Qt's `QColor` does not accept that form with fractional alpha. Use:

- `color_from_setting(value, fallback)`
- `color_to_rgba_setting(color, alpha_pct=None)`
- `alpha_percent(color)`

Use these helpers for any setting that may contain `rgba()`.

## Platform Behavior

### Wayland

Expected limitations:

- Absolute `move()`/saved position may be ignored.
- Runtime always-on-top toggling may be ignored by the compositor.
- Mouse passthrough is not implemented; the overlay intercepts clicks within
  its widget bounds.

Design response:

- The overlay shrinks to content to minimize blocked desktop area.
- Positioning should favor anchor/offset settings over saved coordinates.

### X11

Expected behavior:

- Manual drag position can be saved/restored.
- `set_always_on_top()` can toggle the window flag at runtime.

## Tests And Verification

Current tests:

- `tests/test_lrc_parser.py`
- `tests/test_lrclib.py`
- `tests/test_lyrics_manager.py`

Run tests when `pytest` is installed:

```bash
python -m pytest -q
```

Basic syntax check:

```bash
python -m compileall src
```

Settings dialog smoke test without a display server can use:

```bash
QT_QPA_PLATFORM=offscreen python - <<'PY'
import sys
from PySide6.QtWidgets import QApplication
from ui.settings_dialog import SettingsDialog
from config.settings import DEFAULT_SETTINGS

class FakeSettings:
    def __init__(self):
        import copy
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
    def get(self, key, default=None):
        cur = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
    def set(self, key, value):
        cur = self.data
        parts = key.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

class FakeOverlay:
    def isVisible(self): return True
    def setVisible(self, checked): pass
    def update(self): pass
    def _shrink_to_content(self): pass
    def set_always_on_top(self, checked): pass
    def set_hide_delay(self, value): pass

class FakeLyrics:
    def set_lrclib_enabled(self, enabled): pass
    def set_cache_limits(self, ttl, max_entries): pass

class FakeMpris:
    def get_active_player(self): return ""
    def get_players(self): return []
    def pin_player(self, player): pass
    def unpin_player(self): pass
    def get_current_metadata(self): return None

class FakeApp:
    def __init__(self):
        self.settings = FakeSettings()
        self.overlay = FakeOverlay()
        self.lyrics = FakeLyrics()
        self.mpris = FakeMpris()
        self.tray = None
    def _position_overlay(self): pass
    def on_reload(self, meta): pass

qapp = QApplication(sys.argv)
dialog = SettingsDialog(FakeApp())
print(dialog.windowTitle(), dialog.minimumWidth())
PY
```

## Common Change Recipes

### Add A New Setting

1. Add a default value to `DEFAULT_SETTINGS` in `config/settings.py`.
2. Read the setting where behavior is implemented.
3. Add a GUI control in `ui/settings_dialog.py`.
4. If tray quick access is appropriate, add it to `ui/tray.py`.
5. If the setting affects runtime services, update `SettingsDialog._set()` and
   `Application._on_settings_changed()`.
6. Consider external hot-reload behavior.

### Add A New Lyrics Source

1. Add a client module under `src/lyrics/`.
2. Add source settings under `sources`.
3. Extend `LyricsManager.fetch_lyrics()` to try sources in order.
4. Preserve the same `LyricsData` abstraction.
5. Decide how source failures behave: fallback, cache-only, or not-found.
6. Add tests around source priority and fallback behavior.

### Add Word-Level Karaoke Rendering

1. Reuse `LyricLine.words` from `lrc_parser.py`.
2. Extend `OverlayWindow.paintEvent()` to render current line segments.
3. Use current playback position plus `window.lyrics_offset_ms`.
4. Keep text width measurement stable; avoid changing overlay size every frame.
5. Add parser/display tests for enhanced LRC.

### Add Global Hotkeys

The project currently has no global hotkey implementation. For KDE, likely
integration points are KDE Global Shortcuts over D-Bus or a portal-based
approach. Be careful with Flatpak/sandbox implications.

## Known Limitations And Risks

- Musixmatch settings exist but Musixmatch fetching is not implemented.
- The GUI disables Musixmatch controls to avoid implying support exists.
- `sources.lrclib.enabled = false` currently prevents both cache lookup and
  network lookup. That may or may not be the desired long-term behavior.
- Cache directory uses `Path.home() / ".cache" / "lyricaod"` rather than
  `$XDG_CACHE_HOME`.
- Tray creation catches all exceptions and returns `None`, which prevents
  crashes but can hide real tray setup errors.
- `SettingsDialog` calls some private methods (`overlay._shrink_to_content()`,
  `app._position_overlay()`, `tray._update_checks()`). This matches current
  code style, but future cleanup could expose explicit public methods.
- `LyricsManager.select_alternative()` mutates `_cached_alternatives` in a way
  that swaps alternatives but does not explicitly store the old primary; review
  this before changing alternative behavior.
- Current overlay text layout elides long lines rather than wrapping.
- There is no packaging metadata yet beyond setup docs.

## Current Verification Status

As of this handoff, these checks were run successfully:

```bash
python -m compileall src
QT_QPA_PLATFORM=offscreen python <settings-dialog-smoke-test>
```

`pytest` was not available in the local environment used for this handoff:

```text
/usr/bin/python: No module named pytest
```

Install test dependencies before relying on the test suite.
