"""System tray icon with context menu for controlling Lyricaod."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFontDialog, QMenu, QSystemTrayIcon


def _make_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    pt = QPainter(pix)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing)
    pt.setPen(QColor("#ffffff"))
    pt.setFont(QFont("sans-serif", 30, QFont.Weight.Bold))
    pt.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
    pt.end()
    return QIcon(pix)


class TrayIcon(QSystemTrayIcon):
    """System-tray presence with player-switching and visibility toggle."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app  # Application instance from main.py
        self._setup_menu()

    @classmethod
    def create(cls, app, parent=None):
        """Safe factory: returns TrayIcon or None if tray is unavailable."""
        # QSystemTrayIcon.isSystemTrayAvailable can segfault on Wayland (Qt6 bug).
        # Instead we try to construct and catch failure silently.
        try:
            tray = cls(app, parent)
            tray.setIcon(_make_icon())
            tray.setToolTip("Lyricaod")
            tray.activated.connect(tray._on_activated)
            tray.setContextMenu(tray._menu)
            tray._app.mpris.players_changed.connect(tray._rebuild_player_menu)
            tray._app.mpris.active_player_changed.connect(tray._update_player_menu_checks)
            tray.show()
            return tray
        except Exception:
            return None

    def _setup_menu(self):
        self._menu = QMenu()

        self._show_action = QAction("Show Lyrics")
        self._show_action.setCheckable(True)
        self._show_action.setChecked(True)
        self._show_action.triggered.connect(self._toggle_visibility)
        self._menu.addAction(self._show_action)

        self._menu.addSeparator()

        self._player_menu = QMenu("Active Player")
        self._menu.addMenu(self._player_menu)

        self._menu.addSeparator()

        # --- Lines submenu ---
        self._lines_menu = QMenu("Visible Lines")
        for n in (3, 5, 7, 10):
            a = QAction(str(n), self)
            a.setCheckable(True)
            a.triggered.connect(lambda checked, v=n: self._set_lines(v))
            self._lines_menu.addAction(a)
        self._menu.addMenu(self._lines_menu)

        # --- Offset submenu ---
        self._offset_menu = QMenu("Lyrics Offset")
        for label, ms in [("−1000 ms", -1000), ("−500 ms", -500), ("None", 0),
                          ("+500 ms", 500), ("+1000 ms", 1000)]:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setData(ms)
            a.triggered.connect(lambda checked, v=ms: self._set_offset(v))
            self._offset_menu.addAction(a)
        self._menu.addMenu(self._offset_menu)

        # --- Font ---
        self._font_action = QAction("Choose Font…")
        self._font_action.triggered.connect(self._choose_font)
        self._menu.addAction(self._font_action)

        # --- Background ---
        self._bg_action = QAction("Text Background")
        self._bg_action.setCheckable(True)
        self._bg_action.triggered.connect(self._toggle_background)
        self._menu.addAction(self._bg_action)

        # --- Always on Top ---
        self._ontop_action = QAction("Always on Top")
        self._ontop_action.setCheckable(True)
        self._ontop_action.triggered.connect(self._toggle_always_on_top)
        self._menu.addAction(self._ontop_action)

        # --- Remember Position ---
        self._remember_pos_action = QAction("Remember Position")
        self._remember_pos_action.setCheckable(True)
        self._remember_pos_action.triggered.connect(self._toggle_remember_pos)
        self._menu.addAction(self._remember_pos_action)

        # --- Show Seekbar ---
        self._seekbar_action = QAction("Show Seekbar")
        self._seekbar_action.setCheckable(True)
        self._seekbar_action.triggered.connect(self._toggle_seekbar)
        self._menu.addAction(self._seekbar_action)

        self._menu.addSeparator()

        reload_action = QAction("Reload Lyrics")
        reload_action.triggered.connect(self._reload_lyrics)
        self._menu.addAction(reload_action)

        self._menu.addSeparator()

        quit_action = QAction("Quit")
        quit_action.triggered.connect(QApplication.quit)
        self._menu.addAction(quit_action)

        self._update_checks()

    # ------------------------------------------------------------------
    #  Slots
    # ------------------------------------------------------------------

    def _toggle_visibility(self, checked: bool):
        self._app.overlay.setVisible(checked)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            visible = not self._app.overlay.isVisible()
            self._app.overlay.setVisible(visible)
            self._show_action.setChecked(visible)

    def _rebuild_player_menu(self, players: list[str]):
        self._player_menu.clear()
        active = self._app.mpris.get_active_player()
        for name in players:
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(name == active)
            action.triggered.connect(
                lambda checked, n=name: self._select_player(n)
            )
            self._player_menu.addAction(action)

    def _update_player_menu_checks(self, active_name: str):
        for action in self._player_menu.actions():
            action.setChecked(action.text() == active_name)

    def _select_player(self, name: str):
        self._app.mpris.pin_player(name)
        self._update_player_menu_checks(name)

    def _reload_lyrics(self):
        meta = self._app.mpris.get_current_metadata()
        if meta:
            self._app.on_reload(meta)

    def _choose_font(self):
        current = self._app.settings.get("window.font_family", "sans-serif")
        size = self._app.settings.get("window.font_size", 24)
        cur_font = QFont(current)
        if size > 0:
            cur_font.setPixelSize(size)
        ok, font = QFontDialog.getFont(
            cur_font,
            None,
            "Lyricaod — Choose Font",
            QFontDialog.FontDialogOption.MonospacedFonts
            | QFontDialog.FontDialogOption.ProportionalFonts,
        )
        if ok:
            self._app.settings.set("window.font_family", font.family())
            # QFontDialog may return point-size; store pixel-size always
            ps = font.pixelSize()
            if ps <= 0:
                ps = font.pointSize()
                if ps > 0:
                    ps = round(ps * 1.333)  # approximate pt → px
                else:
                    ps = size  # fallback to previous
            self._app.settings.set("window.font_size", max(8, ps))
            self._app.overlay._shrink_to_content()
            self._app.overlay.update()

    def _set_lines(self, count: int):
        self._app.settings.set("window.visible_lines", count)
        self._app.overlay._shrink_to_content()
        self._app.overlay.update()
        self._update_checks()

    def _set_offset(self, ms: int):
        self._app.settings.set("window.lyrics_offset_ms", ms)
        print(f"[Settings] lyrics_offset_ms = {ms}")
        self._update_checks()

    def _toggle_background(self, checked: bool):
        self._app.settings.set("window.background_enabled", checked)
        self._app.overlay.update()

    def _toggle_always_on_top(self, checked: bool):
        self._app.settings.set("behavior.always_on_top", checked)
        self._app.overlay.set_always_on_top(checked)

    def _toggle_remember_pos(self, checked: bool):
        self._app.settings.set("behavior.remember_position", checked)

    def _toggle_seekbar(self, checked: bool):
        self._app.settings.set("window.show_seekbar", checked)
        self._app.overlay._shrink_to_content()
        self._app.overlay.update()

    def set_show_checked(self, checked: bool):
        """Update the tray 'Show Lyrics' checkbox without emitting triggered."""
        self._show_action.blockSignals(True)
        self._show_action.setChecked(checked)
        self._show_action.blockSignals(False)

    def _update_checks(self):
        cur_lines = self._app.settings.get("window.visible_lines", 5)
        for a in self._lines_menu.actions():
            a.setChecked(int(a.text()) == cur_lines)

        cur_offset = self._app.settings.get("window.lyrics_offset_ms", 0)
        for a in self._offset_menu.actions():
            val = a.data()
            a.setChecked(isinstance(val, int) and cur_offset == val)

        self._bg_action.setChecked(
            self._app.settings.get("window.background_enabled", False)
        )
        self._ontop_action.setChecked(
            self._app.settings.get("behavior.always_on_top", True)
        )
        self._remember_pos_action.setChecked(
            self._app.settings.get("behavior.remember_position", True)
        )
        self._seekbar_action.setChecked(
            self._app.settings.get("window.show_seekbar", True)
        )
