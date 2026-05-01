# Lyricaod - Desktop Lyrics Display for KDE

## Overview
A transparent, frameless desktop lyrics overlay for KDE Plasma that fetches lyrics from multiple sources (Musixmatch, LRClib, etc.) and displays them with a minimalist, auto-hiding UI.

---

## Architecture

```
lyricaod/
├── src/
│   ├── main.py                 # Entry point, tray icon, lifecycle
│   ├── player/
│   │   ├── __init__.py
│   │   └── mpris.py            # MPRIS D-Bus listener, position interpolator, active player selection
│   ├── lyrics/
│   │   ├── __init__.py
│   │   ├── manager.py          # Fetch orchestration, fallback chain, SQLite cache
│   │   ├── lrc_parser.py       # LRC format parser (basic + enhanced, offset)
│   │   └── lrclib.py           # LRClib API client
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── overlay.py          # Frameless transparent window + hover controls
│   │   ├── settings_dialog.py  # Tabbed settings dialog (live preview)
│   │   ├── color_utils.py      # CSS rgba() string ↔ QColor conversion
│   │   └── tray.py             # System tray icon & menu
│   └── config/
│       ├── __init__.py
│       └── settings.py         # Load/save/watcher for settings.json
├── tests/
│   ├── test_lrc_parser.py
│   └── test_lrclib.py
├── requirements.txt
└── docs/
    └── design.md               # This document
```

> **Note:** `player_selector.py`, `musixmatch.py`, and `controls.py` from earlier design iterations were merged into `mpris.py`, `overlay.py`, and removed respectively. Musixmatch support is planned but not yet implemented.

---

## 1. Media Detection (KDE / MPRIS)

### Protocol
- **MPRIS2** via D-Bus session bus, namespace `org.mpris.MediaPlayer2.*`
- Library: **`dbus-python`** (simpler than QtDBus, integrates with glib mainloop)

### Signals tracked
| Signal | Source | Purpose |
|--------|--------|---------|
| `PropertiesChanged` | `org.freedesktop.DBus.Properties` | Detect Metadata / PlaybackStatus changes |
| `Seeked` | `org.mpris.MediaPlayer2.Player` | Reset local position timer on seek |

### Metadata fields used
```
xesam:title, xesam:artist, xesam:album, mpris:length, mpris:trackid
```

### Player selection rule (priority order)
Implemented in `mpris.py` (`_select_active()`):
1. **Pinned player** — if set via tray menu, always wins
2. **Playing player** — any player with `PlaybackStatus == "Playing"`; `plasma-browser-integration` is preferred over raw browser MPRIS because it provides cleaner metadata
3. **Current player** — keep existing active player if it still exists
4. **First available** — fallback to any discovered player

### Position tracking (critical for LRC sync)
- Read `Rate` and `Position` from MPRIS on metadata change
- Start a **local monotonic timer** (30ms interval, `QTimer`) that interpolates:
  ```
  estimated_position = last_mpris_position + (now - last_mpris_timestamp) * rate
  ```
- Re-sync on every `PropertiesChanged(Position)` or `Seeked` signal
- Clamp to `[0, mpris:length]`
- Uses `time.monotonic()` (not `time.time()`) to avoid NTP skew
- This achieves sub-100ms accuracy for LRC line switching

---

## 2. Lyrics Sources

| Source | Type | Auth | Rate Limit | Priority | Status |
|--------|------|------|------------|----------|--------|
| **LRClib** | Synced LRC | None | Moderate | 1 (first) | ✅ Implemented |
| **Musixmatch** | Synced+Unsynced | API Key | Strict (2000/day) | 2 (fallback) | ⏳ Not yet implemented |
| *(future)* NetEase | Synced | Unofficial | Unknown | 3 | 📋 Planned |

### Fetch strategy

**Phase 1 — Primary lyrics (fast path):**

1. Check SQLite cache: key = `SHA256(artist | title | trackid)`
2. Cache hit → return primary lyrics immediately (alternatives may be empty if not yet fetched)
3. Cache miss → call LRClib `/api/get` (single HTTP request, no alternatives)
4. All fail → show "No lyrics found" in overlay

**Phase 2 — Alternatives (on-demand):**

1. User clicks the ⇄ (alternatives) button in the overlay
2. Check cache first — if alternatives are already stored from a prior fetch, show them immediately
3. Otherwise, call LRClib `/api/search` followed by parallel `/api/get/{id}` calls via `asyncio.gather`
4. Cache the alternatives and emit to the overlay

This two-phase design keeps the initial lyrics load fast (1 request vs the old 1 + 1 + N requests) while still allowing users to explore alternative matches.

### Caching (SQLite)
- Key: `SHA256( artist | title | trackid )`
- `trackid` inclusion prevents cache collision across different tracks with same metadata
- Store:
  ```json
  {
    "primary": { "artist", "title", "synced", "plain_text", "lrc_raw", "source" },
    "alternatives": [ /* same structure */ ]
  }
  ```
- Old single-result format is still readable for backward compatibility
- TTL: 30 days (configurable), stale entries deleted on startup
- Max entries: 10,000 (LRU eviction — oldest `fetched_at` removed when limit exceeded)

### Error handling per source
- Network timeout: 10s
- HTTP 5xx / network failure: emit `lyrics_not_found`; overlay shows cached lyrics if available, else "No lyrics found"
- HTTP 429 backoff and multi-source fallback are planned but not yet implemented (only LRClib is active)

---

## 3. UI Design (Frameless Overlay)

### Framework: PySide6 (LGPL)

### Platform support strategy: Wayland First (KDE Plasma 6)
KDE Plasma 6 defaults to Wayland. The app works on both Wayland and X11, with X11-only features (absolute positioning, always-on-top toggle at runtime) gracefully degrading on Wayland.

| Feature | Wayland (KWin) | X11 |
|---------|---------------|-----|
| Frameless | `Qt.FramelessWindowHint` | Same |
| Always on top | `Qt.WindowStaysOnTopHint` (requested, compositor decides) | `Qt.WindowStaysOnTopHint` (enforced) |
| Translucent bg | `Qt.WA_TranslucentBackground` | Same |
| Mouse passthrough | **Not supported** — window always intercepts clicks in its bounding box | `Qt.WA_TransparentForMouseEvents` would work, but not implemented |
| Positioning | **Absolute `move()` ignored** — relies on anchor + KWin geometry hints | Absolute `move()` works; user-dragged position can be saved/restored |
| Focus stealing | `Qt.WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating` | Same |

### Dynamic Bounding Box
Because mouse passthrough is impossible on Wayland, the overlay window dynamically resizes itself to wrap the currently displayed text. This minimizes the "dead zone" where users cannot click the desktop beneath the lyrics. On X11, the top-left corner is preserved after resize to prevent position drift.

### Window configuration
```python
Qt.FramelessWindowHint        # No title bar
Qt.WindowStaysOnTopHint       # Always visible above other windows
Qt.Tool                       # No taskbar entry
Qt.WindowDoesNotAcceptFocus   # Don't steal focus from active app
Qt.WA_TranslucentBackground   # Transparent background
Qt.WA_ShowWithoutActivating   # Show without stealing focus
```

### Geometry
- Stored as: `{ screen_index: 0, anchor: "bottom-center", offset_x: 0, offset_y: -100, width_pct: 60, height_px: 300 }`
- Anchor points: `top-left`, `top-center`, `top-right`, `center`, `bottom-left`, `bottom-center`, `bottom-right`
- Recalculated on screen change / resolution change via `QScreen` signals

### Interaction model (revised)

```
┌─────────────────────────────────────────┐
│  Normal state (no mouse on widget)      │
│                                         │
│     "And I will always love you..."     │  ← lyrics text, white + shadow
│                                         │
│  Background: transparent (alpha=0)      │
│  Controls: hidden                       │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Hover state (mouse enters widget)      │
│                                    [⨯]  │  ← close button, top-right
│     "And I will always love you..."     │
│         ─ ─ ─ ─ ─ ─ ─ ─ ─             │  ← drag handle (subtle line)
│                                    [⚙]  │  ← settings, bottom-right
│  Background: rgba(0,0,0,0.35)          │
│  Controls: fade in (200ms animation)    │
└─────────────────────────────────────────┘

Mouse leaves → controls fade out after 2s delay (configurable)
```

**Note**: Because hover detection requires receiving mouse events, the window **blocks clicks to the desktop when active**. X11 mouse passthrough (`Qt.WA_TransparentForMouseEvents`) is not implemented.

### Fade animation
- Use `QGraphicsOpacityEffect` on the controls container only (not the lyrics text)
- `QPropertyAnimation` on `opacity` property, duration 200ms

---

## 4. Lyrics Display Logic

### LRC Parser (`lrc_parser.py`)

Supported formats:
```
# Basic
[01:23.45]Line text

# Multi-timestamp (repeated line)
[01:23.45][02:45.67]Repeated line

# Enhanced word-level timing
<01:23.45>word1 <01:23.90>word2

# Header tags
[ti:Song Title]
[ar:Artist]
[offset:+1500]    # Global offset in ms
[length:03:45]
```

Output data structure:
```python
@dataclass
class LyricLine:
    timestamp_ms: int
    text: str                    # Plain text (for basic display)
    words: list[tuple[int, str]] # [(ms, "word1"), ...] — optional, for word highlighting

@dataclass
class ParsedLRC:
    title: str | None
    artist: str | None
    offset_ms: int               # Global offset from [offset:] tag
    lines: list[LyricLine]
```

### Synced display (LRC)
- Current line = binary search for `timestamp_ms <= estimated_position`
- Highlight current line (brighter + larger font or underline)
- Previous lines above, upcoming lines below (max 5 lines visible, configurable)
- Smooth scroll using `QPropertyAnimation` on `QScrollArea.scrollTo()` or custom painting
- Apply `offset_ms` from LRC header to all timestamps

### Unsynced display (plain text)
- Show first ~6 lines, centered
- No highlighting or scrolling

### Text rendering
- `QPainter` with `drawText()` for maximum control over:
  - Text shadow: draw same text offset by 2px in `rgba(0,0,0,0.6)` then main text in white
  - Multi-line layout with proper line spacing
  - Current line emphasis (different color/weight)
- Font: Noto Sans CJK JP (bundled or system), fallback to sans-serif

---

## 5. System Tray

### Tray icon
- Always visible in the system tray (KDE's System Tray widget)
- Left-click: toggle lyrics overlay visibility
- Right-click: context menu

### Context menu
```
Lyricaod
─────────────────
✓ Show Lyrics           ← toggle
─────────────────
Active Player: Spotify  ← submenu to switch
  ● Spotify
  ○ Firefox
─────────────────
 Reload Lyrics
 Settings...
─────────────────
 Quit
```

---

## 6. Configuration

### File location
```
$XDG_CONFIG_HOME/lyricaod/settings.json   # (typically ~/.config/lyricaod/settings.json)
$XDG_CACHE_HOME/lyricaod/cache.db         # SQLite lyrics cache
```

### settings.json schema
```jsonc
{
  "window": {
    "screen_index": 0,
    "anchor": "bottom-center",     // top-left | top-center | top-right | center | bottom-left | bottom-center | bottom-right
    "offset_x": 0,
    "offset_y": -100,
    "width_pct": 60,               // percentage of screen width
    "height_px": 300,
    "font_size": 24,
    "font_family": "sans-serif",
    "text_color": "#ffffff",
    "text_shadow_color": "rgba(0,0,0,0.6)",
    "highlight_color": "#ffcc00",
    "visible_lines": 5,            // max lines visible at once
    "background_enabled": false,   // semi-transparent bg behind lyrics
    "background_color": "rgba(0,0,0,0.45)",
    "text_shadow": true,           // drop-shadow behind text
    "lyrics_offset_ms": 0,         // global sync offset
    "user_x": null,                // saved X position (X11 only)
    "user_y": null                 // saved Y position (X11 only)
  },
  "behavior": {
    "start_minimized": false,
    "auto_hide_controls": true,
    "hide_delay_ms": 2000,
    "always_on_top": true,
    "remember_position": true,     // X11 only; ignored on Wayland
    "pinned_player": null          // bus name of preferred MPRIS player
  },
  "sources": {
    "lrclib": { "enabled": true },
    "musixmatch": { "enabled": false, "api_key": "" }   // not yet implemented
  },
  "cache": {
    "ttl_days": 30,
    "max_entries": 10000
  }
}
```

### Hot-reload
- Watch `settings.json` via `QFileSystemWatcher`
- On file change → reload config → apply to running UI without restart
- Font size/color changes apply immediately to next render frame

---

## 7. Data Flow (revised)

```
┌──────────────┐   D-Bus Signals    ┌─────────────────┐
│ MPRIS Player │ ──────────────────→ │ player/mpris.py  │
│ (Spotify)    │   PropertiesChanged │ ・metadata       │
└──────────────┘   Seeked           │ ・position       │
                                     │ ・local timer    │
                                     └───────┬─────────┘
                                             │
                         metadata changed?   │ estimated_position
                                             ▼
                                     ┌─────────────────┐
                                     │ lyrics/manager.py│
                                     │ ・check cache    │
                                     │ ・fetch if miss  │  ← single /api/get (fast)
                                     │ ・parse LRC      │
                                     └───────┬─────────┘
                                             │ ParsedLRC or plain text
                                             ▼
                                     ┌─────────────────┐
                                     │  ui/overlay.py   │
                                     │ ・render lyrics  │
                                     │ ・highlight line │
                                     │ ・hover controls │
                                     │ ・alt btn click →│
                                     └───────┬─────────┘
                                             │
                              alternatives_requested? │ settings.json changed?
                                             ▼        ▼
                                     ┌─────────────────┐
                                     │ lyrics/manager.py│
                                     │ ・fetch_alt()    │  ← /api/search + parallel /get/{id}
                                     │ ・alternatives_ready
                                     └─────────────────┘
                                     ┌─────────────────┐
                                     │ config/settings  │
                                     │ QFileSystemWatch │
                                     └─────────────────┘
```

---

## 8. Error States

| State | UI Behavior |
|-------|-------------|
| No player running | Show "Waiting for media…" in overlay |
| Player paused | Continue showing last lyrics (no dimming currently) |
| Fetching lyrics | Silent background fetch; last lyrics remain visible |
| Network error | Show cached lyrics if available, else "No lyrics found for [title]" |
| Lyrics not found | Show "No lyrics found for [title]" |
| LRC parse error | Not handled separately; falls through to plain text or empty |

---

## 9. Tech Stack (final)

| Component | Library | Rationale |
|-----------|---------|-----------|
| UI | **PySide6** | LGPL, no license fees for distribution |
| D-Bus | **dbus-python** | Simpler API, well-maintained, integrates with glib event loop |
| HTTP | **httpx** | Single library for sync + async, connection pooling |
| Cache | **sqlite3** (stdlib) | No extra dependency, sufficient for key-value cache |
| Config | **dataclasses** + **json** (stdlib) | Lightweight, no pydantic overhead |
| Linting | ruff | Fast Python linter |
| Type check | mypy | Static type checking |

### requirements.txt
```
PySide6>=6.6
dbus-python>=1.3
httpx>=0.25
```

---

## 10. Packaging & Distribution

| Method | Target | Priority |
|--------|--------|----------|
| **AUR** (`lyricaod-git`) | Arch / Manjaro users | 1 |
| **Flatpak** | Cross-distro KDE users | 2 |
| **PyPI** (`pip install lyricaod`) | Devs / power users | 3 |

### Flatpak specifics
- Runtime: `org.kde.Platform` (6.6)
- Sandbox needs D-Bus session bus access for MPRIS
- Manifest: `org.flatpak.Builder` with `python3-pip` module

---

## 11. Implementation Status

| Phase | Tasks | Status |
|-------|-------|--------|
| **1. Core** | `player/mpris.py` — detect, track, interpolate position | ✅ Done |
| **2. Lyrics** | `lyrics/lrclib.py` + `lyrics/lrc_parser.py` + `lyrics/manager.py` + SQLite cache | ✅ Done |
| **3. UI** | `ui/overlay.py` — frameless window, hover controls, rendering | ✅ Done |
| **4. Integration** | Wire player → lyrics → UI. Tray icon. End-to-end working. | ✅ Done |
| **5. Polish** | Hot-reload settings, error states, animations, Wayland support, GUI settings dialog, color utils | ✅ Done |
| **6. Perf** | Single-request initial fetch, on-demand alternatives via `asyncio.gather` parallel fetches | ✅ Done |
| **7. Future** | Musixmatch source, HTTP 429 backoff, word-level karaoke highlight, global hotkeys | 📋 Planned |

---

## 12. Known Limitations

1. **Wayland absolute positioning** — Wayland protocol does not allow clients to set absolute window coordinates. `remember_position` is silently ignored on Wayland; only anchor-based placement works.
2. **Wayland always-on-top toggle at runtime** — `setWindowFlag()` changes require a window recreate that Wayland compositors may ignore. Toggling "Always on Top" from the tray may not have immediate effect on Wayland.
3. **Musixmatch not implemented** — Only LRClib is active. Musixmatch API key settings exist in `settings.json` but are unused.
4. **No HTTP 429 backoff** — LRClib rate-limiting is handled by the service; client-side exponential backoff is not implemented.
5. **No global hotkeys** — KDE Global Shortcuts D-Bus API integration is not yet implemented.

## 13. Open Questions

1. **Word-level karaoke highlighting** — Enhanced LRC (`<mm:ss.xx>word`) is parsed but not rendered. Worth the complexity?
2. **Musixmatch API key strategy** — Bundle a free-tier key? Proxy through a server? Require user to bring their own?
3. **Hotkey support** — Global shortcut (e.g., `Ctrl+Shift+L`) to toggle overlay? Depends on KDE Global Shortcuts D-Bus API.
