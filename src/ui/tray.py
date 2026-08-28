"""System tray controls with a safe tray-less fallback."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFontDialog, QMenu, QSystemTrayIcon

from ui.kwin_rules import set_rule_enabled
from ui.settings_dialog import SettingsDialog


logger = logging.getLogger(__name__)


def _make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("sans-serif", 30, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
    painter.end()
    return QIcon(pixmap)


_TRAY_PROBE_TIMEOUT_SEC = 1.0
# A session started before the panel finished registering its StatusNotifier
# host would otherwise be mistaken for a tray-less one, so a negative result
# is retried once before we give up on the tray.
_TRAY_PROBE_RETRY_DELAY_SEC = 0.5


def _status_notifier_host_registered(dbus) -> bool:
    bus = dbus.SessionBus()
    for service in (
        "org.kde.StatusNotifierWatcher",
        "org.freedesktop.StatusNotifierWatcher",
    ):
        if not bus.name_has_owner(service):
            continue
        props = dbus.Interface(
            bus.get_object(service, "/StatusNotifierWatcher"),
            "org.freedesktop.DBus.Properties",
        )
        registered = props.Get(
            service,
            "IsStatusNotifierHostRegistered",
            timeout=_TRAY_PROBE_TIMEOUT_SEC,
        )
        if bool(registered):
            return True
    return False


def _tray_host_missing(app) -> bool:
    """Report a conclusively absent tray without asking Qt.

    ``QSystemTrayIcon.isSystemTrayAvailable()`` can segfault on Wayland (Qt6
    bug), so it is never called. Wayland has no XEmbed fallback either: the
    StatusNotifier D-Bus host is the only tray mechanism there, so its absence
    is conclusive. Every uncertain case -- X11, Windows, a missing binding, a
    D-Bus failure -- returns False, keeping the ordinary tray path.
    """
    try:
        if not app.overlay.is_wayland():
            return False
    except Exception:
        return False
    try:
        import dbus
    except Exception:
        return False
    try:
        if _status_notifier_host_registered(dbus):
            return False
        time.sleep(_TRAY_PROBE_RETRY_DELAY_SEC)
        return not _status_notifier_host_registered(dbus)
    except Exception:
        return False


class TraylessFallback:
    """Keep a usable overlay visible when no system tray can be created."""

    available = False

    def __init__(self, app):
        self._app = app
        self._ensure_visible()

    def _ensure_visible(self):
        QTimer.singleShot(0, self._app.overlay.show)

    def set_show_checked(self, checked: bool):
        if not checked:
            logger.warning(
                "Ignoring overlay hide request because no system tray is available"
            )
            self._ensure_visible()

    def update_checks(self):
        pass

    def _update_checks(self):
        self.update_checks()

    def hide(self):
        pass


class TrayIcon(QSystemTrayIcon):
    """System tray presence with player switching and quick settings."""

    available = True

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self._setup_menu()

    @classmethod
    def create(cls, app, parent=None):
        try:
            # Qt constructs, paints and shows a tray icon even when nothing can
            # host it, so absence has to be established before we commit to a
            # real tray. _tray_host_missing does that without isSystemTrayAvailable().
            if _tray_host_missing(app):
                logger.warning(
                    "No system tray host is available; using visible fallback"
                )
                return TraylessFallback(app)
            tray = cls(app, parent)
            tray.setIcon(_make_icon())
            tray.setToolTip("Lyricaod")
            tray.activated.connect(tray._on_activated)
            tray.setContextMenu(tray._menu)
            tray._app.mpris.players_changed.connect(tray._rebuild_player_menu)
            tray._app.mpris.active_player_changed.connect(
                tray._update_player_menu_checks
            )
            tray.show()
            tray._rebuild_player_menu(tray._app.mpris.get_players())
            return tray
        except Exception:
            logger.exception("System tray creation failed; using visible fallback")
            return TraylessFallback(app)

    def _setup_menu(self):
        self._menu = QMenu()

        self._show_action = QAction("歌詞を表示", self)
        self._show_action.setCheckable(True)
        self._show_action.setChecked(True)
        self._show_action.triggered.connect(self._toggle_visibility)
        self._menu.addAction(self._show_action)

        settings_action = QAction("設定...", self)
        settings_action.triggered.connect(self._open_settings)
        self._menu.addAction(settings_action)
        self._menu.addSeparator()

        self._player_menu = QMenu("有効なプレイヤー")
        self._menu.addMenu(self._player_menu)
        self._browser_status_action = QAction("ブラウザ接続: 待機中", self)
        self._browser_status_action.setEnabled(False)
        self._menu.addAction(self._browser_status_action)
        self._menu.addSeparator()

        self._lines_menu = QMenu("表示行数")
        for count in (3, 5, 7, 10):
            action = QAction(str(count), self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked, value=count: self._set_lines(value)
            )
            self._lines_menu.addAction(action)
        self._menu.addMenu(self._lines_menu)

        self._offset_menu = QMenu("歌詞オフセット")
        for label, offset in (
            ("−1000 ms", -1000),
            ("−500 ms", -500),
            ("なし", 0),
            ("+500 ms", 500),
            ("+1000 ms", 1000),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(offset)
            action.triggered.connect(
                lambda _checked, value=offset: self._set_offset(value)
            )
            self._offset_menu.addAction(action)
        self._menu.addMenu(self._offset_menu)

        self._font_action = QAction("フォントを選択...", self)
        self._font_action.triggered.connect(self._choose_font)
        self._menu.addAction(self._font_action)

        self._bg_action = QAction("文字背景", self)
        self._bg_action.setCheckable(True)
        self._bg_action.triggered.connect(self._toggle_background)
        self._menu.addAction(self._bg_action)

        label = "常に手前に表示"
        if self._app.overlay.is_wayland():
            label += "（再起動が必要）"
        self._ontop_action = QAction(label, self)
        self._ontop_action.setCheckable(True)
        self._ontop_action.triggered.connect(self._toggle_always_on_top)
        self._menu.addAction(self._ontop_action)

        self._remember_pos_action = QAction("位置を記憶", self)
        self._remember_pos_action.setCheckable(True)
        self._remember_pos_action.triggered.connect(self._toggle_remember_pos)
        self._menu.addAction(self._remember_pos_action)

        self._seekbar_action = QAction("シークバーを表示", self)
        self._seekbar_action.setCheckable(True)
        self._seekbar_action.triggered.connect(self._toggle_seekbar)
        self._menu.addAction(self._seekbar_action)

        self._menu.addSeparator()
        reload_action = QAction("歌詞を再読み込み", self)
        reload_action.triggered.connect(self._reload_lyrics)
        self._menu.addAction(reload_action)
        self._menu.addSeparator()

        quit_action = QAction("終了", self)
        quit_action.triggered.connect(QApplication.quit)
        self._menu.addAction(quit_action)
        self.update_checks()

    def _refresh_overlay_layout(self):
        if hasattr(self._app.overlay, "refresh_layout"):
            self._app.overlay.refresh_layout()
        else:
            self._app.overlay._shrink_to_content()

    def _toggle_visibility(self, checked: bool):
        self._app.overlay.setVisible(checked)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            visible = not self._app.overlay.isVisible()
            self._app.overlay.setVisible(visible)
            self.set_show_checked(visible)

    def _rebuild_player_menu(self, players: list[str]):
        self._player_menu.clear()
        pinned = self._app.settings.get("behavior.pinned_player") or ""

        automatic = QAction("自動", self)
        automatic.setData("")
        automatic.setCheckable(True)
        automatic.setChecked(not pinned)
        automatic.triggered.connect(lambda _checked: self._select_player(""))
        self._player_menu.addAction(automatic)
        if players:
            self._player_menu.addSeparator()

        for player in players:
            action = QAction(self._player_label(player), self)
            action.setData(player)
            action.setCheckable(True)
            action.setChecked(player == pinned)
            action.triggered.connect(
                lambda _checked, name=player: self._select_player(name)
            )
            self._player_menu.addAction(action)
        self._update_browser_status_action()

    def _update_player_menu_checks(self, _active_name: str = ""):
        pinned = self._app.settings.get("behavior.pinned_player") or ""
        for action in self._player_menu.actions():
            if action.isSeparator():
                continue
            action.setChecked(action.data() == pinned)
        self._update_browser_status_action()

    def _select_player(self, name: str):
        if name:
            self._app.settings.set("behavior.pinned_player", name)
            self._app.mpris.pin_player(name)
        else:
            self._app.settings.set("behavior.pinned_player", None)
            self._app.mpris.unpin_player()
        self._update_player_menu_checks()

    def _reload_lyrics(self):
        metadata = self._app.mpris.get_current_metadata()
        if metadata:
            self._app.on_reload(metadata)

    def _open_settings(self):
        dialog = getattr(self._app, "settings_dialog", None)
        if dialog is None:
            dialog = SettingsDialog(self._app)
            self._app.settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _choose_font(self):
        family = self._app.settings.get("window.font_family", "sans-serif")
        size = self._app.settings.get("window.font_size", 24)
        current = QFont(family)
        if size > 0:
            current.setPixelSize(size)
        ok, font = QFontDialog.getFont(current, None, "Lyricaod — フォントを選択")
        if not ok:
            return
        pixel_size = font.pixelSize()
        if pixel_size <= 0:
            point_size = font.pointSize()
            pixel_size = round(point_size * 1.333) if point_size > 0 else size
        self._app.settings.set("window.font_family", font.family())
        self._app.settings.set("window.font_size", max(8, pixel_size))
        self._refresh_overlay_layout()
        self._app.overlay.update()

    def _set_lines(self, count: int):
        self._app.settings.set("window.visible_lines", count)
        self._refresh_overlay_layout()
        self._app.overlay.update()
        self.update_checks()

    def _set_offset(self, offset_ms: int):
        self._app.settings.set("window.lyrics_offset_ms", offset_ms)
        self.update_checks()

    def _toggle_background(self, checked: bool):
        self._app.settings.set("window.background_enabled", checked)
        self._app.overlay.update()

    def _toggle_always_on_top(self, checked: bool):
        self._app.settings.set("behavior.always_on_top", checked)
        self._app.overlay.set_always_on_top(checked)
        if self._app.overlay.is_wayland():
            rule_id, reloaded = set_rule_enabled(checked)
            if rule_id is None or not reloaded:
                logger.warning("KWin rule update was not fully applied")

    def _toggle_remember_pos(self, checked: bool):
        self._app.settings.set("behavior.remember_position", checked)

    def _toggle_seekbar(self, checked: bool):
        self._app.settings.set("window.show_seekbar", checked)
        self._refresh_overlay_layout()
        self._app.overlay.update()

    def set_show_checked(self, checked: bool):
        self._show_action.blockSignals(True)
        self._show_action.setChecked(checked)
        self._show_action.blockSignals(False)

    def update_checks(self):
        current_lines = self._app.settings.get("window.visible_lines", 5)
        for action in self._lines_menu.actions():
            action.setChecked(int(action.text()) == current_lines)

        current_offset = self._app.settings.get("window.lyrics_offset_ms", 0)
        for action in self._offset_menu.actions():
            value = action.data()
            action.setChecked(isinstance(value, int) and value == current_offset)

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
        self._update_player_menu_checks()
        self._update_browser_status_action()

    def _update_checks(self):
        self.update_checks()

    def _update_browser_status_action(self):
        if self._app.mpris.is_browser_connected():
            active = self._app.mpris.get_active_player()
            suffix = "使用中" if active == "browser-ws" else "接続済み"
            self._browser_status_action.setText(f"ブラウザ接続: {suffix}")
        else:
            self._browser_status_action.setText("ブラウザ接続: 待機中")

    @staticmethod
    def _player_label(player_id: str) -> str:
        if player_id == "browser-ws":
            return "ブラウザ拡張機能 (browser-ws)"
        return player_id
