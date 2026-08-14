"""Color helpers for settings values stored as CSS-like strings."""

from __future__ import annotations

import re

from PySide6.QtGui import QColor

_RGBA_RE = re.compile(
    r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([0-9.]+)\s*\)",
    re.IGNORECASE,
)


def color_from_setting(value: str, fallback: str = "#ffffff") -> QColor:
    """Parse a persisted color string into ``QColor``.

    Qt's ``QColor`` accepts hex values but not CSS ``rgba(r,g,b,a)`` strings
    with fractional alpha, which the existing settings file uses.
    """
    if not isinstance(value, str):
        value = fallback

    match = _RGBA_RE.fullmatch(value.strip())
    if match:
        r, g, b = (max(0, min(255, int(match.group(i)))) for i in range(1, 4))
        alpha_raw = float(match.group(4))
        alpha = round(alpha_raw * 255) if alpha_raw <= 1 else round(alpha_raw)
        return QColor(r, g, b, max(0, min(255, alpha)))

    color = QColor(value)
    if color.isValid():
        return color

    fallback_match = _RGBA_RE.fullmatch(fallback.strip()) if fallback else None
    if fallback_match:
        r, g, b = (
            max(0, min(255, int(fallback_match.group(i)))) for i in range(1, 4)
        )
        alpha_raw = float(fallback_match.group(4))
        alpha = round(alpha_raw * 255) if alpha_raw <= 1 else round(alpha_raw)
        return QColor(r, g, b, max(0, min(255, alpha)))

    return QColor(fallback)


def color_to_rgba_setting(color: QColor, alpha_pct: int | None = None) -> str:
    """Serialize a color as ``rgba(r,g,b,a)`` with fractional alpha."""
    alpha = color.alphaF()
    if alpha_pct is not None:
        alpha = max(0, min(100, alpha_pct)) / 100
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha:.2f})"


def alpha_percent(color: QColor) -> int:
    return round(color.alphaF() * 100)
