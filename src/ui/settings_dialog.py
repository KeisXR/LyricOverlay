"""Settings dialog for Lyricaod."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.color_utils import alpha_percent, color_from_setting, color_to_rgba_setting


class SettingsDialog(QDialog):
    """Immediate-apply settings dialog."""

    ANCHORS = [
        "top-left",
        "top-center",
        "top-right",
        "center",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ]

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self._settings = app.settings
        self._background_color = color_from_setting(
            self._settings.get("window.background_color", "rgba(0,0,0,0.45)"),
            "rgba(0,0,0,0.45)",
        )

        self.setWindowTitle("Lyricaod Settings")
        self.setMinimumWidth(560)

        tabs = QTabWidget()
        tabs.addTab(self._build_display_tab(), "Display")
        tabs.addTab(self._build_position_tab(), "Position")
        tabs.addTab(self._build_sync_tab(), "Sync")
        tabs.addTab(self._build_behavior_tab(), "Behavior")
        tabs.addTab(self._build_sources_tab(), "Sources")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    #  Tab builders
    # ------------------------------------------------------------------

    def _build_display_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        text_group = QGroupBox("Lyrics")
        text_form = QFormLayout(text_group)

        self._font_label = QLabel(self._font_summary())
        font_btn = QPushButton("Choose...")
        font_btn.clicked.connect(self._choose_font)
        font_row = QHBoxLayout()
        font_row.addWidget(self._font_label, 1)
        font_row.addWidget(font_btn)
        text_form.addRow("Font", font_row)

        self._font_size = self._spin("window.font_size", 8, 96, " px")
        self._font_size.valueChanged.connect(
            lambda value: self._set("window.font_size", value, resize=True)
        )
        text_form.addRow("Font size", self._font_size)

        visible_lines = self._spin("window.visible_lines", 1, 12)
        visible_lines.valueChanged.connect(
            lambda value: self._set("window.visible_lines", value, resize=True)
        )
        text_form.addRow("Visible lines", visible_lines)

        text_form.addRow(
            "Text color",
            self._color_button("window.text_color", "#ffffff", resize=False),
        )
        text_form.addRow(
            "Current line color",
            self._color_button("window.highlight_color", "#ffcc00", resize=False),
        )

        shadow = self._check("window.text_shadow")
        shadow.toggled.connect(lambda checked: self._set("window.text_shadow", checked))
        text_form.addRow("Text shadow", shadow)
        text_form.addRow(
            "Shadow color",
            self._color_button("window.text_shadow_color", "rgba(0,0,0,0.6)"),
        )

        background_group = QGroupBox("Background")
        background_form = QFormLayout(background_group)

        bg_enabled = self._check("window.background_enabled")
        bg_enabled.toggled.connect(
            lambda checked: self._set("window.background_enabled", checked)
        )
        background_form.addRow("Text background", bg_enabled)

        bg_btn = QPushButton()
        self._style_color_button(bg_btn, self._background_color)
        bg_btn.clicked.connect(self._choose_background_color)
        background_form.addRow("Background color", bg_btn)

        opacity_slider, opacity_spin = self._linked_slider_spin(
            0, 100, alpha_percent(self._background_color), "%"
        )
        opacity_slider.valueChanged.connect(self._set_background_opacity)
        background_form.addRow("Background opacity", opacity_slider)
        background_form.addRow("", opacity_spin)

        seekbar = self._check("window.show_seekbar")
        seekbar.toggled.connect(
            lambda checked: self._set("window.show_seekbar", checked, resize=True)
        )
        background_form.addRow("Show seekbar", seekbar)

        layout.addWidget(text_group)
        layout.addWidget(background_group)
        layout.addStretch(1)
        return tab

    def _build_position_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        screen = QComboBox()
        screens = QApplication.screens()
        for idx, qscreen in enumerate(screens):
            screen.addItem(f"{idx}: {qscreen.name()}", idx)
        screen.setCurrentIndex(
            min(self._settings.get("window.screen_index", 0), max(0, len(screens) - 1))
        )
        screen.currentIndexChanged.connect(
            lambda _: self._set(
                "window.screen_index", screen.currentData(), reposition=True
            )
        )
        form.addRow("Screen", screen)

        anchor = QComboBox()
        anchor.addItems(self.ANCHORS)
        current_anchor = self._settings.get("window.anchor", "bottom-center")
        anchor.setCurrentText(
            current_anchor if current_anchor in self.ANCHORS else "bottom-center"
        )
        anchor.currentTextChanged.connect(
            lambda value: self._set("window.anchor", value, reposition=True)
        )
        form.addRow("Anchor", anchor)

        offset_x = self._spin("window.offset_x", -3000, 3000, " px")
        offset_x.valueChanged.connect(
            lambda value: self._set("window.offset_x", value, reposition=True)
        )
        form.addRow("Offset X", offset_x)

        offset_y = self._spin("window.offset_y", -3000, 3000, " px")
        offset_y.valueChanged.connect(
            lambda value: self._set("window.offset_y", value, reposition=True)
        )
        form.addRow("Offset Y", offset_y)

        width = self._spin("window.width_pct", 10, 100, "%")
        width.valueChanged.connect(
            lambda value: self._set("window.width_pct", value, reposition=True)
        )
        form.addRow("Width", width)

        height = self._spin("window.height_px", 40, 1200, " px")
        height.valueChanged.connect(
            lambda value: self._set("window.height_px", value, reposition=True)
        )
        form.addRow("Initial height", height)

        remember = self._check("behavior.remember_position")
        remember.toggled.connect(
            lambda checked: self._set("behavior.remember_position", checked)
        )
        form.addRow("Remember position", remember)

        reset_btn = QPushButton("Reset saved position")
        reset_btn.clicked.connect(self._reset_saved_position)
        form.addRow("", reset_btn)

        return tab

    def _build_sync_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        player_group = QGroupBox("Player")
        player_layout = QGridLayout(player_group)
        self._player_combo = QComboBox()
        self._reload_players()
        self._player_combo.activated.connect(self._select_player)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._reload_players)
        reload_btn = QPushButton("Reload lyrics")
        reload_btn.clicked.connect(self._reload_lyrics)
        player_layout.addWidget(QLabel("Active player"), 0, 0)
        player_layout.addWidget(self._player_combo, 0, 1)
        player_layout.addWidget(refresh_btn, 0, 2)
        player_layout.addWidget(reload_btn, 1, 1, 1, 2)

        sync_group = QGroupBox("Lyrics timing")
        sync_form = QFormLayout(sync_group)
        current_offset = self._settings.get("window.lyrics_offset_ms", 0)
        offset_slider, offset_spin = self._linked_slider_spin(
            -5000, 5000, current_offset, " ms"
        )
        offset_slider.setSingleStep(50)
        offset_slider.setPageStep(500)
        offset_spin.setSingleStep(50)
        offset_slider.valueChanged.connect(
            lambda value: self._set("window.lyrics_offset_ms", value)
        )
        sync_form.addRow("Sync offset", offset_slider)
        sync_form.addRow("", offset_spin)

        zero_btn = QPushButton("Reset to 0 ms")
        zero_btn.clicked.connect(lambda: offset_slider.setValue(0))
        sync_form.addRow("", zero_btn)

        layout.addWidget(player_group)
        layout.addWidget(sync_group)
        layout.addStretch(1)
        return tab

    def _build_behavior_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        visible = QCheckBox()
        visible.setChecked(self._app.overlay.isVisible())
        visible.toggled.connect(self._toggle_overlay_visible)
        form.addRow("Show overlay", visible)

        start_minimized = self._check("behavior.start_minimized")
        start_minimized.toggled.connect(
            lambda checked: self._set("behavior.start_minimized", checked)
        )
        form.addRow("Start minimized", start_minimized)

        always_on_top = self._check("behavior.always_on_top")
        always_on_top.toggled.connect(
            lambda checked: self._set(
                "behavior.always_on_top", checked, always_on_top=True
            )
        )
        form.addRow("Always on top", always_on_top)

        auto_hide = self._check("behavior.auto_hide_controls")
        auto_hide.toggled.connect(
            lambda checked: self._set("behavior.auto_hide_controls", checked)
        )
        form.addRow("Auto-hide controls", auto_hide)

        hide_delay = self._settings.get("behavior.hide_delay_ms", 2000)
        delay_slider, delay_spin = self._linked_slider_spin(0, 10000, hide_delay, " ms")
        delay_slider.setSingleStep(100)
        delay_slider.setPageStep(500)
        delay_spin.setSingleStep(100)
        delay_slider.valueChanged.connect(
            lambda value: self._set("behavior.hide_delay_ms", value, hide_delay=True)
        )
        form.addRow("Hide delay", delay_slider)
        form.addRow("", delay_spin)

        return tab

    def _build_sources_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        lrclib = self._check("sources.lrclib.enabled")
        lrclib.toggled.connect(
            lambda checked: self._set("sources.lrclib.enabled", checked)
        )
        form.addRow("LRClib", lrclib)

        musixmatch = self._check("sources.musixmatch.enabled")
        musixmatch.setEnabled(False)
        musixmatch.toggled.connect(
            lambda checked: self._set("sources.musixmatch.enabled", checked)
        )
        form.addRow("Musixmatch (planned)", musixmatch)

        api_key = QLineEdit(self._settings.get("sources.musixmatch.api_key", ""))
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        api_key.setEnabled(False)
        api_key.editingFinished.connect(
            lambda: self._set("sources.musixmatch.api_key", api_key.text())
        )
        form.addRow("Musixmatch API key", api_key)

        ttl = self._spin("cache.ttl_days", 1, 3650, " days")
        ttl.valueChanged.connect(lambda value: self._set("cache.ttl_days", value))
        form.addRow("Cache TTL", ttl)

        max_entries = self._spin("cache.max_entries", 100, 1000000)
        max_entries.setSingleStep(100)
        max_entries.valueChanged.connect(
            lambda value: self._set("cache.max_entries", value)
        )
        form.addRow("Cache max entries", max_entries)

        return tab

    # ------------------------------------------------------------------
    #  Widget helpers
    # ------------------------------------------------------------------

    def _spin(self, key: str, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(int(self._settings.get(key, minimum)))
        spin.setSuffix(suffix)
        return spin

    def _check(self, key: str) -> QCheckBox:
        check = QCheckBox()
        check.setChecked(bool(self._settings.get(key, False)))
        return check

    def _linked_slider_spin(
        self, minimum: int, maximum: int, value: int, suffix: str = ""
    ) -> tuple[QSlider, QSpinBox]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)

        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSuffix(suffix)

        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        return slider, spin

    def _color_button(self, key: str, fallback: str, resize: bool = False) -> QPushButton:
        color = color_from_setting(self._settings.get(key, fallback), fallback)
        button = QPushButton()
        self._style_color_button(button, color)
        button.clicked.connect(lambda: self._choose_color(key, fallback, button, resize))
        return button

    @staticmethod
    def _style_color_button(button: QPushButton, color: QColor):
        button.setText(color.name().upper())
        button.setStyleSheet(f"background-color: {color.name()};")

    def _font_summary(self) -> str:
        return (
            f"{self._settings.get('window.font_family', 'sans-serif')}, "
            f"{self._settings.get('window.font_size', 24)} px"
        )

    # ------------------------------------------------------------------
    #  Actions
    # ------------------------------------------------------------------

    def _set(
        self,
        key: str,
        value,
        *,
        resize: bool = False,
        reposition: bool = False,
        always_on_top: bool = False,
        hide_delay: bool = False,
    ):
        self._settings.set(key, value)
        if resize:
            self._app.overlay._shrink_to_content()
        if reposition:
            self._app._position_overlay()
        if always_on_top:
            self._app.overlay.set_always_on_top(bool(value))
        if hide_delay:
            self._app.overlay.set_hide_delay(int(value))
        if key == "sources.lrclib.enabled":
            self._app.lyrics.set_lrclib_enabled(bool(value))
        if key in ("cache.ttl_days", "cache.max_entries"):
            self._app.lyrics.set_cache_limits(
                self._settings.get("cache.ttl_days", 30),
                self._settings.get("cache.max_entries", 10000),
            )
        self._app.overlay.update()
        if self._app.tray:
            self._app.tray._update_checks()

    def _choose_font(self):
        current = self._settings.get("window.font_family", "sans-serif")
        size = self._settings.get("window.font_size", 24)
        cur_font = QFont(current)
        if size > 0:
            cur_font.setPixelSize(size)
        ok, font = QFontDialog.getFont(cur_font, self, "Choose Font")
        if not ok:
            return

        pixel_size = font.pixelSize()
        if pixel_size <= 0:
            point_size = font.pointSize()
            pixel_size = round(point_size * 1.333) if point_size > 0 else size
        self._settings.set("window.font_family", font.family())
        self._settings.set("window.font_size", max(8, pixel_size))
        self._font_size.blockSignals(True)
        self._font_size.setValue(max(8, pixel_size))
        self._font_size.blockSignals(False)
        self._font_label.setText(self._font_summary())
        self._app.overlay._shrink_to_content()
        self._app.overlay.update()

    def _choose_color(
        self, key: str, fallback: str, button: QPushButton, resize: bool = False
    ):
        initial = color_from_setting(self._settings.get(key, fallback), fallback)
        color = QColorDialog.getColor(initial, self, "Choose Color")
        if not color.isValid():
            return
        self._settings.set(key, color.name())
        self._style_color_button(button, color)
        if resize:
            self._app.overlay._shrink_to_content()
        self._app.overlay.update()

    def _choose_background_color(self):
        color = QColorDialog.getColor(self._background_color, self, "Choose Background")
        if not color.isValid():
            return
        color.setAlpha(self._background_color.alpha())
        self._background_color = color
        sender = self.sender()
        if isinstance(sender, QPushButton):
            self._style_color_button(sender, color)
        self._settings.set("window.background_color", color_to_rgba_setting(color))
        self._app.overlay.update()

    def _set_background_opacity(self, value: int):
        self._background_color.setAlphaF(max(0, min(100, value)) / 100)
        self._settings.set(
            "window.background_color",
            color_to_rgba_setting(self._background_color, value),
        )
        self._app.overlay.update()

    def _reset_saved_position(self):
        self._settings.set("window.user_x", None)
        self._settings.set("window.user_y", None)
        self._app._position_overlay()

    def _reload_players(self):
        if not hasattr(self, "_player_combo"):
            return
        self._player_combo.blockSignals(True)
        self._player_combo.clear()
        self._player_combo.addItem("Auto", "")
        active = self._app.mpris.get_active_player()
        for player in self._app.mpris.get_players():
            self._player_combo.addItem(player, player)
            if player == active:
                self._player_combo.setCurrentIndex(self._player_combo.count() - 1)
        self._player_combo.blockSignals(False)

    def _select_player(self, *_args):
        player = self._player_combo.currentData()
        if player:
            self._settings.set("behavior.pinned_player", player)
            self._app.mpris.pin_player(player)
        else:
            self._settings.set("behavior.pinned_player", None)
            self._app.mpris.unpin_player()

    def _reload_lyrics(self):
        meta = self._app.mpris.get_current_metadata()
        if meta:
            self._app.on_reload(meta)

    def _toggle_overlay_visible(self, checked: bool):
        self._app.overlay.setVisible(checked)
        if self._app.tray:
            self._app.tray.set_show_checked(checked)
