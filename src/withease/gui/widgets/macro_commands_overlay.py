"""Macro command overlay (P3).

While macro mode is active, this frameless panel lists every macro command and
its key, grouped by category (a leading ★ favourites group, then the user's
categories, then anything uncategorised).  It appears when macro mode starts and
disappears again when it ends – so it is a just-in-time cheat-sheet, never a
permanent fixture.  The always-on favourites overlay (:mod:`actions_overlay`)
stays a separate, independent thing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from withease.core.event_bus import bus

if TYPE_CHECKING:
    from withease.app import WithEaseApp

_BG = QColor(30, 34, 42, 238)
_BORDER = QColor(230, 81, 0, 210)      # macro accent (orange)
_HEADER_FG = QColor(255, 176, 110)     # category headers
_LABEL_FG = QColor(235, 238, 245)
_KEY_FG = QColor(255, 176, 110)
_FAV_FG = QColor(255, 213, 128)        # favourites: warm gold
_RADIUS = 8
_PAD = 12
_COL_GAP = 18
_GROUP_GAP = 8
_MARGIN = 12
_DEFAULT_FONT_PX = 13


class _Bridge(QObject):
    changed = Signal(bool)   # macro mode on/off
    refresh = Signal()


class MacroCommandsOverlay(QWidget):
    def __init__(self, app: "WithEaseApp") -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._app = app
        self._active = False
        self._font_px = _DEFAULT_FONT_PX
        # groups: [(header, [(label, key, is_favourite), ...]), ...]
        self._groups: list[tuple[str, list[tuple[str, str, bool]]]] = []

        self._bridge = _Bridge()
        self._bridge.changed.connect(self._on_mode_changed)
        self._bridge.refresh.connect(self._refresh)
        bus.subscribe("macros.mode_changed",
                      lambda active=False, **_: self._bridge.changed.emit(active))
        for event in ("profiles.changed", "module.settings_changed",
                      "overlay.config_changed", "module.stopped"):
            bus.subscribe(event, lambda **_: self._bridge.refresh.emit())

    # ------------------------------------------------------------------

    def _config(self) -> dict:
        return self._app.get_macro_commands_config()

    def _on_mode_changed(self, active: bool) -> None:
        self._active = active
        self._refresh()

    def _refresh(self) -> None:
        cfg = self._config()
        if not self._active or not cfg.get("enabled", True):
            self.hide()
            return
        self._groups = self._app.get_macro_command_groups()
        if not self._groups:
            self.hide()
            return
        self._font_px = max(9, min(28, int(cfg.get("font_size",
                                                   _DEFAULT_FONT_PX))))
        self._resize_to_content()
        self._reposition(cfg)
        self.show()
        self.update()

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _row_h(self) -> int:
        return self._font_px + 8

    def _header_h(self) -> int:
        return self._font_px + 12

    def _fonts(self) -> tuple[QFont, QFont]:
        base = QFont(self.font())
        base.setPixelSize(self._font_px)
        bold = QFont(base)
        bold.setBold(True)
        return base, bold

    def _column_widths(self) -> tuple[int, int, int]:
        base, bold = self._fonts()
        fm, fm_bold = QFontMetrics(base), QFontMetrics(bold)
        label_w = key_w = header_w = 0
        for header, rows in self._groups:
            header_w = max(header_w, fm_bold.horizontalAdvance(header))
            for label, key, fav in rows:
                text = ("★ " + label) if fav else label
                label_w = max(label_w, fm.horizontalAdvance(text))
                key_w = max(key_w, fm_bold.horizontalAdvance(key))
        return label_w, key_w + 4, header_w

    def _resize_to_content(self) -> None:
        font = self.font()
        font.setPixelSize(self._font_px)
        self.setFont(font)
        label_w, key_w, header_w = self._column_widths()
        body_w = label_w + _COL_GAP + key_w
        w = min(max(180, max(body_w, header_w) + 2 * _PAD), 720)
        h = 2 * _PAD
        for i, (_header, rows) in enumerate(self._groups):
            h += self._header_h() + len(rows) * self._row_h()
            if i < len(self._groups) - 1:
                h += _GROUP_GAP
        self.setFixedSize(w, h)

    def _reposition(self, cfg: dict) -> None:
        pos = cfg.get("position", "top-center")
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        if "left" in pos:
            x = geom.x() + _MARGIN
        elif "right" in pos:
            x = geom.x() + geom.width() - self.width() - _MARGIN
        else:
            x = geom.x() + (geom.width() - self.width()) // 2
        y = (geom.y() + _MARGIN if "top" in pos
             else geom.y() + geom.height() - self.height() - _MARGIN)
        self.move(x, y)

    # ------------------------------------------------------------------

    def paintEvent(self, _event: object) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), _RADIUS, _RADIUS)
        p.fillPath(path, _BG)
        p.setPen(_BORDER)
        p.drawPath(path)

        base, bold = self._fonts()
        label_w, _key_w, _header_w = self._column_widths()
        key_left = _PAD + label_w + _COL_GAP
        key_rect_w = self.width() - key_left - _PAD
        row_h, header_h = self._row_h(), self._header_h()
        y = _PAD
        for header, rows in self._groups:
            p.setFont(bold)
            p.setPen(_HEADER_FG)
            p.drawText(QRect(_PAD, y, self.width() - 2 * _PAD, header_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       header)
            y += header_h
            for label, key, fav in rows:
                text = ("★ " + label) if fav else label
                p.setFont(base)
                p.setPen(_FAV_FG if fav else _LABEL_FG)
                p.drawText(QRect(_PAD, y, label_w, row_h),
                           Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft, text)
                p.setFont(bold)
                p.setPen(_FAV_FG if fav else _KEY_FG)
                p.drawText(QRect(key_left, y, key_rect_w, row_h),
                           Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignRight, key)
                y += row_h
            y += _GROUP_GAP
        p.end()
