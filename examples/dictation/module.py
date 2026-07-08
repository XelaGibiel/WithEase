"""Diktieren – externes AccessMate-Modul (Sprache zu Text per Whisper).

Zum Installieren diesen Ordner nach %APPDATA%/AccessMate/modules/ kopieren
und AccessMate neu starten – „Diktieren“ erscheint dann als eigene Kategorie
unten in den Einstellungen.

Ablauf: Hotkey drücken → Aufnahme (🎙-Chip erscheint) → erneut drücken bzw.
Taste loslassen stoppt → der erkannte Text wird in die aktive Anwendung
eingefügt.  Erkennung wahlweise per OpenAI-kompatibler Cloud-API
(OpenRouter/OpenAI/Groq/eigene URL) oder lokal via faster-whisper.

Dieses Modul ist autark: es bringt seine eigenen deutschen und englischen
Texte mit und hängt nur an der öffentlichen AccessMate-Erweiterungs-API
(BaseModule, Event-Bus, ActionManager, geteilter Tastatur-Hook, App-Config
und das wiederverwendbare HotkeyEdit-Widget).  Der Kern weiß nichts von ihm.

Optionale Abhängigkeiten (nur bei Nutzung nötig):
    pip install sounddevice requests          # Aufnahme + Cloud
    pip install faster-whisper                # lokale Erkennung
"""
from __future__ import annotations

import io
import logging
import threading
import time
import wave
from typing import Any

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from accessmate.core import config as app_config
from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.core.win_keyboard_hook import (
    current_combo_str,
    is_altgr_fake_lctrl,
    shared_keyboard_hook,
    vk_to_combo_str,
)
from accessmate.gui.widgets.hotkey_edit import HotkeyEdit
from accessmate.modules.base import BaseModule

try:
    from pynput.keyboard import Controller as KeyController
    from pynput.keyboard import Key as PynputKey
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

_log = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000  # what whisper expects
_CHANNELS = 1


# ---------------------------------------------------------------------------
# Self-contained translations (the core locale files know nothing about this
# add-on).  Falls back to English for anything missing.
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "de": {
        "name": "Diktieren",
        "enabled": "Diktiermodul aktivieren",
        "description.long": "Hotkey drücken, sprechen, fertig – der erkannte Text wird in die aktive Anwendung eingefügt. Hinweis: Beim Cloud-Backend wird die Aufnahme an den gewählten Anbieter geschickt; beim lokalen Backend bleibt alles auf diesem PC.",
        "deps_missing": "⚠ Für dieses Add-on fehlen Komponenten. Zum Aktivieren im Programmordner ausführen:  pip install sounddevice requests  (für lokale Erkennung zusätzlich: faster-whisper)",
        "action": "Diktat starten/stoppen",
        "hotkey": "Diktier-Taste",
        "mode": "Aufnahmemodus",
        "mode.toggle": "Umschalten (Taste startet/stoppt)",
        "mode.hold": "Halten (sprechen solange gedrückt)",
        "backend": "Erkennung",
        "backend.cloud": "Cloud-Dienst (OpenRouter, OpenAI, Groq …)",
        "backend.local": "Lokal auf diesem PC",
        "backend.local.missing": "nicht installiert",
        "provider": "Anbieter",
        "provider.openrouter": "OpenRouter",
        "provider.openai": "OpenAI",
        "provider.groq": "Groq",
        "provider.custom": "Eigene URL (OpenAI-kompatibel)",
        "base_url": "Server-URL",
        "api_key": "API-Schlüssel",
        "api_key.hint": "Wird gerätweit gespeichert (nicht im Profil), derzeit im Klartext in app.json.",
        "model": "Modell",
        "local_model": "Whisper-Modell",
        "local.hint": "Beim ersten Diktat wird das Modell heruntergeladen (tiny ≈ 75 MB … large-v3 ≈ 1,5 GB). Größer = genauer, aber langsamer.",
        "local.not_installed": "Die lokale Erkennung ist auf diesem PC noch nicht installiert. Du kannst sie mit einem Klick automatisch installieren lassen – es sind keine Vorkenntnisse nötig.",
        "local.install": "Automatisch installieren",
        "local.install.running": "Wird installiert … Das kann einige Minuten dauern. Du kannst das Fenster geöffnet lassen.",
        "local.install.done": "Fertig! Die lokale Spracherkennung ist jetzt installiert und kann verwendet werden.",
        "local.install.failed": "Die Installation hat leider nicht geklappt: {err}\nBitte versuche es erneut oder nutze die Anleitung.",
        "local.howto": "Anleitung anzeigen",
        "local.howto.text": "So installierst du die lokale Spracherkennung von Hand:\n\n1. Öffne die Eingabeaufforderung (Windows-Taste drücken, „cmd“ eintippen, Enter).\n2. Tippe ein:  pip install faster-whisper\n3. Drücke Enter und warte, bis die Installation fertig ist.\n4. Starte AccessMate neu.\n\nTipp: Der Knopf „Automatisch installieren“ erledigt genau diese Schritte für dich.",
        "language": "Sprache",
        "lang.auto": "Automatisch erkennen",
        "insert": "Text einfügen per",
        "insert.clipboard": "Zwischenablage + Strg+V (schnell)",
        "insert.type": "Tippen (Zeichen für Zeichen)",
        "keep_clipboard": "Erkannten Text zusätzlich in der Zwischenablage behalten",
        "max_seconds": "Max. Aufnahmedauer",
        "device": "Mikrofon",
        "device.default": "Standardgerät",
        "test": "Test: 3 Sekunden aufnehmen und erkennen",
        "test.recording": "🎙 Aufnahme läuft (3 s) …",
        "test.result": "Erkannter Text:\n\n{text}",
        "test.error": "Test fehlgeschlagen:\n\n{err}",
        "chip.recording": "Aufnahme … (Esc bricht ab)",
        "chip.transcribing": "Erkenne Text …",
        "chip.error": "Diktat-Fehler",
        "err.no_audio_lib": "Audio-Bibliothek (sounddevice) fehlt",
        "err.mic": "Mikrofon-Fehler: {err}",
        "err.no_url": "Keine Server-URL konfiguriert",
        "err.no_key": "Kein API-Schlüssel hinterlegt",
        "err.no_local": "faster-whisper ist nicht installiert (pip install faster-whisper)",
    },
    "en": {
        "name": "Dictation",
        "enabled": "Enable dictation module",
        "description.long": "Press the hotkey, speak, done – the recognised text is inserted into the active application. Note: with the cloud backend the recording is sent to the chosen provider; with the local backend everything stays on this PC.",
        "deps_missing": "⚠ This add-on is missing components. To enable it, run in the program folder:  pip install sounddevice requests  (for local recognition also: faster-whisper)",
        "action": "Start/stop dictation",
        "hotkey": "Dictation key",
        "mode": "Recording mode",
        "mode.toggle": "Toggle (key starts/stops)",
        "mode.hold": "Hold (speak while pressed)",
        "backend": "Recognition",
        "backend.cloud": "Cloud service (OpenRouter, OpenAI, Groq …)",
        "backend.local": "Locally on this PC",
        "backend.local.missing": "not installed",
        "provider": "Provider",
        "provider.openrouter": "OpenRouter",
        "provider.openai": "OpenAI",
        "provider.groq": "Groq",
        "provider.custom": "Custom URL (OpenAI-compatible)",
        "base_url": "Server URL",
        "api_key": "API key",
        "api_key.hint": "Stored device-wide (not in the profile), currently in plain text in app.json.",
        "model": "Model",
        "local_model": "Whisper model",
        "local.hint": "The model is downloaded on first use (tiny ≈ 75 MB … large-v3 ≈ 1.5 GB). Bigger = more accurate but slower.",
        "local.not_installed": "Local recognition is not installed on this PC yet. You can have it installed automatically with one click – no technical knowledge needed.",
        "local.install": "Install automatically",
        "local.install.running": "Installing … This may take a few minutes. You can keep this window open.",
        "local.install.done": "Done! Local speech recognition is now installed and ready to use.",
        "local.install.failed": "The installation did not work: {err}\nPlease try again or use the instructions.",
        "local.howto": "Show instructions",
        "local.howto.text": "How to install local speech recognition manually:\n\n1. Open the command prompt (press the Windows key, type \"cmd\", press Enter).\n2. Type:  pip install faster-whisper\n3. Press Enter and wait until the installation finishes.\n4. Restart AccessMate.\n\nTip: the \"Install automatically\" button does exactly these steps for you.",
        "language": "Language",
        "lang.auto": "Detect automatically",
        "insert": "Insert text via",
        "insert.clipboard": "Clipboard + Ctrl+V (fast)",
        "insert.type": "Typing (character by character)",
        "keep_clipboard": "Also keep the recognised text in the clipboard",
        "max_seconds": "Max. recording length",
        "device": "Microphone",
        "device.default": "Default device",
        "test": "Test: record 3 seconds and transcribe",
        "test.recording": "🎙 Recording (3 s) …",
        "test.result": "Recognised text:\n\n{text}",
        "test.error": "Test failed:\n\n{err}",
        "chip.recording": "Recording … (Esc cancels)",
        "chip.transcribing": "Transcribing …",
        "chip.error": "Dictation error",
        "err.no_audio_lib": "Audio library (sounddevice) missing",
        "err.mic": "Microphone error: {err}",
        "err.no_url": "No server URL configured",
        "err.no_key": "No API key configured",
        "err.no_local": "faster-whisper is not installed (pip install faster-whisper)",
    },
}


class _Lang:
    """Tracks the app's active language for this module's own strings."""

    def __init__(self) -> None:
        self.code = "en"
        try:
            self.code = app_config.load_app_config().get("language", "en")
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


# Theme-aware, self-contained label styles (no dependency on the core theme
# module): palette(mid) follows light/dark, relative font sizes follow the
# global font-size setting.
def _hint_style() -> str:
    return "color: palette(mid); font-size: smaller;"


def _warn_style() -> str:
    return "color: #D9534F; font-size: smaller;"   # readable on light + dark


def _title_style() -> str:
    return "font-weight: bold; font-size: larger;"


def _sync_module_checkbox(widget: QWidget, module: "DictationModule",
                          checkbox: QCheckBox,
                          update_enabled_state: Any) -> None:
    """Keep the page's enable-checkbox in sync when the module is toggled
    elsewhere (emergency stop, tray, profile switch).  Self-contained copy of
    the core helper so the module needs nothing from accessmate.gui.settings."""

    def on_state(module_id: str, **_: object) -> None:
        if module_id != module.MODULE_ID:
            return
        checkbox.blockSignals(True)
        checkbox.setChecked(module.enabled)
        checkbox.blockSignals(False)
        update_enabled_state(module.enabled)

    bus.subscribe("module.started", on_state)
    bus.subscribe("module.stopped", on_state)
    widget.destroyed.connect(lambda: (
        bus.unsubscribe("module.started", on_state),
        bus.unsubscribe("module.stopped", on_state)))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Provider presets: id → (base_url, request style, suggested models)
PROVIDERS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "style": "openrouter",           # JSON body with base64 audio
        "models": ["openai/whisper-1", "openai/gpt-4o-mini-transcribe",
                   "google/chirp-3"],
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "style": "multipart",
        "models": ["whisper-1", "gpt-4o-mini-transcribe"],
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "style": "multipart",
        "models": ["whisper-large-v3", "whisper-large-v3-turbo"],
    },
    "custom": {
        "base_url": "",
        "style": "multipart",
        "models": [],
    },
}

LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

LANGUAGES = ["auto", "de", "en", "fr", "es", "it", "nl", "pl", "pt", "ru",
             "tr", "uk", "zh", "ja"]


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def list_input_devices() -> list[tuple[int, str]]:
    """Unique input devices as (sounddevice index, name), preferring WASAPI."""
    import sounddevice as sd

    apis = sd.query_hostapis()

    def api_rank(dev: dict) -> int:
        name = apis[dev["hostapi"]]["name"].lower()
        if "wasapi" in name:
            return 0
        if "directsound" in name:
            return 1
        return 2

    devices = list(enumerate(sd.query_devices()))
    inputs = [(i, d) for i, d in devices if d.get("max_input_channels", 0) > 0]
    wasapi = [(i, d) for i, d in inputs if api_rank(d) == 0]
    pool = wasapi if wasapi else inputs

    best: dict[str, tuple[int, int]] = {}  # name → (rank, index)
    for idx, dev in pool:
        rank = api_rank(dev)
        current = best.get(dev["name"])
        if current is None or rank < current[0]:
            best[dev["name"]] = (rank, idx)
    return [(idx, name) for name, (_r, idx) in sorted(
        best.items(), key=lambda kv: kv[0].lower())]


def resolve_input_device(value: Any) -> int | None:
    """Translate the stored device setting into a sounddevice index."""
    if value in (None, "", "default"):
        return None
    if isinstance(value, int):
        return value
    for idx, name in list_input_devices():
        if name == value:
            return idx
    return None


def open_input_stream(sd: Any, device: int | None,
                      callback: Any) -> tuple[Any, int, int]:
    """Open a RawInputStream, falling back to the device's native format."""
    try:
        info = sd.query_devices(device, "input")
    except Exception:
        info = {"default_samplerate": 48_000, "max_input_channels": 1}
    native_rate = int(info.get("default_samplerate") or 48_000)
    max_ch = max(1, int(info.get("max_input_channels") or 1))

    attempts = [(_SAMPLE_RATE, 1)]
    if (native_rate, 1) not in attempts:
        attempts.append((native_rate, 1))
    attempts.append((native_rate, min(2, max_ch)))

    last_exc: Exception | None = None
    for rate, channels in attempts:
        try:
            stream = sd.RawInputStream(
                samplerate=rate, channels=channels, dtype="int16",
                callback=callback, device=device)
            stream.start()
            return stream, rate, channels
        except Exception as exc:
            last_exc = exc
    raise last_exc or RuntimeError("no usable input format")


def audio_available() -> bool:
    """True if the optional recording/cloud dependencies are installed."""
    try:
        import requests  # noqa: F401
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


def local_backend_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Status chip (recording / transcribing / error)
# ---------------------------------------------------------------------------

_CHIP_COLORS = {
    "recording": "#C62828",     # red
    "transcribing": "#1565C0",  # blue
    "error": "#7B3F00",         # dark orange/brown
}
_CHIP_FG = "#FFFFFF"
_CHIP_RADIUS = 6
_CHIP_MARGIN = 12
_CHIP_DEFAULT_H = 28
_CHIP_ERROR_MS = 3500
_CHIP_PULSE_MS = 40
_CHIP_PULSE_PERIOD_MS = 1100


class _ChipBridge(QObject):
    state = Signal(str, str)


class DictationIndicator(QWidget):
    """Top-centre chip: pulsing red while recording, blue while transcribing,
    brief brown on errors."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._chip_h = _CHIP_DEFAULT_H
        self._state = "idle"
        self._detail = ""
        self._pulse_opacity = 1.0
        self._pulse_elapsed = 0

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(_CHIP_PULSE_MS)
        self._pulse_timer.timeout.connect(self._on_pulse)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.setInterval(_CHIP_ERROR_MS)
        self._error_timer.timeout.connect(self._clear_error)

        self._bridge = _ChipBridge()
        self._bridge.state.connect(self._apply_state)
        bus.subscribe("dictation.state", self._on_state)

    def _on_state(self, state: str, detail: str = "", **_: object) -> None:
        self._bridge.state.emit(state, detail)

    def _apply_state(self, state: str, detail: str) -> None:
        self._error_timer.stop()
        self._state = state
        self._detail = detail
        if state == "recording":
            self._start_pulse()
        else:
            self._stop_pulse()
        if state in ("recording", "transcribing"):
            self._update_geometry()
            self.show()
            self.update()
        elif state == "error":
            self._update_geometry()
            self.show()
            self.update()
            self._error_timer.start()
        else:  # idle
            self.hide()

    def _clear_error(self) -> None:
        if self._state == "error":
            self._state = "idle"
            self.hide()

    def _start_pulse(self) -> None:
        self._pulse_elapsed = 0
        self._pulse_opacity = 1.0
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()

    def _stop_pulse(self) -> None:
        self._pulse_timer.stop()
        self._pulse_opacity = 1.0

    def _on_pulse(self) -> None:
        import math
        self._pulse_elapsed += _CHIP_PULSE_MS
        phase = (self._pulse_elapsed % _CHIP_PULSE_PERIOD_MS) / _CHIP_PULSE_PERIOD_MS
        self._pulse_opacity = 0.775 + 0.225 * math.cos(phase * 2 * math.pi)
        self.update()

    def _label(self) -> str:
        if self._state == "recording":
            return f"🎙 {_t('chip.recording')}"
        if self._state == "transcribing":
            return f"⏳ {_t('chip.transcribing')}"
        if self._state == "error":
            detail = f" – {self._detail}" if self._detail else ""
            return f"⚠ {_t('chip.error')}{detail}"
        return ""

    def _chip_w(self) -> int:
        from PySide6.QtGui import QFontMetrics
        font = self.font()
        font.setPixelSize(max(10, int(self._chip_h * 0.5)))
        font.setBold(True)
        return QFontMetrics(font).horizontalAdvance(self._label()) + 28

    def _update_geometry(self) -> None:
        self.setFixedSize(self._chip_w() + 2 * _CHIP_MARGIN,
                          self._chip_h + 2 * _CHIP_MARGIN)
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        x = geom.x() + (geom.width() - self.width()) // 2
        y = geom.y() + _CHIP_MARGIN + self._chip_h + 2 * _CHIP_MARGIN
        self.move(x, y)

    def paintEvent(self, _event: object) -> None:  # type: ignore[override]
        if self._state == "idle":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._pulse_opacity)

        path = QPainterPath()
        path.addRoundedRect(_CHIP_MARGIN, _CHIP_MARGIN, self._chip_w(),
                            self._chip_h, _CHIP_RADIUS, _CHIP_RADIUS)
        p.fillPath(path, QColor(_CHIP_COLORS.get(self._state, "#444444")))

        p.setPen(QColor(_CHIP_FG))
        font = p.font()
        font.setPixelSize(max(10, int(self._chip_h * 0.5)))
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRect(_CHIP_MARGIN, _CHIP_MARGIN, self._chip_w(),
                         self._chip_h),
                   Qt.AlignmentFlag.AlignCenter, self._label())
        p.end()


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

class _TestBridge(QObject):
    finished = Signal(bool, str)   # ok, text-or-error


class _InstallBridge(QObject):
    finished = Signal(bool, str)   # ok, error text


class DictationSettingsWidget(QWidget):
    def __init__(self, module: "DictationModule",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._settings = module._settings
        self._test_bridge = _TestBridge()
        self._test_bridge.finished.connect(self._on_test_finished)
        self._install_bridge = _InstallBridge()
        self._install_bridge.finished.connect(self._on_install_finished)
        self._build_ui()
        _sync_module_checkbox(self, module, self._enabled_cb,
                              self._update_enabled_state)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Module toggle + privacy note ──────────────────────────────
        self._enabled_cb = QCheckBox(_t("enabled"))
        self._enabled_cb.setChecked(self._module.enabled)
        self._enabled_cb.setStyleSheet(_title_style())
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        desc = QLabel(_t("description.long"))
        desc.setStyleSheet(_hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if not audio_available():
            missing = QLabel(_t("deps_missing"))
            missing.setStyleSheet(_warn_style())
            missing.setWordWrap(True)
            layout.addWidget(missing)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        form = QFormLayout()
        form.setSpacing(8)
        self._form = form

        # ── Hotkey + mode ─────────────────────────────────────────────
        self._hotkey = HotkeyEdit(self._settings.get("hotkey", ""),
                                  action_id="dictation.toggle")
        self._hotkey.key_changed.connect(lambda k: self._save("hotkey", k))
        form.addRow(_t("hotkey"), self._hotkey)

        self._mode = QComboBox()
        self._mode.addItem(_t("mode.toggle"), "toggle")
        self._mode.addItem(_t("mode.hold"), "hold")
        if self._settings.get("mode", "toggle") == "hold":
            self._mode.setCurrentIndex(1)
        self._mode.currentIndexChanged.connect(
            lambda i: self._save("mode", self._mode.itemData(i)))
        form.addRow(_t("mode"), self._mode)

        # ── Backend ───────────────────────────────────────────────────
        self._backend = QComboBox()
        self._backend.addItem(_t("backend.cloud"), "cloud")
        local_label = _t("backend.local")
        if not local_backend_available():
            local_label += f" ({_t('backend.local.missing')})"
        self._backend.addItem(local_label, "local")
        if self._settings.get("backend", "cloud") == "local":
            self._backend.setCurrentIndex(1)
        self._backend.currentIndexChanged.connect(self._on_backend_changed)
        form.addRow(_t("backend"), self._backend)

        # Cloud fields
        self._provider = QComboBox()
        for pid in PROVIDERS:
            self._provider.addItem(_t(f"provider.{pid}"), pid)
        saved_provider = self._settings.get("provider", "openrouter")
        ids = list(PROVIDERS.keys())
        if saved_provider in ids:
            self._provider.setCurrentIndex(ids.index(saved_provider))
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow(_t("provider"), self._provider)

        self._base_url = QLineEdit(self._settings.get("base_url", ""))
        self._base_url.setPlaceholderText("https://…/v1")
        self._base_url.setMinimumWidth(280)
        self._base_url.editingFinished.connect(
            lambda: self._save("base_url", self._base_url.text().strip()))
        form.addRow(_t("base_url"), self._base_url)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setMinimumWidth(280)
        self._api_key.setText(self._module.get_api_key(saved_provider))
        self._api_key.editingFinished.connect(self._on_api_key_changed)
        form.addRow(_t("api_key"), self._api_key)

        self._key_hint = QLabel(_t("api_key.hint"))
        self._key_hint.setStyleSheet(_hint_style())
        self._key_hint.setWordWrap(True)
        form.addRow("", self._key_hint)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._fill_models(saved_provider)
        saved_model = self._settings.get("model", "")
        if saved_model:
            self._model.setEditText(saved_model)
        self._model.currentTextChanged.connect(
            lambda t: self._save("model", t.strip()))
        form.addRow(_t("model"), self._model)

        # Local fields
        self._local_model = QComboBox()
        for m in LOCAL_MODELS:
            self._local_model.addItem(m, m)
        saved_local = self._settings.get("local_model", "base")
        if saved_local in LOCAL_MODELS:
            self._local_model.setCurrentIndex(LOCAL_MODELS.index(saved_local))
        self._local_model.currentIndexChanged.connect(
            lambda i: self._save("local_model", self._local_model.itemData(i)))
        form.addRow(_t("local_model"), self._local_model)

        self._local_hint = QLabel(_t("local.hint"))
        self._local_hint.setStyleSheet(_hint_style())
        self._local_hint.setWordWrap(True)
        form.addRow("", self._local_hint)

        # Local backend not installed yet: offer a fully automatic install
        # plus a plain-language how-to, so no command line is ever needed.
        self._install_box = QWidget()
        install_layout = QVBoxLayout(self._install_box)
        install_layout.setContentsMargins(0, 0, 0, 0)
        install_layout.setSpacing(6)
        install_note = QLabel(_t("local.not_installed"))
        install_note.setStyleSheet(_warn_style())
        install_note.setWordWrap(True)
        install_layout.addWidget(install_note)
        from PySide6.QtWidgets import QHBoxLayout
        install_btns = QHBoxLayout()
        self._install_btn = QPushButton(_t("local.install"))
        self._install_btn.clicked.connect(self._on_install_local)
        install_btns.addWidget(self._install_btn)
        howto_btn = QPushButton(_t("local.howto"))
        howto_btn.clicked.connect(self._on_show_howto)
        install_btns.addWidget(howto_btn)
        install_btns.addStretch()
        install_layout.addLayout(install_btns)
        self._install_status = QLabel("")
        self._install_status.setWordWrap(True)
        install_layout.addWidget(self._install_status)
        form.addRow("", self._install_box)

        # ── Common options ────────────────────────────────────────────
        self._lang = QComboBox()
        for code in LANGUAGES:
            label = _t("lang.auto") if code == "auto" else code
            self._lang.addItem(label, code)
        saved_lang = self._settings.get("language", "auto")
        if saved_lang in LANGUAGES:
            self._lang.setCurrentIndex(LANGUAGES.index(saved_lang))
        self._lang.currentIndexChanged.connect(
            lambda i: self._save("language", self._lang.itemData(i)))
        form.addRow(_t("language"), self._lang)

        self._insert = QComboBox()
        self._insert.addItem(_t("insert.clipboard"), "clipboard")
        self._insert.addItem(_t("insert.type"), "type")
        if self._settings.get("insert_method", "clipboard") == "type":
            self._insert.setCurrentIndex(1)
        self._insert.currentIndexChanged.connect(
            lambda i: self._save("insert_method", self._insert.itemData(i)))
        form.addRow(_t("insert"), self._insert)

        self._keep_clipboard = QCheckBox(_t("keep_clipboard"))
        self._keep_clipboard.setChecked(
            bool(self._settings.get("keep_in_clipboard", False)))
        self._keep_clipboard.toggled.connect(
            lambda v: self._save("keep_in_clipboard", v))
        form.addRow("", self._keep_clipboard)

        self._max_seconds = QSpinBox()
        self._max_seconds.setRange(5, 600)
        self._max_seconds.setSuffix(" s")
        self._max_seconds.setValue(int(self._settings.get("max_seconds", 120)))
        self._max_seconds.valueChanged.connect(
            lambda v: self._save("max_seconds", v))
        form.addRow(_t("max_seconds"), self._max_seconds)

        self._device = QComboBox()
        self._device.addItem(_t("device.default"), "default")
        try:
            for idx, name in list_input_devices():
                self._device.addItem(name, idx)
        except Exception:
            pass
        saved_dev = self._settings.get("input_device", "default")
        for i in range(self._device.count()):
            data = self._device.itemData(i)
            if data == saved_dev or self._device.itemText(i) == saved_dev:
                self._device.setCurrentIndex(i)
                break
        self._device.currentIndexChanged.connect(
            lambda i: self._save("input_device", self._device.itemData(i)))
        form.addRow(_t("device"), self._device)

        layout.addLayout(form)

        # ── Test button ───────────────────────────────────────────────
        self._test_btn = QPushButton(_t("test"))
        self._test_btn.clicked.connect(self._on_test)
        layout.addWidget(self._test_btn)

        layout.addStretch()
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._on_backend_changed(self._backend.currentIndex())
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _fill_models(self, provider: str) -> None:
        self._model.blockSignals(True)
        self._model.clear()
        for m in PROVIDERS.get(provider, {}).get("models", []):
            self._model.addItem(m)
        self._model.blockSignals(False)

    def _on_provider_changed(self, index: int) -> None:
        provider = self._provider.itemData(index)
        self._save("provider", provider)
        self._fill_models(provider)
        first = PROVIDERS.get(provider, {}).get("models", [])
        self._model.setEditText(first[0] if first else "")
        self._api_key.setText(self._module.get_api_key(provider))
        self._update_cloud_rows()

    def _on_api_key_changed(self) -> None:
        provider = self._provider.currentData()
        self._module.set_api_key(provider, self._api_key.text().strip())

    def _on_backend_changed(self, index: int) -> None:
        backend = self._backend.itemData(index)
        self._save("backend", backend)
        cloud = backend == "cloud"
        for widget in (self._provider, self._api_key, self._model,
                       self._key_hint):
            self._form.setRowVisible(widget, cloud)
        self._form.setRowVisible(self._local_model, not cloud)
        self._local_hint.setVisible(not cloud)
        self._form.setRowVisible(
            self._install_box, not cloud and not local_backend_available())
        self._update_cloud_rows()

    def _update_cloud_rows(self) -> None:
        cloud = self._backend.currentData() == "cloud"
        custom = self._provider.currentData() == "custom"
        self._form.setRowVisible(self._base_url, cloud and custom)

    # ------------------------------------------------------------------
    # Local backend installation (one click, no command line)
    # ------------------------------------------------------------------

    def _on_install_local(self) -> None:
        self._install_btn.setEnabled(False)
        self._install_status.setStyleSheet(_hint_style())
        self._install_status.setText(_t("local.install.running"))

        import subprocess
        import sys

        def run() -> None:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "faster-whisper"],
                    capture_output=True, text=True, timeout=900,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 0:
                    tail = (result.stderr or result.stdout or "").strip()
                    self._install_bridge.finished.emit(False, tail[-300:])
                else:
                    self._install_bridge.finished.emit(True, "")
            except Exception as exc:
                self._install_bridge.finished.emit(False, str(exc)[:300])

        threading.Thread(target=run, daemon=True,
                         name="dictation-install").start()

    def _on_install_finished(self, ok: bool, err: str) -> None:
        self._install_btn.setEnabled(True)
        if ok and local_backend_available():
            self._install_status.setText("")
            self._backend.setItemText(1, _t("backend.local"))
            self._form.setRowVisible(self._install_box, False)
            QMessageBox.information(self, _t("local.install"),
                                    _t("local.install.done"))
        else:
            self._install_status.setStyleSheet(_warn_style())
            self._install_status.setText(
                _t("local.install.failed", err=err))

    def _on_show_howto(self) -> None:
        QMessageBox.information(self, _t("local.howto"),
                                _t("local.howto.text"))

    # ------------------------------------------------------------------
    # Test recording (3 s → transcribe → show result)
    # ------------------------------------------------------------------

    def _on_test(self) -> None:
        self._test_btn.setEnabled(False)
        self._test_btn.setText(_t("test.recording"))

        def run() -> None:
            try:
                import sounddevice as sd
                chunks: list[bytes] = []

                def cb(indata, _f, _t2, _s) -> None:
                    chunks.append(bytes(indata))

                device = resolve_input_device(
                    self._settings.get("input_device"))
                stream, rate, channels = open_input_stream(sd, device, cb)
                time.sleep(3.0)
                stream.stop()
                stream.close()
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(channels)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(b"".join(chunks))
                text = self._module.transcribe(buf.getvalue())
                self._test_bridge.finished.emit(True, text or "")
            except Exception as exc:
                _log.exception("dictation test failed")
                self._test_bridge.finished.emit(False, str(exc)[:300])

        threading.Thread(target=run, daemon=True).start()

    def _on_test_finished(self, ok: bool, text: str) -> None:
        self._test_btn.setEnabled(True)
        self._test_btn.setText(_t("test"))
        if ok:
            QMessageBox.information(
                self, _t("test"), _t("test.result", text=text or "—"))
        else:
            QMessageBox.warning(
                self, _t("test"), _t("test.error", err=text))

    # ------------------------------------------------------------------

    def _on_module_toggled(self, enabled: bool) -> None:
        if enabled:
            self._module.enable()
        else:
            self._module.disable()
        self._update_enabled_state(enabled)

    def _update_enabled_state(self, enabled: bool) -> None:
        for w in (self._hotkey, self._mode, self._backend, self._provider,
                  self._base_url, self._api_key, self._model,
                  self._local_model, self._lang, self._insert,
                  self._keep_clipboard, self._max_seconds, self._device,
                  self._test_btn):
            w.setEnabled(enabled)


# ---------------------------------------------------------------------------
# The module
# ---------------------------------------------------------------------------

class DictationModule(BaseModule):
    MODULE_ID = "dictation"
    DESCRIPTION = "Diktieren – Sprache zu Text per Whisper"

    @property
    def DISPLAY_NAME(self) -> str:  # type: ignore[override]
        return _t("name")

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._kb_subscribed = False
        self._trigger = ""
        self._state = "idle"            # idle | recording | transcribing
        self._state_lock = threading.Lock()
        self._audio_chunks: list[bytes] = []
        self._stream: Any = None
        self._record_started = 0.0
        self._max_timer: threading.Timer | None = None
        self._local_model: Any = None   # lazily loaded faster-whisper model
        self._local_model_name = ""
        self._indicator: DictationIndicator | None = None

        # Listed in the actions table / favourites / conflict checks; the key
        # itself is handled by our own hook subscription below.
        action_manager.register(Action(
            id="dictation.toggle",
            label=_t("action"),
            callback=lambda: None,
        ))

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._ensure_indicator()
        self._refresh_trigger()
        if not self._kb_subscribed:
            shared_keyboard_hook.subscribe(self._on_key_event)
            self._kb_subscribed = True
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        if self._kb_subscribed:
            shared_keyboard_hook.unsubscribe(self._on_key_event)
            self._kb_subscribed = False
        self._abort_recording()
        self._set_state("idle")
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        return DictationSettingsWidget(self)

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self.on_settings_changed()

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    def on_settings_changed(self) -> None:
        self._refresh_trigger()
        action_manager.assign_trigger(
            "dictation.toggle", self._trigger if self.enabled else "")
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    # ------------------------------------------------------------------

    def _ensure_indicator(self) -> None:
        # Created lazily so a merely-loaded (never enabled) module opens no
        # overlay window.  It subscribes to dictation.state on creation.
        if self._indicator is None:
            self._indicator = DictationIndicator()

    # ------------------------------------------------------------------
    # Hotkey handling (shared hook)
    # ------------------------------------------------------------------

    def _refresh_trigger(self) -> None:
        self._trigger = self._settings.get("hotkey", "")

    def _on_key_event(self, vk: int, scan: int, extended: bool,
                      injected: bool, is_press: bool) -> bool:
        """Hook-thread callback – must return fast, never block."""
        if injected or not self._trigger:
            return False
        if is_altgr_fake_lctrl(vk, scan):
            return False

        hold_mode = self._settings.get("mode", "toggle") == "hold"

        if is_press:
            if vk == 0x1B and self._state == "recording":
                threading.Thread(target=self._abort_recording,
                                 daemon=True).start()
                return True
            combo = current_combo_str(vk)
            if combo == self._trigger:
                if self._state == "recording" and not hold_mode:
                    threading.Thread(target=self._stop_and_transcribe,
                                     daemon=True).start()
                elif self._state == "idle":
                    threading.Thread(target=self._start_recording,
                                     daemon=True).start()
                return True  # swallow the hotkey
            return False

        if (hold_mode and self._state == "recording"
                and vk_to_combo_str(vk) == self._trigger.split("+")[-1]):
            threading.Thread(target=self._stop_and_transcribe,
                             daemon=True).start()
        return False

    # ------------------------------------------------------------------
    # State / indicator
    # ------------------------------------------------------------------

    def _set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        bus.publish("dictation.state", state=state, detail=detail)

    def _error(self, detail: str) -> None:
        _log.error("dictation error: %s", detail)
        self._set_state("idle")
        bus.publish("dictation.state", state="error", detail=detail)

    # ------------------------------------------------------------------
    # Recording (sounddevice)
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        with self._state_lock:
            if self._state != "idle":
                return
            try:
                import sounddevice as sd
            except Exception:
                self._error(_t("err.no_audio_lib"))
                return

            self._audio_chunks = []

            def callback(indata, _frames, _time, _status) -> None:
                self._audio_chunks.append(bytes(indata))

            try:
                device = resolve_input_device(
                    self._settings.get("input_device"))
            except Exception:
                device = None
            try:
                self._stream, self._rec_rate, self._rec_channels = (
                    open_input_stream(sd, device, callback))
            except Exception as exc:
                self._stream = None
                self._error(_t("err.mic", err=str(exc)[:80]))
                return

            self._record_started = time.monotonic()
            self._set_state("recording")

            max_s = int(self._settings.get("max_seconds", 120))
            self._max_timer = threading.Timer(max_s, self._stop_and_transcribe)
            self._max_timer.daemon = True
            self._max_timer.start()

    def _close_stream(self) -> bytes:
        """Stop the stream and return the recorded WAV bytes."""
        if self._max_timer:
            self._max_timer.cancel()
            self._max_timer = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        raw = b"".join(self._audio_chunks)
        self._audio_chunks = []
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(getattr(self, "_rec_channels", _CHANNELS))
            w.setsampwidth(2)
            w.setframerate(getattr(self, "_rec_rate", _SAMPLE_RATE))
            w.writeframes(raw)
        return buf.getvalue()

    def _abort_recording(self) -> None:
        with self._state_lock:
            if self._state != "recording":
                return
            self._close_stream()
            self._set_state("idle")

    def _stop_and_transcribe(self) -> None:
        with self._state_lock:
            if self._state != "recording":
                return
            wav = self._close_stream()
            duration = time.monotonic() - self._record_started
            if duration < 0.4 or len(wav) < 8000:
                self._set_state("idle")  # too short to contain speech
                return
            self._set_state("transcribing")
        try:
            text = self.transcribe(wav)
        except Exception as exc:
            self._error(str(exc)[:120])
            return
        self._set_state("idle")
        text = (text or "").strip()
        if text:
            self._insert_text(text)

    # ------------------------------------------------------------------
    # Transcription backends
    # ------------------------------------------------------------------

    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio using the configured backend (blocking)."""
        if self._settings.get("backend", "cloud") == "local":
            return self._transcribe_local(wav_bytes)
        return self._transcribe_cloud(wav_bytes)

    def _language(self) -> str | None:
        lang = self._settings.get("language", "auto")
        return None if lang in ("", "auto") else lang

    # -- Cloud (OpenAI-compatible / OpenRouter) --------------------------

    def _cloud_config(self) -> tuple[str, str, str, str]:
        """(base_url, style, model, api_key) from settings + app config."""
        provider = self._settings.get("provider", "openrouter")
        preset = PROVIDERS.get(provider, PROVIDERS["custom"])
        base_url = (self._settings.get("base_url", "")
                    if provider == "custom" else preset["base_url"])
        style = preset["style"]
        model = self._settings.get("model", "") or (
            preset["models"][0] if preset["models"] else "whisper-1")
        cfg = app_config.load_app_config()
        api_key = cfg.get("dictation_api_keys", {}).get(provider, "")
        return base_url.rstrip("/"), style, model, api_key

    @staticmethod
    def get_api_key(provider: str) -> str:
        return app_config.load_app_config().get(
            "dictation_api_keys", {}).get(provider, "")

    @staticmethod
    def set_api_key(provider: str, key: str) -> None:
        cfg = app_config.load_app_config()
        cfg.setdefault("dictation_api_keys", {})[provider] = key
        app_config.save_app_config(cfg)

    def _transcribe_cloud(self, wav_bytes: bytes) -> str:
        import requests

        base_url, style, model, api_key = self._cloud_config()
        if not base_url:
            raise RuntimeError(_t("err.no_url"))
        if not api_key:
            raise RuntimeError(_t("err.no_key"))

        url = f"{base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        lang = self._language()

        if style == "openrouter":
            import base64
            payload: dict[str, Any] = {
                "model": model,
                "input_audio": {
                    "data": base64.b64encode(wav_bytes).decode("ascii"),
                    "format": "wav",
                },
            }
            if lang:
                payload["language"] = lang
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=60)
        else:  # standard OpenAI multipart
            data = {"model": model}
            if lang:
                data["language"] = lang
            resp = requests.post(
                url, headers=headers, data=data,
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                timeout=60,
            )

        if resp.status_code != 200:
            raise RuntimeError(f"API {resp.status_code}: {resp.text[:120]}")
        return resp.json().get("text", "")

    # -- Local (faster-whisper) ------------------------------------------

    def _transcribe_local(self, wav_bytes: bytes) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(_t("err.no_local"))

        model_name = self._settings.get("local_model", "base")
        if self._local_model is None or self._local_model_name != model_name:
            _log.info("loading local whisper model %r …", model_name)
            self._local_model = WhisperModel(model_name, device="auto",
                                             compute_type="auto")
            self._local_model_name = model_name

        segments, _info = self._local_model.transcribe(
            io.BytesIO(wav_bytes), language=self._language())
        return " ".join(seg.text.strip() for seg in segments)

    # ------------------------------------------------------------------
    # Text insertion
    # ------------------------------------------------------------------

    def _insert_text(self, text: str) -> None:
        if not PYNPUT_AVAILABLE:
            return
        method = self._settings.get("insert_method", "clipboard")
        keep = bool(self._settings.get("keep_in_clipboard", False))
        if method == "type":
            KeyController().type(text)
            if keep:
                self._set_clipboard(text)
            return
        self._paste_via_clipboard(text, keep=keep)

    @staticmethod
    def _clipboard_funcs():
        """Return (get_text, set_text) helpers for the Windows clipboard."""
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        kernel32.GlobalAlloc.restype = wt.HGLOBAL
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = (wt.HGLOBAL,)
        kernel32.GlobalUnlock.argtypes = (wt.HGLOBAL,)
        user32.GetClipboardData.restype = wt.HANDLE
        user32.SetClipboardData.restype = wt.HANDLE
        user32.SetClipboardData.argtypes = (wt.UINT, wt.HANDLE)

        def get_text() -> str | None:
            if not user32.OpenClipboard(None):
                return None
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return None
                ptr = kernel32.GlobalLock(handle)
                try:
                    return ctypes.wstring_at(ptr) if ptr else None
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()

        def set_text(value: str) -> bool:
            if not user32.OpenClipboard(None):
                return False
            try:
                user32.EmptyClipboard()
                data = value.encode("utf-16-le") + b"\x00\x00"
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                ptr = kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                    kernel32.GlobalFree(handle)
                    return False
                return True
            finally:
                user32.CloseClipboard()

        return get_text, set_text

    @classmethod
    def _set_clipboard(cls, text: str) -> None:
        try:
            _get, set_text = cls._clipboard_funcs()
            set_text(text)
        except Exception:
            pass

    @classmethod
    def _paste_via_clipboard(cls, text: str, keep: bool = False) -> None:
        """Put text on the clipboard, send Ctrl+V, then optionally restore
        the previous clipboard (keep=False) or leave the text (keep=True)."""
        get_text, set_text = cls._clipboard_funcs()

        previous = None if keep else get_text()
        if not set_text(text):
            KeyController().type(text)  # clipboard busy – fall back to typing
            return

        time.sleep(0.05)
        ctrl = KeyController()
        ctrl.press(PynputKey.ctrl)
        ctrl.press("v")
        ctrl.release("v")
        ctrl.release(PynputKey.ctrl)

        if previous is not None:
            threading.Timer(0.4, lambda: set_text(previous)).start()
