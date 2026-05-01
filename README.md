# Lyricaod

Desktop lyrics overlay for KDE Plasma. Displays synced lyrics as a transparent, frameless window on top of all other windows, sourced from [LRClib](https://lrclib.net).

## Features

- **Synced lyrics** — LRC format with line-by-line highlighting, powered by LRClib API
- **MPRIS integration** — Auto-detects media players (Spotify, Firefox, KDE Connect, etc.) via D-Bus
- **Transparent overlay** — Frameless, always-on-top, no taskbar entry, doesn't steal focus
- **Hover controls** — Close, resync, and alternative lyrics buttons fade in on mouse hover
- **Dynamic sizing** — Window shrinks to fit displayed text (Wayland-friendly)
- **Seekbar** — Playback progress bar with timestamp display (toggleable)
- **Loading indicator** — Shows "⏳ Loading…" while fetching lyrics
- **SQLite cache** — Fast offline access with TTL and LRU eviction
- **Hot-reload settings** — Edit `settings.json` and changes apply instantly
- **System tray** — Full control via tray menu (font, lines, offset, player switching)

## Screenshot

> A transparent overlay showing synced lyrics with the current line highlighted in yellow. Hovering reveals close (✕), resync (⟳), and alternatives (⇄) buttons.

## Requirements

- **KDE Plasma 6** (Wayland or X11)
- **Python 3.12+**
- System packages: `pyside6`, `python-dbus`, `python-gobject`

## Installation

### Arch Linux / Manjaro

```bash
# Install system dependencies
sudo pacman -S pyside6 python-dbus python-gobject python-httpx

# Clone and run
git clone https://github.com/<your-username>/lyricaod.git
cd lyricaod
python src/main.py
```

### Other distros

```bash
# Clone
git clone https://github.com/<your-username>/lyricaod.git
cd lyricaod

# Run the setup script (creates venv, installs deps)
bash setup.sh

# Run
.venv/bin/python src/main.py
```

## Usage

```bash
# Normal start
python src/main.py

# Start minimized (tray only)
python src/main.py --minimized
```

### Tray Menu

| Menu Item | Description |
|-----------|-------------|
| **Show Lyrics** | Toggle overlay visibility |
| **Active Player** | Switch between detected MPRIS players |
| **Visible Lines** | 3 / 5 / 7 / 10 lines |
| **Lyrics Offset** | ±1000 ms sync adjustment |
| **Choose Font…** | Font picker dialog |
| **Text Background** | Semi-transparent bg behind lyrics |
| **Always on Top** | Window stays above other windows (X11) |
| **Remember Position** | Save/restore window position (X11) |
| **Show Seekbar** | Playback progress bar |
| **Reload Lyrics** | Re-fetch lyrics for current track |
| **Quit** | Exit the application |

### Overlay Controls

Hover over the overlay to reveal:

| Button | Action |
|--------|--------|
| **✕** (top-right) | Hide overlay |
| **⟳** | Resync — re-fetch lyrics for current track |
| **⇄** | Switch to alternative lyrics match |
| **Drag anywhere** | Move window (position saved on X11) |

## Configuration

Settings are stored at `~/.config/lyricaod/settings.json`. Edit the file directly for changes to apply at runtime.

```jsonc
{
  "window": {
    "screen_index": 0,
    "anchor": "bottom-center",     // top-left | top-center | top-right | center | bottom-left | bottom-center | bottom-right
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
    "lyrics_offset_ms": 0,
    "show_seekbar": true,
    "user_x": null,
    "user_y": null
  },
  "behavior": {
    "start_minimized": false,
    "auto_hide_controls": true,
    "hide_delay_ms": 2000,
    "pinned_player": null,
    "remember_position": true,
    "always_on_top": true
  },
  "sources": {
    "lrclib": { "enabled": true },
    "musixmatch": { "enabled": false, "api_key": "" }
  },
  "cache": {
    "ttl_days": 30,
    "max_entries": 10000
  }
}
```

## Known Limitations

- **Wayland absolute positioning** — Wayland protocol does not allow clients to set absolute window coordinates. `remember_position` is silently ignored on Wayland; only anchor-based placement works.
- **Wayland always-on-top toggle** — `setWindowFlag()` changes require a window recreate that Wayland compositors may ignore.
- **Musixmatch not implemented** — Only LRClib is active.
- **No global hotkeys** — KDE Global Shortcuts D-Bus API integration is planned.

## Tech Stack

| Component | Library |
|-----------|---------|
| UI | PySide6 |
| D-Bus | dbus-python + GLib |
| HTTP | httpx |
| Cache | sqlite3 (stdlib) |
| Config | json (stdlib) + QFileSystemWatcher |

## License

MIT
