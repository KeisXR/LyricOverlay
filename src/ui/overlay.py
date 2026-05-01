"""
Frameless transparent overlay window for displaying lyrics.

The window:
  - Is always on top (``WindowStaysOnTopHint``)
  - Has no window decorations (``FramelessWindowHint``)
  - Has a fully transparent background when idle
  - Shows a slight dark tint + control buttons on mouse hover
  - Dynamically shrinks to fit the displayed text (Wayland-friendly)
  - Optional semi-transparent background behind text for readability
"""

from PySide6.QtCore import (
    Property as QtProperty,
    QPoint,
    QRect,
    Qt,
    QTimer,
    QPropertyAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
    QMoveEvent,
)
from PySide6.QtWidgets import QWidget, QMenu

from lyrics.lrc_parser import ParsedLRC, find_current_line
from ui.color_utils import color_from_setting


class OverlayWindow(QWidget):
    """Main lyrics overlay."""

    closed = Signal()
    alternative_selected = Signal(int)
    alternatives_requested = Signal()
    resync_requested = Signal()

    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings

        # Lyrics data
        self._parsed_lrc: ParsedLRC | None = None
        self._display_lines: list[str] = []
        self._synced = False
        self._current_line = -1

        # Loading state
        self._loading = False

        # Seek bar
        self._seek_position = 0
        self._seek_length = 0

        # Alternatives
        self._alternatives: list = []
        self._alternatives_loading = False
        self._alt_menu_rect = QRect()

        # Hover state
        self._hover = False
        self._controls_opacity = 0.0
        self._close_btn_rect = QRect()
        self._resync_btn_rect = QRect()

        self._setup_window()
        self._setup_hide_timer()
        self._setup_pos_saver()

    def _setup_pos_saver(self):
        """Debounced position persistence."""
        self._pos_save_timer = QTimer(self)
        self._pos_save_timer.setSingleShot(True)
        self._pos_save_timer.setInterval(800)
        self._pos_save_timer.timeout.connect(self._save_position)

    # ------------------------------------------------------------------
    #  Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("Lyricaod Overlay")
        self.setWindowFlags(self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setMinimumWidth(120)
        self.setMinimumHeight(40)

    def _window_flags(self):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._settings.get("behavior.always_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _setup_hide_timer(self):
        delay = self._settings.get("behavior.hide_delay_ms", 2000)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(delay)
        self._hide_timer.timeout.connect(self._on_hide_controls)

    def _on_hide_controls(self):
        if not self._hover:
            self._animate_controls_opacity(0.0)

    # ------------------------------------------------------------------
    #  Qt property for opacity animation
    # ------------------------------------------------------------------

    def _get_controls_opacity(self) -> float:
        return self._controls_opacity

    def _set_controls_opacity(self, value: float):
        self._controls_opacity = value
        self.update()

    controls_opacity = QtProperty(
        float, _get_controls_opacity, _set_controls_opacity
    )

    def _animate_controls_opacity(self, target: float):
        # Keep as instance attr so Python doesn't GC the animation mid-flight
        self._opacity_anim = QPropertyAnimation(self, b"controls_opacity", parent=self)
        self._opacity_anim.setDuration(200)
        self._opacity_anim.setStartValue(self._controls_opacity)
        self._opacity_anim.setEndValue(target)
        self._opacity_anim.start()

    # ------------------------------------------------------------------
    #  Mouse events
    # ------------------------------------------------------------------

    def enterEvent(self, event: QEnterEvent):
        self._hover = True
        self._hide_timer.stop()
        self._animate_controls_opacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        if self._settings.get("behavior.auto_hide_controls", True):
            self._hide_timer.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._close_btn_rect.contains(event.pos()) and self._controls_opacity > 0.5:
                self.closed.emit()
                return
            if self._resync_btn_rect.contains(event.pos()) and self._controls_opacity > 0.5:
                self.resync_requested.emit()
                return
            if self._alt_menu_rect.contains(event.pos()) and self._controls_opacity > 0.5:
                if self._alternatives:
                    self._show_alternatives_menu()
                elif not self._alternatives_loading:
                    self.alternatives_requested.emit()
                return
            wh = self.windowHandle()
            if wh:
                wh.startSystemMove()

    def mouseMoveEvent(self, event: QMouseEvent):
        pass

    def mouseReleaseEvent(self, event: QMouseEvent):
        pass

    def moveEvent(self, event: QMoveEvent):
        """Debounce: save position after the user finishes dragging."""
        super().moveEvent(event)
        if not self.is_wayland() and self._settings.get("behavior.remember_position", True):
            self._pos_save_timer.start()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    @staticmethod
    def is_wayland() -> bool:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.platformName().startswith("wayland")

    def set_always_on_top(self, on: bool):
        if self.is_wayland():
            return
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()
        else:
            self.hide()

    def set_hide_delay(self, delay_ms: int):
        self._hide_timer.setInterval(max(0, delay_ms))

    def _save_position(self):
        if self.is_wayland():
            return
        pos = self.pos()
        self._settings.set("window.user_x", pos.x())
        self._settings.set("window.user_y", pos.y())

    def restore_position(self):
        """Move window to saved position if remember_position is enabled."""
        if self.is_wayland():
            return
        if not self._settings.get("behavior.remember_position", True):
            return
        x = self._settings.get("window.user_x")
        y = self._settings.get("window.user_y")
        if x is not None and y is not None:
            self.move(int(x), int(y))

    def set_lyrics_data(self, lrc: ParsedLRC, synced: bool = True):
        self._parsed_lrc = lrc
        self._synced = synced
        self._display_lines = [ln.text for ln in lrc.lines] if lrc.lines else []
        self._current_line = -1
        self._shrink_to_content()
        self.update()

    def set_plain_text(self, text: str):
        self._synced = False
        self._parsed_lrc = None
        self._display_lines = [
            ln.strip() for ln in text.strip().split("\n") if ln.strip()
        ]
        self._current_line = -1
        self._shrink_to_content()
        self.update()

    def set_position(self, position_ms: int):
        if not self._synced or self._parsed_lrc is None:
            return
        offset = self._settings.get("window.lyrics_offset_ms", 0)
        idx = find_current_line(self._parsed_lrc, position_ms + offset)
        if idx != self._current_line:
            self._current_line = idx
            self.update()

    def set_alternatives(self, alternatives: list):
        self._alternatives = alternatives
        self.update()

    def set_alternatives_loading(self, loading: bool):
        self._alternatives_loading = loading
        self.update()

    def set_loading(self, loading: bool):
        self._loading = loading
        self._shrink_to_content()
        self.update()

    def set_seek(self, position_ms: int, length_ms: int):
        self._seek_position = position_ms
        self._seek_length = max(0, length_ms)
        self.update()

    # ------------------------------------------------------------------
    #  Dynamic sizing
    # ------------------------------------------------------------------

    def _shrink_to_content(self):
        font = self._make_font()
        fm = QFontMetrics(font)

        if not self._display_lines:
            self.resize(300, 60)
            return

        visible = min(
            len(self._display_lines),
            self._settings.get("window.visible_lines", 5),
        )
        max_w = max(
            (fm.horizontalAdvance(ln) for ln in self._display_lines if ln),
            default=100,
        )
        line_h = fm.lineSpacing()
        pad = 60
        old_geo = self.geometry()
        seekbar_h = 20 if self._settings.get("window.show_seekbar", True) and self._seek_length > 0 else 0
        self.resize(max_w + pad * 2, line_h * max(1, visible) + pad + seekbar_h)
        # Preserve top-left corner after resize (X11 only)
        if not self.is_wayland():
            self.move(old_geo.x(), old_geo.y())

    def _make_font(self) -> QFont:
        family = self._settings.get("window.font_family", "sans-serif")
        size = self._settings.get("window.font_size", 24)
        font = QFont(family)
        if size > 0:
            font.setPixelSize(size)
        return font

    # ------------------------------------------------------------------
    #  Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # --- Background ---
        use_bg = self._settings.get("window.background_enabled", False)
        hint_alpha = 0.15  # low-opacity hint for alternatives indicator

        if use_bg:
            bg_color = color_from_setting(
                self._settings.get("window.background_color", "rgba(0,0,0,0.45)"),
                "rgba(0,0,0,0.45)",
            )
        else:
            bg_color = QColor(0, 0, 0, 0)

        # On hover, blend in extra tint
        hover_alpha = int(90 * self._controls_opacity) if self._controls_opacity > 0.01 else 0
        if use_bg or hover_alpha > 0:
            if use_bg:
                bg = QColor(bg_color)
            else:
                bg = QColor(0, 0, 0, hover_alpha)
            if hover_alpha > 0 and use_bg:
                bg.setAlpha(min(255, bg.alpha() + hover_alpha))
            painter.fillRect(self.rect(), bg)

        # --- Lyrics text ---
        font = self._make_font()
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_h = fm.lineSpacing()

        text_color = color_from_setting(
            self._settings.get("window.text_color", "#ffffff"), "#ffffff"
        )
        shadow_color = color_from_setting(
            self._settings.get("window.text_shadow_color", "rgba(0,0,0,0.6)"),
            "rgba(0,0,0,0.6)",
        )
        highlight_color = color_from_setting(
            self._settings.get("window.highlight_color", "#ffcc00"), "#ffcc00"
        )

        visible = min(
            len(self._display_lines),
            self._settings.get("window.visible_lines", 5),
        )

        # --- Loading indicator ---
        if self._loading:
            painter.setPen(QPen(text_color))
            load_font = QFont(font)
            load_font.setPixelSize(max(14, font.pixelSize() // 2))
            painter.setFont(load_font)
            loader_text = "⏳ Loading…"
            lw = QFontMetrics(load_font).horizontalAdvance(loader_text)
            lx = (self.width() - lw) // 2
            ly = self.height() // 2 + QFontMetrics(load_font).ascent()
            painter.drawText(lx, ly, loader_text)
            painter.end()
            return

        if visible == 0:
            painter.end()
            return

        # Scroll offset
        if self._synced and self._current_line >= 0:
            start = max(0, self._current_line - visible // 2)
        else:
            start = 0
        end = min(start + visible, len(self._display_lines))
        display_slice = self._display_lines[start:end]

        total_h = line_h * len(display_slice)
        y_offset = (self.height() - total_h) // 2

        for i, text in enumerate(display_slice):
            actual_idx = start + i
            is_current = self._synced and actual_idx == self._current_line
            color = highlight_color if is_current else text_color

            text_w = fm.horizontalAdvance(text)
            available_w = self.width() - 20
            if text_w > available_w:
                text = fm.elidedText(text, Qt.TextElideMode.ElideRight, available_w)

            x = max(10, (self.width() - fm.horizontalAdvance(text)) // 2)
            y = y_offset + i * line_h + fm.ascent()

            if self._settings.get("window.text_shadow", True):
                painter.setPen(QPen(shadow_color))
                painter.drawText(x + 2, y + 2, text)

            painter.setPen(QPen(color))
            painter.drawText(x, y, text)

        # --- Controls (hover opacity) ---
        painter.setOpacity(self._controls_opacity)
        self._draw_controls(painter)
        painter.setOpacity(1.0)

        # --- Alternatives hint (always visible at low opacity when lyrics shown) ---
        if self._display_lines and self._controls_opacity < 0.6:
            self._draw_alt_hint(painter)

        # --- Seek bar ---
        if self._settings.get("window.show_seekbar", True) and self._seek_length > 0 and not self._loading:
            self._draw_seekbar(painter)

        painter.end()

    def _draw_controls(self, painter: QPainter):
        """Draw close, resync button, alternatives button, and drag-handle hint (hover only)."""
        btn_x = self.width() - 28
        btn_y = 6

        # Close button
        close_r = QRect(btn_x, btn_y, 22, 22)
        painter.setPen(QPen(QColor(255, 255, 255, 200)))
        btn_font = QFont("sans-serif", 14, QFont.Weight.Bold)
        painter.setFont(btn_font)
        painter.drawText(close_r, Qt.AlignmentFlag.AlignCenter, "✕")
        self._close_btn_rect = close_r

        # Resync button
        resync_x = btn_x - 30
        self._resync_btn_rect = QRect(resync_x, btn_y, 22, 22)
        painter.drawText(self._resync_btn_rect, Qt.AlignmentFlag.AlignCenter, "⟳")

        # Alternatives button (always shown when lyrics are displayed)
        if self._display_lines:
            alt_x = resync_x - 30
            self._alt_menu_rect = QRect(alt_x, btn_y, 22, 22)
            if self._alternatives_loading:
                painter.setPen(QPen(QColor(255, 255, 255, 100)))
                painter.drawText(self._alt_menu_rect, Qt.AlignmentFlag.AlignCenter, "…")
            else:
                painter.drawText(self._alt_menu_rect, Qt.AlignmentFlag.AlignCenter, "⇄")
        else:
            self._alt_menu_rect = QRect()

        # Drag hint
        bar_w = 40
        bar_x = (self.width() - bar_w) // 2
        painter.setPen(QPen(QColor(255, 255, 255, 80)))
        painter.drawLine(QPoint(bar_x, 6), QPoint(bar_x + bar_w, 6))

    def _draw_alt_hint(self, painter: QPainter):
        """Draw a low-opacity alternatives indicator even without hover."""
        painter.save()
        painter.setOpacity(0.25)
        alt_x = self.width() - 58
        alt_y = 6
        hint_r = QRect(alt_x, alt_y, 22, 22)
        btn_font = QFont("sans-serif", 12, QFont.Weight.Bold)
        painter.setFont(btn_font)
        painter.setPen(QPen(QColor(255, 255, 255, 160)))
        painter.drawText(hint_r, Qt.AlignmentFlag.AlignCenter, "⇄")
        # Register the rect at full-size so clicks hit even the low-opacity hint
        self._alt_menu_rect = QRect(alt_x, alt_y, 22, 22)
        painter.restore()

    @staticmethod
    def _format_time(ms: int) -> str:
        s = max(0, ms // 1000)
        return f"{s // 60}:{s % 60:02d}"

    def _draw_seekbar(self, painter: QPainter):
        """Draw a thin progress bar with mm:ss labels at the bottom."""
        bar_h = 18
        margin = 10
        y = self.height() - bar_h
        w = self.width() - margin * 2

        # Time labels
        small_font = QFont("sans-serif", 9)
        painter.setFont(small_font)
        fm = QFontMetrics(small_font)
        painter.setPen(QPen(QColor(255, 255, 255, 140)))
        cur_label = self._format_time(self._seek_position)
        tot_label = self._format_time(self._seek_length)
        painter.drawText(margin, y + bar_h - 2, cur_label)
        painter.drawText(self.width() - margin - fm.horizontalAdvance(tot_label),
                         y + bar_h - 2, tot_label)

        # Progress track
        track_y = y + 2
        track_h = 3
        track_w = w
        painter.setPen(QPen(QColor(255, 255, 255, 60)))
        painter.drawLine(QPoint(margin, track_y), QPoint(margin + track_w, track_y))

        # Progress fill
        if self._seek_length > 0:
            pct = min(1.0, self._seek_position / self._seek_length)
            fill_w = int(track_w * pct)
            painter.setPen(QPen(QColor(255, 255, 255, 200)))
            painter.drawLine(QPoint(margin, track_y), QPoint(margin + fill_w, track_y))

    def _show_alternatives_menu(self):
        """Show a popup menu listing alternative lyrics results."""
        menu = QMenu(self)
        for i, alt in enumerate(self._alternatives):
            label = f"{alt.artist} — {alt.title}"
            if alt.synced:
                label += "  [synced]"
            action = menu.addAction(label)
            action.triggered.connect(lambda checked, idx=i: self.alternative_selected.emit(idx))

        if self._alternatives:
            menu.addSeparator()
            menu.addAction("✕ Close")

        pos = self.mapToGlobal(
            QPoint(self._alt_menu_rect.x(), self._alt_menu_rect.bottom() + 4)
        )
        menu.exec(pos)
