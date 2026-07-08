"""Trinkpause – externes AccessMate-Modul (Trinkpausen-Erinnerung).

Zum Installieren diesen Ordner nach %APPDATA%/AccessMate/modules/ kopieren
und AccessMate neu starten – die „Trinkpause“ erscheint dann als eigene
Kategorie unten in den Einstellungen.

Das Modul erinnert in einstellbaren Abständen ans Trinken.  Die Erinnerung
erscheint wahlweise als dezentes Fenster in der Bildschirmmitte oder als
bildschirmfüllender Regen, der die Arbeit spürbar unterbricht.  Wie stark
sie unterbricht (sofort wegklickbar, kurze Wartezeit oder Bestätigung),
lässt sich ebenfalls einstellen.

Dieses Modul ist bewusst autark: es bringt seine eigenen deutschen und
englischen Texte mit und hängt nur an der öffentlichen BaseModule-/Bus-API,
damit es ohne Änderungen am Kern hinzugefügt werden kann.
"""
from __future__ import annotations

import math
import random
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from accessmate.core import config
from accessmate.core.event_bus import bus
from accessmate.modules.base import BaseModule

STYLES = ("popup", "rain", "liquid")
# Styles that take over the whole screen (vs. the discreet centred pop-up).
_FULLSCREEN_STYLES = ("rain", "liquid")
DISMISS_MODES = ("instant", "delay", "confirm")


# ---------------------------------------------------------------------------
# Self-contained translations (the core locale files know nothing about this
# add-on).  Falls back to English for anything missing.
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "de": {
        "name": "Trinkpause",
        "enabled": "Trinkpausen-Erinnerung aktivieren",
        "description": (
            "Erinnert dich in einstellbaren Abständen daran, etwas zu "
            "trinken. Je nach Einstellung erscheint entweder ein dezentes "
            "Fenster in der Bildschirmmitte oder ein bildschirmfüllender "
            "Regen bzw. ein steigender Flüssigkeitsstand, der die Arbeit "
            "spürbar unterbricht – so, wie du es brauchst."),
        "interval": "Erinnern alle",
        "style": "Darstellung",
        "style.popup": "Dezentes Fenster (Mitte)",
        "style.rain": "Regen über den ganzen Bildschirm",
        "style.liquid": "Flüssigkeitsstand (Bildschirm füllt sich)",
        "dismiss": "Wie stark unterbrechen",
        "dismiss.instant": "Sofort wegklickbar",
        "dismiss.delay": "Kurze Wartezeit",
        "dismiss.confirm": "Bestätigung nötig",
        "delay": "Wartezeit",
        "preview": "Vorschau anzeigen",
        "reminder.title": "Zeit für eine Trinkpause!",
        "reminder.text": "Gönn dir einen Schluck Wasser. 💧",
        "reminder.dismiss": "Schließen",
        "reminder.confirm": "Getrunken ✓",
        "reminder.wait": "Bitte kurz warten … ({seconds} s)",
        "suffix.min": " min",
        "suffix.sec": " s",
    },
    "en": {
        "name": "Drink break",
        "enabled": "Enable drink-break reminder",
        "description": (
            "Reminds you to drink at a configurable interval. Depending on "
            "your setting, either a discreet window appears in the centre of "
            "the screen or a full-screen rain / rising liquid level noticeably "
            "interrupts your work – whatever you need."),
        "interval": "Remind every",
        "style": "Presentation",
        "style.popup": "Discreet window (centre)",
        "style.rain": "Rain across the whole screen",
        "style.liquid": "Liquid level (screen fills up)",
        "dismiss": "How strongly to interrupt",
        "dismiss.instant": "Dismiss instantly",
        "dismiss.delay": "Short wait",
        "dismiss.confirm": "Confirmation required",
        "delay": "Wait time",
        "preview": "Show preview",
        "reminder.title": "Time for a drink break!",
        "reminder.text": "Have a sip of water. 💧",
        "reminder.dismiss": "Close",
        "reminder.confirm": "I drank ✓",
        "reminder.wait": "Please wait a moment … ({seconds} s)",
        "suffix.min": " min",
        "suffix.sec": " s",
    },
}


class _Lang:
    """Tracks the app's active language for this module's own strings."""

    def __init__(self) -> None:
        self.code = "en"
        try:
            self.code = config.load_app_config().get("language", "en")
        except Exception:
            pass
        bus.subscribe("i18n.language_changed", self._on_changed)

    def _on_changed(self, lang: str = "en", **_: object) -> None:
        self.code = lang

    def t(self, key: str, **kwargs: str) -> str:
        table = _STRINGS.get(self.code, _STRINGS["en"])
        text = table.get(key) or _STRINGS["en"].get(key) or key
        for placeholder, value in kwargs.items():
            text = text.replace(f"{{{placeholder}}}", value)
        return text


_lang = _Lang()
_t = _lang.t


# ---------------------------------------------------------------------------
# The reminder overlay (both presentations live here)
# ---------------------------------------------------------------------------

class HydrationReminder(QWidget):
    """Full-screen reminder rendered as a centred pop-up or a rain overlay.

    Everything runs on the Qt main thread (the module's QTimer and the
    settings preview button both fire there), so no cross-thread marshalling
    is needed.
    """

    _FRAME_MS = 33   # ~30 fps for the rain / liquid animation
    _LIQUID_TARGET = 0.66   # fills up to ~2/3 of the screen height

    def __init__(self) -> None:
        super().__init__(parent=None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

        self._style = "popup"
        self._dismiss_mode = "delay"
        self._remaining = 0
        self._drops: list[list[float]] = []   # [x, y, speed, length]
        # Liquid-level animation state.
        self._liquid_level = 0.0              # 0 → _LIQUID_TARGET
        self._liquid_phase = 0.0             # surface wave phase
        self._bubbles: list[list[float]] = []  # [x, y, r, speed]

        self._anim = QTimer(self)
        self._anim.setInterval(self._FRAME_MS)
        self._anim.timeout.connect(self._on_frame)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._on_countdown)

        self._build_card()

    # -- Message card (shared by both styles) -------------------------------

    def _build_card(self) -> None:
        self._card = QWidget(self)
        self._card.setObjectName("hydrationCard")
        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(16)

        self._icon = QLabel("\U0001F4A7")   # 💧
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_font = QFont()
        icon_font.setPixelSize(48)
        self._icon.setFont(icon_font)
        lay.addWidget(self._icon)

        self._title = QLabel()
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPixelSize(22)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setWordWrap(True)
        lay.addWidget(self._title)

        self._text = QLabel()
        self._text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text.setWordWrap(True)
        lay.addWidget(self._text)

        self._btn = QPushButton()
        self._btn.setMinimumHeight(40)
        self._btn.clicked.connect(self._dismiss)
        lay.addWidget(self._btn)

        # High-contrast card so it is readable over any screen content.
        self._card.setStyleSheet(
            "#hydrationCard {"
            " background-color: #103A5C; border: 2px solid #4FC3F7;"
            " border-radius: 14px; }"
            " #hydrationCard QLabel { color: #FFFFFF; background: transparent; }"
            " #hydrationCard QPushButton {"
            " background-color: #4FC3F7; color: #002233; font-weight: bold;"
            " border: none; border-radius: 8px; padding: 8px 20px; }"
            " #hydrationCard QPushButton:disabled {"
            " background-color: #37556B; color: #9FB6C6; }")

    # -- Show / dismiss -----------------------------------------------------

    def show_reminder(self, style: str, dismiss: str,
                      delay_seconds: int) -> None:
        self._style = style if style in STYLES else "popup"
        self._dismiss_mode = dismiss if dismiss in DISMISS_MODES else "delay"

        self._title.setText(_t("reminder.title"))
        self._text.setText(_t("reminder.text"))

        screen = QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else self.rect()

        fullscreen = self._style in _FULLSCREEN_STYLES
        if fullscreen:
            self.setGeometry(geom)
            if self._style == "rain":
                self._spawn_drops(geom.width(), geom.height())
            else:
                self._init_liquid(geom.width(), geom.height())
            self._anim.start()
        else:
            self._anim.stop()
            w, h = 420, 300
            self.setGeometry(geom.x() + (geom.width() - w) // 2,
                             geom.y() + (geom.height() - h) // 2, w, h)

        self._layout_card()
        self._start_dismiss_gate(delay_seconds)
        if fullscreen:
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _layout_card(self) -> None:
        if self._style in _FULLSCREEN_STYLES:
            cw, ch = 460, 320
            self._card.setFixedSize(cw, ch)
            self._card.move((self.width() - cw) // 2,
                            (self.height() - ch) // 2)
        else:
            self._card.setFixedSize(self.width(), self.height())
            self._card.move(0, 0)
        self._card.show()

    def _start_dismiss_gate(self, delay_seconds: int) -> None:
        if self._dismiss_mode == "delay" and delay_seconds > 0:
            self._remaining = delay_seconds
            self._btn.setEnabled(False)
            self._update_button()
            self._countdown.start()
        else:
            self._remaining = 0
            self._countdown.stop()
            self._btn.setEnabled(True)
            self._update_button()

    def _update_button(self) -> None:
        if self._remaining > 0:
            self._btn.setText(_t("reminder.wait", seconds=str(self._remaining)))
        elif self._dismiss_mode == "confirm":
            self._btn.setText(_t("reminder.confirm"))
        else:
            self._btn.setText(_t("reminder.dismiss"))

    def _on_countdown(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._remaining = 0
            self._countdown.stop()
            self._btn.setEnabled(True)
        self._update_button()

    def _dismiss(self) -> None:
        if not self._btn.isEnabled():
            return  # still within the wait period
        self._anim.stop()
        self._countdown.stop()
        self.hide()

    def force_hide(self) -> None:
        """Take the reminder down unconditionally (module stopped)."""
        self._anim.stop()
        self._countdown.stop()
        self.hide()

    # -- Rain animation -----------------------------------------------------

    def _spawn_drops(self, w: int, h: int) -> None:
        count = max(40, w // 12)
        self._drops = [
            [random.uniform(0, w), random.uniform(-h, 0),
             random.uniform(6, 16), random.uniform(8, 22)]
            for _ in range(count)
        ]

    # -- Liquid-level animation --------------------------------------------

    def _init_liquid(self, w: int, h: int) -> None:
        self._liquid_level = 0.0
        self._liquid_phase = 0.0
        count = max(14, w // 90)
        self._bubbles = [
            [random.uniform(0, w), random.uniform(0, h),
             random.uniform(2, 6), random.uniform(0.6, 1.8)]
            for _ in range(count)
        ]

    def _liquid_surface_y(self) -> float:
        return self.height() * (1.0 - self._liquid_level)

    def _on_frame(self) -> None:
        if self._style == "liquid":
            self._liquid_level = min(self._LIQUID_TARGET,
                                     self._liquid_level + 0.012)
            self._liquid_phase += 0.14
            surface = self._liquid_surface_y()
            for b in self._bubbles:
                b[1] -= b[3]
                if b[1] < surface + 6:   # reached the surface → restart below
                    b[0] = random.uniform(0, self.width())
                    b[1] = self.height() - random.uniform(0, 30)
                    b[2] = random.uniform(2, 6)
            self.update()
            return
        h = self.height()
        for d in self._drops:
            d[1] += d[2]
            if d[1] > h:
                d[0] = random.uniform(0, self.width())
                d[1] = random.uniform(-40, 0)
        self.update()

    def paintEvent(self, _event: object) -> None:
        if self._style == "rain":
            self._paint_rain()
        elif self._style == "liquid":
            self._paint_liquid()

    def _paint_rain(self) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(10, 30, 50, 180))
        pen = QPen(QColor(120, 200, 255, 200))
        pen.setWidth(2)
        p.setPen(pen)
        for x, y, _speed, length in self._drops:
            p.drawLine(int(x), int(y), int(x), int(y + length))
        p.end()

    def _paint_liquid(self) -> None:
        w, h = self.width(), self.height()
        surface = self._liquid_surface_y()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Faint dim over the whole screen so it reads as an overlay.
        p.fillRect(self.rect(), QColor(6, 22, 40, 70))

        # Two overlapping sine waves make the surface look alive.
        def wave_y(x: float) -> float:
            return (surface
                    + 14 * math.sin(x * 0.012 + self._liquid_phase)
                    + 6 * math.sin(x * 0.03 - self._liquid_phase * 1.5))

        body = QPainterPath()
        body.moveTo(0, h)
        body.lineTo(0, wave_y(0))
        x = 0.0
        while x <= w:
            body.lineTo(x, wave_y(x))
            x += 6
        body.lineTo(w, wave_y(w))
        body.lineTo(w, h)
        body.closeSubpath()
        p.fillPath(body, QColor(40, 130, 210, 150))

        # Rising bubbles inside the liquid.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(190, 230, 255, 130))
        for bx, by, br, _sp in self._bubbles:
            if by > surface:
                p.drawEllipse(int(bx - br), int(by - br),
                              int(br * 2), int(br * 2))

        # Bright surface line on top.
        pen = QPen(QColor(170, 220, 255, 220))
        pen.setWidth(3)
        p.setPen(pen)
        prev = (0.0, wave_y(0))
        x = 6.0
        while x <= w:
            y = wave_y(x)
            p.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
            prev = (x, y)
            x += 6
        p.end()

    # -- Input --------------------------------------------------------------

    def keyPressEvent(self, event: object) -> None:  # type: ignore[override]
        if (event.key() == Qt.Key.Key_Escape
                and self._dismiss_mode != "confirm"):
            self._dismiss()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: object) -> None:  # type: ignore[override]
        if self._dismiss_mode == "instant":
            self._dismiss()
        else:
            super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

class HydrationSettings(QWidget):
    def __init__(self, module: "HydrationModule") -> None:
        super().__init__()
        self._module = module
        self._settings = module._settings
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self._enabled_cb = QCheckBox(_t("enabled"))
        self._enabled_cb.setChecked(self._module.enabled)
        self._enabled_cb.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        desc = QLabel(_t("description"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        form = QFormLayout()
        form.setSpacing(8)
        self._form = form

        self._interval = QSpinBox()
        self._interval.setRange(1, 480)
        self._interval.setSuffix(_t("suffix.min"))
        self._interval.setValue(int(self._settings.get("interval_minutes", 60)))
        self._interval.valueChanged.connect(
            lambda v: self._save("interval_minutes", v))
        form.addRow(_t("interval"), self._interval)

        self._style = QComboBox()
        self._style.addItem(_t("style.popup"), "popup")
        self._style.addItem(_t("style.rain"), "rain")
        self._style.addItem(_t("style.liquid"), "liquid")
        saved_style = self._settings.get("style", "popup")
        if saved_style in STYLES:
            self._style.setCurrentIndex(STYLES.index(saved_style))
        self._style.currentIndexChanged.connect(
            lambda i: self._save("style", self._style.itemData(i)))
        form.addRow(_t("style"), self._style)

        self._dismiss = QComboBox()
        self._dismiss.addItem(_t("dismiss.instant"), "instant")
        self._dismiss.addItem(_t("dismiss.delay"), "delay")
        self._dismiss.addItem(_t("dismiss.confirm"), "confirm")
        cur = self._settings.get("dismiss_mode", "delay")
        self._dismiss.setCurrentIndex(
            DISMISS_MODES.index(cur) if cur in DISMISS_MODES else 1)
        self._dismiss.currentIndexChanged.connect(self._on_dismiss_changed)
        form.addRow(_t("dismiss"), self._dismiss)

        self._delay = QSpinBox()
        self._delay.setRange(1, 60)
        self._delay.setSuffix(_t("suffix.sec"))
        self._delay.setValue(int(self._settings.get("delay_seconds", 5)))
        self._delay.valueChanged.connect(
            lambda v: self._save("delay_seconds", v))
        form.addRow(_t("delay"), self._delay)

        layout.addLayout(form)

        self._preview_btn = QPushButton(_t("preview"))
        self._preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(self._preview_btn)

        layout.addStretch()
        self._update_delay_row()
        self._update_enabled_state(self._module.enabled)

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _on_dismiss_changed(self, index: int) -> None:
        self._save("dismiss_mode", self._dismiss.itemData(index))
        self._update_delay_row()

    def _update_delay_row(self) -> None:
        self._form.setRowVisible(
            self._delay, self._dismiss.currentData() == "delay")

    def _on_preview(self) -> None:
        self._module.show_reminder(
            self._style.currentData(),
            self._dismiss.currentData(),
            int(self._delay.value()),
        )

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _update_enabled_state(self, enabled: bool) -> None:
        for w in (self._interval, self._style, self._dismiss, self._delay,
                  self._preview_btn):
            w.setEnabled(enabled)


# ---------------------------------------------------------------------------
# The module
# ---------------------------------------------------------------------------

class HydrationModule(BaseModule):
    MODULE_ID = "hydration"
    DESCRIPTION = "Erinnert in Intervallen an Trinkpausen"

    @property
    def DISPLAY_NAME(self) -> str:  # type: ignore[override]
        return _t("name")

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._overlay: HydrationReminder | None = None
        # QTimer fires on the Qt main thread, so triggering the overlay is
        # thread-safe without any extra marshalling.
        self._timer = QTimer()
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._remind)

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._restart_timer()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        self._timer.stop()
        if self._overlay is not None:
            self._overlay.force_hide()
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        return HydrationSettings(self)

    def on_settings_changed(self) -> None:
        if self._enabled:
            self._restart_timer()
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self.on_settings_changed()

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    # -- Reminder -----------------------------------------------------------

    def _ensure_overlay(self) -> HydrationReminder:
        if self._overlay is None:
            self._overlay = HydrationReminder()
        return self._overlay

    def _interval_ms(self) -> int:
        minutes = max(1, int(self._settings.get("interval_minutes", 60)))
        return minutes * 60 * 1000

    def _restart_timer(self) -> None:
        self._timer.stop()
        self._timer.setInterval(self._interval_ms())
        self._timer.start()

    def _remind(self) -> None:
        self.show_reminder(
            self._settings.get("style", "popup"),
            self._settings.get("dismiss_mode", "delay"),
            int(self._settings.get("delay_seconds", 5)),
        )

    def show_reminder(self, style: str, dismiss: str,
                      delay_seconds: int) -> None:
        """Show the reminder now (used by the timer and the settings preview)."""
        self._ensure_overlay().show_reminder(style, dismiss, delay_seconds)
