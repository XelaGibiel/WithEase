"""Diktieren – externes WithEase-Modul (Sprache zu Text per Whisper).

Zum Installieren diesen Ordner nach %APPDATA%/WithEase/modules/ kopieren
und WithEase neu starten – „Diktieren“ erscheint dann als eigene Kategorie
unten in den Einstellungen.

Ablauf: Hotkey drücken → Aufnahme (🎙-Chip erscheint) → erneut drücken bzw.
Taste loslassen stoppt → der erkannte Text wird in die aktive Anwendung
eingefügt.  Erkennung wahlweise per OpenAI-kompatibler Cloud-API
(OpenRouter/OpenAI/Groq/eigene URL) oder lokal via faster-whisper.

Dieses Modul ist autark: es bringt seine eigenen deutschen und englischen
Texte mit und hängt nur an der öffentlichen WithEase-Erweiterungs-API
(BaseModule, Event-Bus, ActionManager, geteilter Tastatur-Hook, App-Config
und das wiederverwendbare HotkeyEdit-Widget).  Der Kern weiß nichts von ihm.

Optionale Abhängigkeiten (nur bei Nutzung nötig):
    pip install sounddevice requests          # Aufnahme + Cloud
    pip install audioop-lts                    # nur Python ≥ 3.13 (stdlib-Ersatz)
    pip install faster-whisper                # lokale Erkennung

Einfacher: In den Diktat-Einstellungen erledigt der Knopf „Automatisch
installieren“ das für den Nutzer (siehe ``missing_audio_packages`` und
``requirements.txt`` in diesem Ordner).
"""
from __future__ import annotations

import faulthandler
import io
import json
import logging
import os
import queue
import sys
import threading
import time
import wave
from typing import Any

# Vosk (Kaldi/OpenBLAS) and faster-whisper (CTranslate2) each ship their own
# OpenMP runtime.  When both are loaded in one process on Windows, the duplicate
# OpenMP runtimes abort the whole process ("OMP: Error #15") – which looked like
# the app "just closing".  Allow the duplicate; must be set before either lib
# loads (they are imported lazily further down).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# The live noise-gate uses the stdlib ``audioop`` (not numpy) on purpose: the
# main process runs Vosk, and we keep every *other* heavy native library
# (numpy, CTranslate2, PyAV) OUT of it – faster-whisper runs in a separate
# process (see WhisperProc) so the two never share native runtimes/OpenMP,
# which is what crashed the app when they lived together.
import warnings as _warnings

# ``audioop`` was removed from the stdlib in Python 3.13.  The drop-in
# replacement is the ``audioop-lts`` package (it still imports as ``audioop``).
# Import it defensively: a missing audioop must NEVER crash the whole add-on at
# import time, or the module never loads and the user never even sees the
# one-click installer that would fix it.  When it is absent the recording path
# stays disabled and the settings page offers to install it (see
# ``audio_available`` / ``missing_audio_packages``).  Every audioop call site is
# already wrapped in try/except with a safe fallback, so ``audioop = None`` is
# handled gracefully throughout.
try:
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", DeprecationWarning)  # audioop -> 3.13
        import audioop
except ImportError:
    audioop = None  # type: ignore[assignment]


def audioop_available() -> bool:
    """True if ``audioop`` is importable (stdlib, or the ``audioop-lts``
    backport on Python ≥ 3.13 where it was removed from the stdlib)."""
    return audioop is not None

# Allow importing this add-on's sibling files (commands_de, editor_actions,
# dictation_window) both when loaded by WithEase and when run standalone.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from withease.core import config as app_config
from withease.core.action_manager import Action, action_manager
from withease.core.event_bus import bus
from withease.core.win_keyboard_hook import (
    current_combo_str,
    is_altgr_fake_lctrl,
    shared_keyboard_hook,
    vk_to_combo_str,
)
from withease.gui.widgets.hotkey_edit import HotkeyEdit
from withease.modules.base import BaseModule

try:
    from pynput.keyboard import Controller as KeyController
    from pynput.keyboard import Key as PynputKey
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

_log = logging.getLogger(__name__)

_SAMPLE_RATE = 16_000  # what whisper expects
_CHANNELS = 1

# Dump the C-level stack of every thread to a file if a native library crashes
# the process, so a "the app just closed" report tells us *where* (Vosk vs
# Whisper vs PortAudio) instead of leaving nothing behind.
try:
    _crash_path = os.path.join(app_config.CONFIG_DIR, "dictation_crash.log")
    _crash_file = open(_crash_path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(_crash_file)
except Exception:       # never let diagnostics break startup
    faulthandler.enable()


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
        "group.basics": "Grundeinstellungen",
        "group.recognition": "Spracherkennung",
        "group.output": "Textausgabe",
        "group.vocab_ai": "Wörterbuch & KI",
        "group.vocab": "Wörterbuch",
        "group.ai": "KI (zum Ausklappen anhaken)",
        "group.advanced": "▸ Erweitert",
        "group.advanced.open": "▾ Erweitert",
        "action": "Diktat starten/stoppen",
        "action.command": "Sprachbefehl starten/stoppen",
        "hotkey": "Diktier-Taste",
        "hotkey.command": "Befehls-Taste (optional)",
        "hotkey.command.hint": "Wenn gesetzt: Diese Taste ist nur für Befehle (Cursor, markiere …), die Diktier-Taste nur für Text. So werden Befehl und Diktat sauber getrennt.",
        "mode": "Aufnahmemodus",
        "mode.toggle": "Umschalten (Taste startet/stoppt)",
        "mode.hold": "Halten (sprechen solange gedrückt)",
        "backend": "Erkennung",
        "backend.cloud": "Cloud-Dienst (OpenRouter, OpenAI, Groq …)",
        "backend.local": "Lokal auf diesem PC",
        "backend.local.missing": "nicht installiert",
        "backend.live": "Live-Diktat (Vosk, wortweise + Whisper-Politur)",
        "backend.live.hint": "Wort-für-Wort live wie am Handy; der fertige Satz wird von Whisper nachpoliert (Zeichensetzung, Groß-/Kleinschreibung, dein Wörterbuch). Benötigt Vosk + ein deutsches Vosk-Modell (siehe Anleitung, falls nicht vorhanden).",
        "live_use_vosk": "Vosk-Vorschau (Wort-für-Wort)",
        "live_use_vosk.hint": "Aus (empfohlen): Nur Whisper – der Text erscheint in ~1–2-Sekunden-Schritten, dafür genauer. Ein: Vosk zeigt sofort graue Wörter (Wort-für-Wort), die Whisper danach korrigiert – schneller sichtbar, aber gröber und lädt ein großes Vosk-Modell.",
        "live_pause": "Satzpause (Live)",
        "live_pause.hint": "Wie lange eine Sprechpause dauern muss, damit der Satz als beendet gilt und von Whisper poliert wird. Höher = ganze Sätze auf einmal (saubere Zeichensetzung); niedriger = die Politur erscheint früher. Hilft gegen „wilde“ Zeichensetzung bei Pausen mitten im Satz.",
        "live_pause.auto": "Satzpause automatisch lernen",
        "live_pause.auto.hint": "Passt die Satzpause selbst an: Endet ein diktierter Abschnitt sauber mit einem Satzzeichen, wird die Pause etwas kürzer (reaktionsschneller); wurde ein Satz mittendrin zerschnitten, wird sie länger. Nähert sich mit der Zeit deiner natürlichen Sprechweise an.",
        "live_gate": "Rauschgrenze (Live)",
        "live_gate.hint": "Alles, was leiser ist als dieser Wert (Lüfter, Brummen, Tastatur im Hintergrund), gilt als Stille und wird nicht als Text erkannt. Höher = mehr Ruhe, aber leises Sprechen kann verschluckt werden; niedriger = empfindlicher. 0 schaltet die Grenze aus. Richtwert: 200–400.",
        "live_agc": "Automatische Aussteuerung",
        "live_agc.hint": "Hebt leises/zu weit entferntes Sprechen automatisch auf einen gleichmäßigen Pegel an, bevor es an die Erkennung geht (verstärkt nur Sprache, kein Rauschen; ohne Übersteuern). Verbessert die Genauigkeit von Vosk UND Whisper spürbar, wenn dein Mikro eher leise ist.",
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
        "local.frozen_note": "Die lokale Erkennung ist in der App-Version (.exe) nicht verfügbar – dafür nutze bitte das Cloud-Backend oben. Wer die lokale Erkennung möchte, verwendet die Quellcode-Version von WithEase.",
        "local.install": "Automatisch installieren",
        "local.install.gpu": "GPU-Beschleunigung einrichten",
        "local.ready": "✓ Lokale Erkennung ist installiert und einsatzbereit.",
        "local.ready_gpu": "✓ Lokale Erkennung ist installiert. Du hast eine NVIDIA-Grafikkarte – mit „GPU-Beschleunigung einrichten“ installierst du die CUDA-Komponenten für deutlich schnelleres Diktieren. Danach WithEase neu starten.",
        "local.install.running": "Wird installiert … Das kann einige Minuten dauern. Du kannst das Fenster geöffnet lassen.",
        "local.install.done": "Fertig! Die lokale Spracherkennung ist jetzt installiert und kann verwendet werden.",
        "local.install.failed": "Die Installation hat leider nicht geklappt: {err}\nBitte versuche es erneut oder nutze die Anleitung.",
        "local.howto": "Anleitung anzeigen",
        "local.howto.text": "So installierst du die lokale Spracherkennung von Hand:\n\n1. Öffne die Eingabeaufforderung (Windows-Taste drücken, „cmd“ eintippen, Enter).\n2. Tippe ein:  pip install faster-whisper\n3. Drücke Enter und warte, bis die Installation fertig ist.\n4. Starte WithEase neu.\n\nTipp: Der Knopf „Automatisch installieren“ erledigt genau diese Schritte für dich.",
        "deps.title": "Komponenten für „Diktieren“",
        "deps.missing": "⚠ Für dieses Add-on fehlen noch Komponenten ({pkgs}). Ein Klick auf „Automatisch installieren“ richtet alles für dich ein – keine Vorkenntnisse nötig. Danach WithEase neu starten.",
        "deps.install": "Automatisch installieren",
        "deps.install.running": "Wird installiert … Das kann einige Minuten dauern. Du kannst das Fenster geöffnet lassen.",
        "deps.install.done": "Fertig! Die Komponenten sind installiert. Bitte starte WithEase einmal neu, damit „Diktieren“ vollständig einsatzbereit ist.",
        "deps.install.failed": "Die Installation hat leider nicht geklappt: {err}\nBitte versuche es erneut oder nutze die Anleitung.",
        "deps.howto.text": "So installierst du die fehlenden Komponenten von Hand:\n\n1. Öffne die Eingabeaufforderung (Windows-Taste drücken, „cmd“ eintippen, Enter).\n2. Tippe ein:  pip install {pkgs}\n3. Drücke Enter und warte, bis die Installation fertig ist.\n4. Starte WithEase neu.\n\nTipp: Der Knopf „Automatisch installieren“ erledigt genau diese Schritte für dich.",
        "language": "Sprache",
        "lang.auto": "Automatisch erkennen",
        "glossary": "Eigene Wörter",
        "glossary.hint": "Namen/Fachbegriffe, die Whisper besser erkennen soll (z. B. „Leibig“, „WithEase“, „Diktierfenster“).",
        "glossary.empty": "Noch keine eigenen Wörter.",
        "glossary.count": "{n} Wörter hinterlegt",
        "glossary.add": "Neues Wort eingeben und Enter drücken",
        "glossary.learn": "Aus Text lernen …",
        "vocab": "Wörterbuch",
        "vocab.hint": "Deine eigenen Wörter: Namen/Fachbegriffe (verbessern die Erkennung) und optional, wie sie ausgesprochen werden („wenn ich X sage, schreibe Y“). Links tippen filtert sofort – du siehst gleich, ob ein Wort schon existiert. Unten nach Herkunft filtern (von dir / gelernt / importiert). Mit „Export/Import“ als Textdatei sichern.",
        "cat.all": "Alle Wörter",
        "cat.user": "Von mir angelegt",
        "cat.learned": "Aus Text gelernt",
        "cat.import": "Importiert",
        "cat.spoken": "Mit gesprochener Form",
        "cat.corrected": "Korrigiert (gelernt)",
        "vocab.empty": "Noch keine Einträge.",
        "vocab.spoken": "Wort suchen & hinzufügen …",
        "vocab.written": "geschrieben (z. B. WithEase)",
        "memory": "Fehler-Gedächtnis",
        "memory.empty": "Noch nichts gelernt.",
        "memory.count": "{n} gelernte Korrekturen",
        "memory.reset": "Alles zurücksetzen",
        "memory.hint": "Korrigierte Wörter werden nach der 2. gleichen Korrektur automatisch angewandt.",
        "edit": "Bearbeiten…",
        "add": "Hinzufügen",
        "ai": "KI-Nachbearbeitung",
        "ai.enable": "Diktierten Text von einer KI glätten (optional, aus)",
        "ai.hint": "Korrigiert nur Grammatik/Zeichensetzung, ändert die Bedeutung nicht. Läuft nur bei reinem Diktat (nicht bei Befehlen); Ergebnis erscheint im Diktierfenster.",
        "ai.backend": "KI läuft",
        "ai.local": "Lokal (Ollama, bleibt auf dem PC)",
        "ai.ollama": "Ollama (lokal, bleibt auf dem PC)",
        "ai.lmstudio": "LM Studio (lokal, bleibt auf dem PC)",
        "ai.cloud": "Cloud (Text wird an den Anbieter gesendet)",
        "ai.model": "KI-Modell",
        "ai.model.hint": "Bei Ollama/LM Studio aus der Liste wählbar (↻ lädt die im Programm verfügbaren Modelle); Cloud als Freitext, z. B. „gpt-4o-mini“.",
        "ai.model.refresh": "↻",
        "ai.model.refresh.hint": "Modell-Liste vom laufenden Programm (Ollama/LM Studio) neu laden",
        "ai.model.none": "Keine Modelle gefunden – läuft Ollama bzw. LM Studio und ist ein Modell geladen?",
        "raw": "Nur reine Erkennung (keine Nachbearbeitung)",
        "raw.hint": "Zeigt die reine Ausgabe der Spracherkennung – ohne unsere Nachbearbeitung (keine Satzzeichen-Korrektur, kein Wörterbuch, kein Fehler-Gedächtnis, keine Halluzinations-Filter, keine KI-Bereinigung). Zum Diagnostizieren: So sieht man, ob Fehler von der Erkennung selbst oder von der Nachbearbeitung kommen.",
        "ai.actions": "KI-Aktionen",
        "ai.actions.hint": "Frei belegbare Buttons links im Diktierfenster: Jeder Button schickt deinen Prompt zusammen mit dem Fensterinhalt an die KI (z. B. „mach daraus eine E-Mail“) und ersetzt den Text durch das Ergebnis. Nutzt das oben eingestellte KI-Backend.",
        "output": "Ausgabe",
        "output.window": "Diktierfenster (mit Sprachbefehlen & Korrektur)",
        "output.direct": "Direkt in die aktive Anwendung einfügen",
        "insert": "Text einfügen per",
        "insert.clipboard": "Zwischenablage + Strg+V (schnell)",
        "insert.type": "Tippen (Zeichen für Zeichen)",
        "keep_clipboard": "Erkannten Text zusätzlich in der Zwischenablage behalten",
        "max_seconds": "Max. Aufnahmedauer",
        "max_seconds.off": "Endlos (kein Limit)",
        "preload": "Spracherkennung beim Start vorladen",
        "preload.hint": "Lädt das Whisper-Modell schon beim Start, damit das erste Diktat sofort schnell ist. Erscheint nur, wenn „Mit Windows starten“ (Allgemein) aktiv ist.",
        "training": "Trainingsdaten sammeln (für spätere Stimm-Anpassung)",
        "training.hint": "Speichert Aufnahme + erkannten Text lokal, damit später ein Anlernen an deine Stimme (Fine-Tuning, z. B. auf GPU) möglich wird. Optional, braucht Speicherplatz.",
        "enroll": "Stimm-Training (Vorlesen) …",
        "enroll.hint": "Bekannte Sätze vorlesen → perfekte (Audio + exakter Text)-Paare als Gold-Trainingsdaten.",
        "device": "Mikrofon",
        "device.default": "Standardgerät",
        "test": "Test: 3 Sekunden aufnehmen und erkennen",
        "test.recording": "🎙 Aufnahme läuft (3 s) …",
        "test.result": "Erkannter Text:\n\n{text}",
        "test.error": "Test fehlgeschlagen:\n\n{err}",
        "chip.recording": "Aufnahme … (Esc bricht ab)",
        "chip.transcribing": "Erkenne Text …",
        "chip.dictation": "Diktat",
        "chip.command": "Befehl",
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
        "group.basics": "Basics",
        "group.recognition": "Speech recognition",
        "group.output": "Text output",
        "group.vocab_ai": "Dictionary & AI",
        "group.vocab": "Dictionary",
        "group.ai": "AI (tick to expand)",
        "group.advanced": "▸ Advanced",
        "group.advanced.open": "▾ Advanced",
        "deps_missing": "⚠ This add-on is missing components. To enable it, run in the program folder:  pip install sounddevice requests  (for local recognition also: faster-whisper)",
        "action": "Start/stop dictation",
        "action.command": "Start/stop voice command",
        "hotkey": "Dictation key",
        "hotkey.command": "Command key (optional)",
        "hotkey.command.hint": "When set: this key is for commands only (Cursor, select …) and the dictation key for text only – a clean split between command and dictation.",
        "mode": "Recording mode",
        "mode.toggle": "Toggle (key starts/stops)",
        "mode.hold": "Hold (speak while pressed)",
        "backend": "Recognition",
        "backend.cloud": "Cloud service (OpenRouter, OpenAI, Groq …)",
        "backend.local": "Locally on this PC",
        "backend.local.missing": "not installed",
        "backend.live": "Live dictation (Vosk, word-by-word + Whisper polish)",
        "backend.live.hint": "Word-by-word live like on a phone; the finished sentence is polished by Whisper (punctuation, casing, your dictionary). Needs Vosk + a German Vosk model.",
        "live_use_vosk": "Vosk preview (word-by-word)",
        "live_use_vosk.hint": "Off (recommended): Whisper only – text appears in ~1–2 s steps but is more accurate. On: Vosk shows instant grey words (word-by-word) that Whisper then corrects – faster to appear but rougher, and loads a large Vosk model.",
        "live_pause": "Sentence pause (live)",
        "live_pause.hint": "How long a speaking pause must last before the sentence counts as finished and is polished by Whisper. Higher = whole sentences at once (clean punctuation); lower = the polish appears sooner. Helps against 'wild' punctuation when you pause mid-sentence.",
        "live_pause.auto": "Learn sentence pause automatically",
        "live_pause.auto.hint": "Adapts the sentence pause on its own: if a dictated stretch ends cleanly on a punctuation mark, the pause gets a little shorter (more responsive); if a sentence got cut in half, it gets longer. Converges on your natural speaking rhythm over time.",
        "live_gate": "Noise gate (live)",
        "live_gate.hint": "Anything quieter than this value (fan, hum, background typing) is treated as silence and never becomes text. Higher = more quiet needed, but soft speech may be dropped; lower = more sensitive. 0 disables it. Typical: 200–400.",
        "live_agc": "Automatic gain control",
        "live_agc.hint": "Automatically raises quiet/distant speech to a steady level before recognition (boosts speech only, not noise; never clips). Noticeably improves accuracy for both Vosk and Whisper when your microphone is on the quiet side.",
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
        "local.frozen_note": "Local recognition is not available in the packaged app (.exe) – please use the cloud backend above instead. If you want local recognition, use the source-code version of WithEase.",
        "local.install": "Install automatically",
        "local.install.gpu": "Set up GPU acceleration",
        "local.ready": "✓ Local recognition is installed and ready to use.",
        "local.ready_gpu": "✓ Local recognition is installed. You have an NVIDIA GPU – use “Set up GPU acceleration” to install the CUDA components for much faster dictation, then restart WithEase.",
        "local.install.running": "Installing … This may take a few minutes. You can keep this window open.",
        "local.install.done": "Done! Local speech recognition is now installed and ready to use.",
        "local.install.failed": "The installation did not work: {err}\nPlease try again or use the instructions.",
        "local.howto": "Show instructions",
        "local.howto.text": "How to install local speech recognition manually:\n\n1. Open the command prompt (press the Windows key, type \"cmd\", press Enter).\n2. Type:  pip install faster-whisper\n3. Press Enter and wait until the installation finishes.\n4. Restart WithEase.\n\nTip: the \"Install automatically\" button does exactly these steps for you.",
        "deps.title": "Components for \"Dictation\"",
        "deps.missing": "⚠ This add-on is still missing components ({pkgs}). One click on \"Install automatically\" sets everything up for you – no technical knowledge needed. Restart WithEase afterwards.",
        "deps.install": "Install automatically",
        "deps.install.running": "Installing … This may take a few minutes. You can keep this window open.",
        "deps.install.done": "Done! The components are installed. Please restart WithEase once so \"Dictation\" is fully ready to use.",
        "deps.install.failed": "The installation did not work: {err}\nPlease try again or use the instructions.",
        "deps.howto.text": "How to install the missing components manually:\n\n1. Open the command prompt (press the Windows key, type \"cmd\", press Enter).\n2. Type:  pip install {pkgs}\n3. Press Enter and wait until the installation finishes.\n4. Restart WithEase.\n\nTip: the \"Install automatically\" button does exactly these steps for you.",
        "language": "Language",
        "lang.auto": "Detect automatically",
        "glossary": "Custom words",
        "glossary.hint": "Names/terms Whisper should recognise better (e.g. \"Leibig\", \"WithEase\").",
        "glossary.empty": "No custom words yet.",
        "glossary.count": "{n} words saved",
        "glossary.add": "Type a new word and press Enter",
        "glossary.learn": "Learn from text …",
        "vocab": "Dictionary",
        "vocab.hint": "Your own words: names/terms (improve recognition) and optionally how they're pronounced ('when I say X, write Y'). Typing on the left filters instantly, so you see at once whether a word exists. Filter by origin below (yours / learned / imported). Use 'Export/Import' to back it up as a text file.",
        "cat.all": "All words",
        "cat.user": "Added by me",
        "cat.learned": "Learned from text",
        "cat.import": "Imported",
        "cat.spoken": "With spoken form",
        "cat.corrected": "Corrected (learned)",
        "vocab.empty": "No entries yet.",
        "vocab.spoken": "search & add a word …",
        "vocab.written": "written (e.g. WithEase)",
        "memory": "Error memory",
        "memory.empty": "Nothing learned yet.",
        "memory.count": "{n} learned corrections",
        "memory.reset": "Reset all",
        "memory.hint": "A corrected word is applied automatically after the 2nd identical correction.",
        "edit": "Edit…",
        "add": "Add",
        "ai": "AI cleanup",
        "ai.enable": "Smooth dictated text with an AI (optional, off)",
        "ai.hint": "Fixes only grammar/punctuation, never the meaning. Runs on plain dictation (not commands); result appears in the dictation window.",
        "ai.backend": "AI runs",
        "ai.local": "Local (Ollama, stays on this PC)",
        "ai.ollama": "Ollama (local, stays on this PC)",
        "ai.lmstudio": "LM Studio (local, stays on this PC)",
        "ai.cloud": "Cloud (text is sent to the provider)",
        "ai.model": "AI model",
        "ai.model.hint": "For Ollama/LM Studio pick from the list (↻ loads the models available in the program); cloud is free-text, e.g. \"gpt-4o-mini\".",
        "ai.model.refresh": "↻",
        "ai.model.refresh.hint": "Reload the model list from the running program (Ollama/LM Studio)",
        "ai.model.none": "No models found – is Ollama or LM Studio running with a model loaded?",
        "raw": "Raw recognition only (no post-processing)",
        "raw.hint": "Shows the recogniser's plain output – without any of our post-processing (no punctuation fixes, no dictionary, no error memory, no hallucination filter, no AI cleanup). For diagnosing whether errors come from recognition itself or from post-processing.",
        "ai.actions": "AI actions",
        "ai.actions.hint": "Custom buttons on the left of the dictation window: each sends your prompt together with the window text to the AI (e.g. \"turn this into an email\") and replaces the text with the result. Uses the AI backend set above.",
        "output": "Output",
        "output.window": "Dictation window (with voice commands & correction)",
        "output.direct": "Insert directly into the active application",
        "insert": "Insert text via",
        "insert.clipboard": "Clipboard + Ctrl+V (fast)",
        "insert.type": "Typing (character by character)",
        "keep_clipboard": "Also keep the recognised text in the clipboard",
        "max_seconds": "Max. recording length",
        "max_seconds.off": "Endless (no limit)",
        "preload": "Preload speech recognition at start",
        "preload.hint": "Loads the Whisper model at start so the first dictation is fast right away. Only shown when 'Start with Windows' (General) is on.",
        "training": "Collect training data (for later voice adaptation)",
        "training.hint": "Saves the recording + recognised text locally so a later adaptation to your voice (fine-tuning, e.g. on a GPU) becomes possible. Optional, uses disk space.",
        "enroll": "Voice training (read aloud) …",
        "enroll.hint": "Read known sentences aloud → perfect (audio + exact text) pairs as gold training data.",
        "device": "Microphone",
        "device.default": "Default device",
        "test": "Test: record 3 seconds and transcribe",
        "test.recording": "🎙 Recording (3 s) …",
        "test.result": "Recognised text:\n\n{text}",
        "test.error": "Test failed:\n\n{err}",
        "chip.recording": "Recording … (Esc cancels)",
        "chip.transcribing": "Transcribing …",
        "chip.dictation": "Dictation",
        "chip.command": "Command",
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
# module).  Uses the high-contrast text colour (palette(mid) was too dark to
# read on the dark theme); the smaller font keeps hints visually secondary.
def _hint_style() -> str:
    return "color: palette(windowText); font-size: smaller;"


def _warn_style() -> str:
    return "color: #D9534F; font-size: smaller;"   # readable on light + dark


def _title_style() -> str:
    return "font-weight: bold; font-size: larger;"


class _Collapsible(QWidget):
    """A titled section that shows/hides its content on click – used to tuck
    rarely-needed expert options away so the page stays calm by default."""

    def __init__(self, title_closed: str, title_open: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title_closed = title_closed
        self._title_open = title_open
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self._btn = QToolButton()
        self._btn.setText(title_closed)
        self._btn.setCheckable(True)
        self._btn.setChecked(False)
        self._btn.setAutoRaise(True)
        self._btn.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 2px 0; }")
        self._btn.toggled.connect(self._on_toggled)
        v.addWidget(self._btn)
        self._content = QWidget()
        self._content.setVisible(False)
        v.addWidget(self._content)

    def _on_toggled(self, on: bool) -> None:
        self._btn.setText(self._title_open if on else self._title_closed)
        self._content.setVisible(on)

    def content(self) -> QWidget:
        return self._content


def _sync_module_checkbox(widget: QWidget, module: "DictationModule",
                          checkbox: QCheckBox,
                          update_enabled_state: Any) -> None:
    """Keep the page's enable-checkbox in sync when the module is toggled
    elsewhere (emergency stop, tray, profile switch).  Self-contained copy of
    the core helper so the module needs nothing from withease.gui.settings."""

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
    """Translate the stored device setting into a sounddevice index.

    Returns None (= system default) when the setting is empty *or* points at a
    device that isn't a usable microphone.  Stored indices go stale whenever
    audio devices are added/removed and can end up on an output device (0 input
    channels) – opening that as a mic fails with "Invalid number of channels".
    """
    if value in (None, "", "default"):
        return None
    idx: int | None = None
    if isinstance(value, int):
        idx = value
    else:
        for i, name in list_input_devices():
            if name == value:
                idx = i
                break
    if idx is not None:
        try:
            import sounddevice as sd
            if int(sd.query_devices(idx).get("max_input_channels", 0)) > 0:
                return idx
        except Exception:
            pass
    return None    # invalid / stale / not an input device → system default


def open_input_stream(sd: Any, device: int | None,
                      callback: Any) -> tuple[Any, int, int]:
    """Open a RawInputStream, falling back to the device's native format and,
    if the chosen device can't be opened at all, to the system default."""
    devices_to_try = [device] + ([None] if device is not None else [])
    last_exc: Exception | None = None
    for dev in devices_to_try:
        try:
            info = sd.query_devices(dev, "input")
        except Exception:
            info = {"default_samplerate": 48_000, "max_input_channels": 1}
        native_rate = int(info.get("default_samplerate") or 48_000)
        max_ch = max(1, int(info.get("max_input_channels") or 1))

        attempts = [(_SAMPLE_RATE, 1)]
        if (native_rate, 1) not in attempts:
            attempts.append((native_rate, 1))
        attempts.append((native_rate, min(2, max_ch)))

        for rate, channels in attempts:
            try:
                stream = sd.RawInputStream(
                    samplerate=rate, channels=channels, dtype="int16",
                    callback=callback, device=dev)
                stream.start()
                return stream, rate, channels
            except Exception as exc:
                last_exc = exc
    raise last_exc or RuntimeError("no usable input format")


def audio_available() -> bool:
    """True if the optional recording/cloud dependencies are all present.

    Needs ``requests`` (cloud API), ``sounddevice`` (mic capture) and
    ``audioop`` (live noise-gate + resampling – stdlib until 3.12, the
    ``audioop-lts`` backport from 3.13 on)."""
    try:
        import requests  # noqa: F401
        import sounddevice  # noqa: F401
    except ImportError:
        return False
    return audioop_available()


def missing_audio_packages() -> list[str]:
    """pip package names for the recording/cloud dependencies that are not yet
    importable on this Python, in install order.  Empty when nothing is missing.

    ``audioop-lts`` is only added on Python ≥ 3.13, where ``audioop`` was
    removed from the standard library; on 3.12 and earlier audioop ships with
    Python and needs no package."""
    missing: list[str] = []
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        missing.append("sounddevice")
    try:
        import requests  # noqa: F401
    except ImportError:
        missing.append("requests")
    if sys.version_info >= (3, 13) and not audioop_available():
        missing.append("audioop-lts")
    return missing


def local_backend_available() -> bool:
    # find_spec locates faster-whisper WITHOUT importing it (which would load
    # CTranslate2/PyAV into this process – exactly what we avoid).
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def _has_nvidia_gpu() -> bool:
    """True if an NVIDIA GPU with a working driver is present.

    Cheap and dependency-free: the CUDA driver installs ``nvcuda.dll`` into
    System32 and puts ``nvidia-smi`` on PATH. We avoid importing torch/
    ctranslate2 just to detect the GPU."""
    if sys.platform == "win32":
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        if os.path.exists(os.path.join(sysroot, "System32", "nvcuda.dll")):
            return True
    import shutil
    return shutil.which("nvidia-smi") is not None


class WhisperProc:
    """Runs the faster-whisper worker in a *separate process* and talks to it
    over line-based JSON.  Keeping Whisper's native libraries out of the main
    (Vosk) process removes the native-runtime conflict that crashed the app."""

    def __init__(self) -> None:
        self._proc: Any = None
        self._lock = threading.Lock()      # one request at a time
        self._start_args: tuple | None = None

    def configure(self, model: str, threads: int) -> None:
        """Remember how to (re)start the worker without starting it now, so a
        later transcribe() can lazily spin it up under its own lock."""
        self._start_args = (model, threads)

    def start(self, model: str, threads: int) -> bool:
        import subprocess
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "whisper_worker.py")
        self._start_args = (model, threads)
        try:
            self._proc = subprocess.Popen(
                [sys.executable, worker, model, str(threads), "auto"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            _log.exception("could not start whisper worker")
            self._proc = None
            return False
        msg = self._read_json()            # wait for the ready handshake
        if not msg.get("ready"):
            _log.error("whisper worker not ready: %s", msg.get("error"))
            self.stop()
            return False
        return True

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _read_json(self) -> dict:
        """Read stdout lines until one parses as JSON (skip any library noise)."""
        while True:
            line = self._proc.stdout.readline()
            if not line:
                return {}
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except Exception:
                continue

    def transcribe(self, wav_bytes: bytes, *, language: str | None = None,
                   hotwords: str | None = None) -> tuple[str, list]:
        import tempfile
        with self._lock:
            if not self.alive():
                if not (self._start_args and self.start(*self._start_args)):
                    return "", []
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            f.write(wav_bytes)
            f.close()
            try:
                req = {"wav": f.name, "language": language,
                       "initial_prompt": None, "hotwords": hotwords,
                       "live": True}
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                msg = self._read_json()    # blocks until the worker responds
            except Exception:
                _log.exception("whisper worker request failed")
                msg = {}
            finally:
                try:
                    os.unlink(f.name)
                except OSError:
                    pass
            return msg.get("text", ""), msg.get("low", [])

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.write('{"cmd": "quit"}\n')
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


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
        # For recording/transcribing, ``_detail`` carries the mode ("Diktat" /
        # "Befehl") so the chip shows which key is being used.
        prefix = f"{self._detail} · " if self._detail else ""
        if self._state == "recording":
            return f"🎙 {prefix}{_t('chip.recording')}"
        if self._state == "transcribing":
            return f"⏳ {prefix}{_t('chip.transcribing')}"
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


class _AiModelsBridge(QObject):
    loaded = Signal(list)          # model names fetched from the local provider


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
        self._deps_bridge = _InstallBridge()
        self._deps_bridge.finished.connect(self._on_deps_install_finished)
        self._ai_models_bridge = _AiModelsBridge()
        self._ai_models_bridge.loaded.connect(self._on_ai_models_loaded)
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
        layout.setSpacing(14)

        # -- Module toggle + privacy note ------------------------------
        self._enabled_cb = QCheckBox(_t("enabled"))
        self._enabled_cb.setChecked(self._module.enabled)
        self._enabled_cb.setStyleSheet(_title_style())
        self._enabled_cb.toggled.connect(self._on_module_toggled)
        layout.addWidget(self._enabled_cb)

        desc = QLabel(_t("description.long"))
        desc.setStyleSheet(_hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._deps_box = self._build_deps_box()
        layout.addWidget(self._deps_box)
        self._deps_box.setVisible(not audio_available())

        def _group(title: str) -> QFormLayout:
            box = QGroupBox(title)
            f = QFormLayout(box)
            f.setSpacing(8)
            f.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            layout.addWidget(box)
            return f

        # -- (1) Grundeinstellungen ------------------------------------
        basics = _group(_t("group.basics"))

        self._hotkey = HotkeyEdit(self._settings.get("hotkey", ""),
                                  action_id="dictation.toggle")
        self._hotkey.key_changed.connect(lambda k: self._save("hotkey", k))
        basics.addRow(_t("hotkey"), self._hotkey)

        self._mode = QComboBox()
        self._mode.addItem(_t("mode.toggle"), "toggle")
        self._mode.addItem(_t("mode.hold"), "hold")
        if self._settings.get("mode", "toggle") == "hold":
            self._mode.setCurrentIndex(1)
        self._mode.currentIndexChanged.connect(
            lambda i: self._save("mode", self._mode.itemData(i)))
        basics.addRow(_t("mode"), self._mode)

        self._lang = QComboBox()
        for code in LANGUAGES:
            label = _t("lang.auto") if code == "auto" else code
            self._lang.addItem(label, code)
        saved_lang = self._settings.get("language", "auto")
        if saved_lang in LANGUAGES:
            self._lang.setCurrentIndex(LANGUAGES.index(saved_lang))
        self._lang.currentIndexChanged.connect(
            lambda i: self._save("language", self._lang.itemData(i)))
        basics.addRow(_t("language"), self._lang)

        # -- (2) Spracherkennung ---------------------------------------
        rec = _group(_t("group.recognition"))
        self._form_rec = rec

        self._backend = QComboBox()
        self._backend.addItem(_t("backend.cloud"), "cloud")
        local_label = _t("backend.local")
        if not local_backend_available():
            local_label += f" ({_t('backend.local.missing')})"
        self._backend.addItem(local_label, "local")
        saved_backend = self._settings.get("backend", "cloud")
        if saved_backend == "live":          # retire an old "live" selection
            saved_backend = "local"
        idx = self._backend.findData(saved_backend)
        if idx >= 0:
            self._backend.setCurrentIndex(idx)
        self._backend.currentIndexChanged.connect(self._on_backend_changed)
        rec.addRow(_t("backend"), self._backend)

        # Microphone – applies to both cloud and local backends, so it lives
        # here in the Speech-recognition group (always visible), not tucked away
        # in the advanced section.
        self._device = QComboBox()
        self._device.addItem(_t("device.default"), "default")
        try:
            for dev_idx, name in list_input_devices():
                self._device.addItem(name, dev_idx)
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
        rec.addRow(_t("device"), self._device)

        # Cloud fields
        self._provider = QComboBox()
        for pid in PROVIDERS:
            self._provider.addItem(_t(f"provider.{pid}"), pid)
        saved_provider = self._settings.get("provider", "openrouter")
        ids = list(PROVIDERS.keys())
        if saved_provider in ids:
            self._provider.setCurrentIndex(ids.index(saved_provider))
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        rec.addRow(_t("provider"), self._provider)

        self._base_url = QLineEdit(self._settings.get("base_url", ""))
        self._base_url.setPlaceholderText("https://.../v1")
        self._base_url.setMinimumWidth(280)
        self._base_url.editingFinished.connect(
            lambda: self._save("base_url", self._base_url.text().strip()))
        rec.addRow(_t("base_url"), self._base_url)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setMinimumWidth(280)
        self._api_key.setText(self._module.get_api_key(saved_provider))
        self._api_key.editingFinished.connect(self._on_api_key_changed)
        rec.addRow(_t("api_key"), self._api_key)

        self._key_hint = QLabel(_t("api_key.hint"))
        self._key_hint.setStyleSheet(_hint_style())
        self._key_hint.setWordWrap(True)
        rec.addRow("", self._key_hint)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._fill_models(saved_provider)
        saved_model = self._settings.get("model", "")
        if saved_model:
            self._model.setEditText(saved_model)
        self._model.currentTextChanged.connect(
            lambda t: self._save("model", t.strip()))
        rec.addRow(_t("model"), self._model)

        # Local fields
        self._local_model = QComboBox()
        for m in LOCAL_MODELS:
            self._local_model.addItem(m, m)
        saved_local = self._settings.get("local_model", "base")
        if saved_local in LOCAL_MODELS:
            self._local_model.setCurrentIndex(LOCAL_MODELS.index(saved_local))
        self._local_model.currentIndexChanged.connect(
            lambda i: self._save("local_model", self._local_model.itemData(i)))
        rec.addRow(_t("local_model"), self._local_model)

        self._local_hint = QLabel(_t("local.hint"))
        self._local_hint.setStyleSheet(_hint_style())
        self._local_hint.setWordWrap(True)
        rec.addRow("", self._local_hint)

        import sys as _sys
        self._frozen = bool(getattr(_sys, "frozen", False))
        self._install_box = QWidget()
        install_layout = QVBoxLayout(self._install_box)
        install_layout.setContentsMargins(0, 0, 0, 0)
        install_layout.setSpacing(6)
        self._install_note = QLabel()
        self._install_note.setWordWrap(True)
        install_layout.addWidget(self._install_note)
        install_btns = QHBoxLayout()
        self._install_btn = QPushButton(_t("local.install"))
        self._install_btn.clicked.connect(self._on_install_local)
        self._install_btn.setVisible(not self._frozen)   # pip N/A in the .exe
        install_btns.addWidget(self._install_btn)
        howto_btn = QPushButton(_t("local.howto"))
        howto_btn.clicked.connect(self._on_show_howto)
        install_btns.addWidget(howto_btn)
        install_btns.addStretch()
        install_layout.addLayout(install_btns)
        self._install_status = QLabel("")
        self._install_status.setWordWrap(True)
        install_layout.addWidget(self._install_status)
        rec.addRow("", self._install_box)
        self._update_install_note()

        self._test_btn = QPushButton(_t("test"))
        self._test_btn.clicked.connect(self._on_test)
        rec.addRow("", self._test_btn)

        # -- (3) Textausgabe -------------------------------------------
        out = _group(_t("group.output"))

        self._output_mode = QComboBox()
        self._output_mode.addItem(_t("output.window"), "window")
        self._output_mode.addItem(_t("output.direct"), "direct")
        if self._settings.get("output_mode", "window") == "direct":
            self._output_mode.setCurrentIndex(1)
        self._output_mode.currentIndexChanged.connect(
            lambda i: self._save("output_mode", self._output_mode.itemData(i)))
        out.addRow(_t("output"), self._output_mode)

        self._insert = QComboBox()
        self._insert.addItem(_t("insert.clipboard"), "clipboard")
        self._insert.addItem(_t("insert.type"), "type")
        if self._settings.get("insert_method", "clipboard") == "type":
            self._insert.setCurrentIndex(1)
        self._insert.currentIndexChanged.connect(
            lambda i: self._save("insert_method", self._insert.itemData(i)))
        out.addRow(_t("insert"), self._insert)

        self._keep_clipboard = QCheckBox(_t("keep_clipboard"))
        self._keep_clipboard.setChecked(
            bool(self._settings.get("keep_in_clipboard", False)))
        self._keep_clipboard.toggled.connect(
            lambda v: self._save("keep_in_clipboard", v))
        out.addRow("", self._keep_clipboard)

        # -- (4) Woerterbuch -------------------------------------------
        vocab = _group(_t("group.vocab"))
        dict_row = QHBoxLayout()
        self._dict_summary = QLabel(self._dict_summary_text())
        self._dict_summary.setStyleSheet(_hint_style())
        self._dict_summary.setToolTip(_t("vocab.hint"))
        dict_row.addWidget(self._dict_summary, 1)
        dict_learn = QPushButton(_t("glossary.learn"))
        dict_learn.clicked.connect(self._open_learn_text)
        dict_row.addWidget(dict_learn)
        dict_edit = QPushButton(_t("edit"))
        dict_edit.clicked.connect(self._open_dictionary)
        dict_row.addWidget(dict_edit)
        vocab.addRow(_t("vocab"), dict_row)

        # -- (5) KI (checkable – folded away while unused) -------------
        ki_box = QGroupBox(_t("group.ai"))
        ki_box.setCheckable(True)
        ki_box.setChecked(bool(self._settings.get("ai_group_open", False)))
        ki_outer = QVBoxLayout(ki_box)
        ki_outer.setContentsMargins(8, 4, 8, 8)
        ki_content = QWidget()
        ai = QFormLayout(ki_content)
        ai.setSpacing(8)
        ai.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form_ai = ai
        ki_outer.addWidget(ki_content)
        ki_content.setVisible(ki_box.isChecked())
        ki_box.toggled.connect(ki_content.setVisible)
        ki_box.toggled.connect(lambda v: self._save("ai_group_open", bool(v)))
        layout.addWidget(ki_box)

        aiact_row = QHBoxLayout()
        _aiact_hint = QLabel(_t("ai.actions.hint"))
        _aiact_hint.setWordWrap(True)
        _aiact_hint.setStyleSheet(_hint_style())
        aiact_row.addWidget(_aiact_hint, 1)
        aiact_btn = QPushButton(_t("edit"))
        aiact_btn.clicked.connect(self._open_ai_actions)
        aiact_row.addWidget(aiact_btn)
        ai.addRow(_t("ai.actions"), aiact_row)

        self._ai_enable = QCheckBox(_t("ai.enable"))
        self._ai_enable.setChecked(bool(self._settings.get("ai_cleanup", False)))
        self._ai_enable.setToolTip(_t("ai.hint"))
        self._ai_enable.toggled.connect(lambda v: self._save("ai_cleanup", v))
        self._ai_enable.toggled.connect(lambda _v: self._update_ai_rows())
        ai.addRow(_t("ai"), self._ai_enable)
        _ai_hint = QLabel(_t("ai.hint"))
        _ai_hint.setWordWrap(True)
        _ai_hint.setStyleSheet(_hint_style())
        ai.addRow("", _ai_hint)

        self._ai_backend = QComboBox()
        self._ai_backend.addItem(_t("ai.ollama"), "ollama")
        self._ai_backend.addItem(_t("ai.lmstudio"), "lmstudio")
        self._ai_backend.addItem(_t("ai.cloud"), "cloud")
        saved_ai_backend = self._settings.get("ai_backend", "ollama")
        if saved_ai_backend == "local":          # legacy value → Ollama
            saved_ai_backend = "ollama"
        bidx = self._ai_backend.findData(saved_ai_backend)
        if bidx >= 0:
            self._ai_backend.setCurrentIndex(bidx)
        self._ai_backend.currentIndexChanged.connect(self._on_ai_backend_changed)
        ai.addRow(_t("ai.backend"), self._ai_backend)
        self._ai_backend_label = ai.labelForField(self._ai_backend)

        # Model as an editable dropdown, populated from the running local
        # provider (Ollama / LM Studio); still free-text for the cloud backend.
        self._ai_model = QComboBox()
        self._ai_model.setEditable(True)
        self._ai_model.setMinimumWidth(220)
        self._ai_model.setToolTip(_t("ai.model.hint"))
        saved_ai_model = self._settings.get("ai_model", "")
        if saved_ai_model:
            self._ai_model.setEditText(saved_ai_model)
        self._ai_model.currentTextChanged.connect(
            lambda t: self._save("ai_model", t.strip()))
        self._ai_model_refresh = QPushButton(_t("ai.model.refresh"))
        self._ai_model_refresh.setFixedWidth(36)
        self._ai_model_refresh.setToolTip(_t("ai.model.refresh.hint"))
        self._ai_model_refresh.clicked.connect(self._refresh_ai_models)
        self._ai_model.setMinimumWidth(240)
        self._ai_model_container = QWidget()
        model_row = QHBoxLayout(self._ai_model_container)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.addWidget(self._ai_model)
        model_row.addWidget(self._ai_model_refresh)
        model_row.addStretch(1)          # keep dropdown+refresh together, left
        ai.addRow(_t("ai.model"), self._ai_model_container)
        self._ai_model_label = ai.labelForField(self._ai_model_container)

        # -- (5) Erweitert (collapsed by default) ----------------------
        adv_section = _Collapsible(
            _t("group.advanced"), _t("group.advanced.open"))
        adv = QFormLayout(adv_section.content())
        adv.setSpacing(8)
        adv.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form_adv = adv
        layout.addWidget(adv_section)

        self._command_hotkey = HotkeyEdit(
            self._settings.get("command_hotkey", ""),
            action_id="dictation.command")
        self._command_hotkey.key_changed.connect(
            lambda k: self._save("command_hotkey", k))
        self._command_hotkey.setToolTip(_t("hotkey.command.hint"))
        adv.addRow(_t("hotkey.command"), self._command_hotkey)
        _cmd_hint = QLabel(_t("hotkey.command.hint"))
        _cmd_hint.setWordWrap(True)
        _cmd_hint.setStyleSheet(_hint_style())
        adv.addRow("", _cmd_hint)

        self._max_seconds = QSpinBox()
        self._max_seconds.setRange(0, 3600)     # 0 = endless (no auto-stop)
        self._max_seconds.setSuffix(" s")
        self._max_seconds.setSpecialValueText(_t("max_seconds.off"))
        self._max_seconds.setValue(int(self._settings.get("max_seconds", 0)))
        self._max_seconds.valueChanged.connect(
            lambda v: self._save("max_seconds", v))
        adv.addRow(_t("max_seconds"), self._max_seconds)

        self._preload_cb = QCheckBox(_t("preload"))
        self._preload_cb.setChecked(
            bool(self._settings.get("preload_model", False)))
        self._preload_cb.setToolTip(_t("preload.hint"))
        self._preload_cb.toggled.connect(
            lambda v: self._save("preload_model", v))
        adv.addRow("", self._preload_cb)
        self._update_preload_row()

        self._raw_cb = QCheckBox(_t("raw"))
        self._raw_cb.setChecked(
            bool(self._settings.get("raw_recognition", False)))
        self._raw_cb.setToolTip(_t("raw.hint"))
        self._raw_cb.toggled.connect(
            lambda v: self._save("raw_recognition", v))
        adv.addRow("", self._raw_cb)
        _raw_hint = QLabel(_t("raw.hint"))
        _raw_hint.setWordWrap(True)
        _raw_hint.setStyleSheet(_hint_style())
        adv.addRow("", _raw_hint)

        layout.addStretch()
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._on_backend_changed(self._backend.currentIndex())
        self._update_ai_rows()
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _update_preload_row(self) -> None:
        """The preload option is only offered when the app autostarts (the
        general 'start with Windows' switch under Settings → General)."""
        try:
            from withease.core import autostart
            on = autostart.is_enabled()
        except Exception:
            on = False
        self._preload_cb.setVisible(on)
        if not on and self._preload_cb.isChecked():
            self._preload_cb.setChecked(False)   # also clears the setting

    def _update_ai_rows(self) -> None:
        """Show „KI läuft"/„KI-Modell" when AI cleanup is on OR the user has
        configured KI-Aktionen (both need a backend + model)."""
        visible = self._ai_enable.isChecked() or bool(self._module.ai_actions())
        for w in (self._ai_backend, getattr(self, "_ai_backend_label", None),
                  self._ai_model_container, getattr(self, "_ai_model_label", None)):
            if w is not None:
                w.setVisible(visible)
        if visible:
            self._refresh_ai_models()

    def _on_ai_backend_changed(self, index: int) -> None:
        self._save("ai_backend", self._ai_backend.itemData(index))
        self._refresh_ai_models()

    def _refresh_ai_models(self) -> None:
        """Reload the model dropdown from the running local provider (async).

        Cloud keeps free-text (no list to fetch), so the refresh button is
        hidden there."""
        is_cloud = self._ai_backend.currentData() == "cloud"
        self._ai_model_refresh.setVisible(not is_cloud)
        if is_cloud:
            return
        self._ai_model_refresh.setEnabled(False)

        def run() -> None:
            try:
                models = self._module.list_ai_models()
            except Exception:
                models = []
            self._ai_models_bridge.loaded.emit(models)

        threading.Thread(target=run, daemon=True, name="ai-models").start()

    def _on_ai_models_loaded(self, models: list) -> None:
        self._ai_model_refresh.setEnabled(True)
        current = self._ai_model.currentText()
        self._ai_model.blockSignals(True)   # repopulating must not clear the setting
        self._ai_model.clear()
        for name in models:
            self._ai_model.addItem(name)
        self._ai_model.setEditText(current)  # keep the user's choice/typed value
        self._ai_model.blockSignals(False)
        self._ai_model.setToolTip(
            _t("ai.model.hint") if models else _t("ai.model.none"))

    def _open_ai_actions(self) -> None:
        from settings_dialogs import AiActionsDialog
        dlg = AiActionsDialog(self._module.ai_actions_raw(),
                              on_save=self._module.save_ai_actions, parent=self)
        dlg.exec()
        self._update_ai_rows()

    def _dict_summary_text(self) -> str:
        n = self._module.dictionary_count()
        return _t("glossary.empty") if n == 0 else _t("glossary.count", n=str(n))

    def _open_dictionary(self) -> None:
        from settings_dialogs import DictionaryDialog
        m = self._module
        cats = [("all", _t("cat.all")), ("user", _t("cat.user")),
                ("learned", _t("cat.learned")), ("import", _t("cat.import")),
                ("spoken", _t("cat.spoken")), ("corrected", _t("cat.corrected"))]
        dlg = DictionaryDialog(
            rows_provider=m.dictionary_rows,
            on_add=lambda w, s: m.add_dictionary_entry(w, s, "user"),
            on_edit=m.dictionary_edit,
            on_remove=m.dictionary_remove,
            categories=cats,
            on_export=m.export_dictionary,
            on_import=m.import_dictionary,
            on_learn=self._open_learn_text,
            on_clear_category=m.clear_dictionary_category,
            title=_t("vocab"), intro=_t("vocab.hint"), parent=self)
        dlg.exec()
        self._dict_summary.setText(self._dict_summary_text())

    def _open_enrollment(self) -> None:
        from enrollment import PROMPTS
        from settings_dialogs import EnrollmentDialog
        dlg = EnrollmentDialog(
            PROMPTS, on_start=self._module.enroll_start,
            on_stop=self._module.enroll_stop,
            on_discard=self._module.enroll_discard, parent=self)
        dlg.exec()

    def _open_learn_text(self) -> None:
        from settings_dialogs import LearnFromTextDialog

        def _add(terms: list) -> None:
            for term in terms:
                self._module.add_learned_word(term)     # tagged „gelernt“
        dlg = LearnFromTextDialog(on_accept=_add, parent=self)
        dlg.exec()
        self._dict_summary.setText(self._dict_summary_text())

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
            self._form_rec.setRowVisible(widget, cloud)
        self._form_rec.setRowVisible(self._local_model, not cloud)
        self._form_rec.setRowVisible(self._local_hint, not cloud)
        # The setup box stays visible for the whole local backend – so the
        # "Automatisch installieren" button is always reachable (also to set up
        # GPU acceleration), not only when faster-whisper is still missing.
        self._form_rec.setRowVisible(self._install_box, not cloud)
        if not cloud:
            self._update_install_note()
        self._update_cloud_rows()

    def _update_cloud_rows(self) -> None:
        cloud = self._backend.currentData() == "cloud"
        custom = self._provider.currentData() == "custom"
        self._form_rec.setRowVisible(self._base_url, cloud and custom)

    # ------------------------------------------------------------------
    # Local backend installation (one click, no command line)
    # ------------------------------------------------------------------

    def _update_install_note(self) -> None:
        """Note + button label reflecting what setup is still useful."""
        if self._frozen:
            self._install_note.setStyleSheet(_warn_style())
            self._install_note.setText(_t("local.frozen_note"))
            return
        gpu = _has_nvidia_gpu()
        if local_backend_available():
            self._install_note.setStyleSheet(_hint_style())
            self._install_note.setText(
                _t("local.ready_gpu") if gpu else _t("local.ready"))
            self._install_btn.setText(
                _t("local.install.gpu") if gpu else _t("local.install"))
        else:
            self._install_note.setStyleSheet(_warn_style())
            self._install_note.setText(_t("local.not_installed"))
            self._install_btn.setText(_t("local.install"))

    def _on_install_local(self) -> None:
        self._install_btn.setEnabled(False)
        self._install_status.setStyleSheet(_hint_style())
        self._install_status.setText(_t("local.install.running"))

        import subprocess
        import sys

        def run() -> None:
            try:
                pkgs = ["faster-whisper"]
                if _has_nvidia_gpu():
                    # CUDA cuBLAS + runtime for GPU inference (CTranslate2
                    # bundles cuDNN but not these). pip skips what's satisfied.
                    pkgs += ["nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12"]
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", *pkgs],
                    capture_output=True, text=True, timeout=1800,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 0:
                    tail = (result.stderr or result.stdout or "").strip()
                    self._install_bridge.finished.emit(False, tail[-300:])
                    return
                # Put the CUDA DLLs into CTranslate2's folder now, so GPU works
                # after the next restart. Safe in this process: the worker's
                # helper only copies files (it never imports ctranslate2).
                try:
                    import whisper_worker
                    whisper_worker._ensure_cuda_libs()
                except Exception:
                    pass
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
            self._update_install_note()          # box stays visible
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
    # Core add-on dependencies (audio + cloud) – one-click installer
    # ------------------------------------------------------------------

    def _build_deps_box(self) -> QWidget:
        """The 'missing components' panel with a one-click auto-installer.

        Covers the recording/cloud dependencies (sounddevice, requests, and
        audioop-lts on Python ≥ 3.13).  Hidden entirely once everything is
        present; the install button is hidden in the frozen .exe (no pip)."""
        import sys as _sys
        box = QGroupBox(_t("deps.title"))
        v = QVBoxLayout(box)
        v.setSpacing(6)

        self._deps_note = QLabel(self._deps_note_text())
        self._deps_note.setStyleSheet(_warn_style())
        self._deps_note.setWordWrap(True)
        v.addWidget(self._deps_note)

        btns = QHBoxLayout()
        self._deps_install_btn = QPushButton(_t("deps.install"))
        self._deps_install_btn.clicked.connect(self._on_install_deps)
        self._deps_install_btn.setVisible(not getattr(_sys, "frozen", False))
        btns.addWidget(self._deps_install_btn)
        deps_howto = QPushButton(_t("local.howto"))
        deps_howto.clicked.connect(self._on_show_deps_howto)
        btns.addWidget(deps_howto)
        btns.addStretch()
        v.addLayout(btns)

        self._deps_status = QLabel("")
        self._deps_status.setWordWrap(True)
        v.addWidget(self._deps_status)
        return box

    @staticmethod
    def _deps_note_text() -> str:
        missing = missing_audio_packages()
        if not missing:
            return _t("deps.title")
        return _t("deps.missing", pkgs=", ".join(missing))

    def _on_install_deps(self) -> None:
        packages = missing_audio_packages()
        if not packages:
            self._refresh_deps_box()
            return
        self._deps_install_btn.setEnabled(False)
        self._deps_status.setStyleSheet(_hint_style())
        self._deps_status.setText(_t("deps.install.running"))

        import subprocess
        import sys

        def run() -> None:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", *packages],
                    capture_output=True, text=True, timeout=900,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode != 0:
                    tail = (result.stderr or result.stdout or "").strip()
                    self._deps_bridge.finished.emit(False, tail[-300:])
                else:
                    self._deps_bridge.finished.emit(True, "")
            except Exception as exc:
                self._deps_bridge.finished.emit(False, str(exc)[:300])

        threading.Thread(target=run, daemon=True,
                         name="dictation-deps-install").start()

    def _on_deps_install_finished(self, ok: bool, err: str) -> None:
        self._deps_install_btn.setEnabled(True)
        if ok:
            # audioop / sounddevice are imported at module load, so a restart is
            # the clean way to pick them up – guide the user to do that.
            self._deps_status.setStyleSheet(_hint_style())
            self._deps_status.setText("")
            QMessageBox.information(self, _t("deps.install"),
                                    _t("deps.install.done"))
        else:
            self._deps_status.setStyleSheet(_warn_style())
            self._deps_status.setText(_t("deps.install.failed", err=err))

    def _on_show_deps_howto(self) -> None:
        pkgs = " ".join(missing_audio_packages() or ["sounddevice", "requests"])
        QMessageBox.information(self, _t("local.howto"),
                                _t("deps.howto.text", pkgs=pkgs))

    def _refresh_deps_box(self) -> None:
        if hasattr(self, "_deps_box"):
            self._deps_box.setVisible(not audio_available())
            self._deps_note.setText(self._deps_note_text())

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
        self._command_trigger = ""      # optional 2nd key: force command mode
        self._active_mode = "auto"      # mode of the recording in progress
        self._active_trigger = ""       # which key started it (for hold mode)
        self._state = "idle"            # idle | recording | transcribing
        self._state_lock = threading.Lock()
        self._audio_chunks: list[bytes] = []
        self._stream: Any = None
        self._record_started = 0.0
        self._max_timer: threading.Timer | None = None
        self._local_model: Any = None   # lazily loaded faster-whisper model
        self._local_model_name = ""
        self._model_lock = threading.Lock()    # guards model loading
        # Serialises ALL native ASR inference – Whisper decodes AND Vosk chunk
        # decoding – so the two engines never run natively at the same time
        # (concurrent native inference in one process crashes hard on Windows).
        self._asr_lock = threading.Lock()
        self._last_low_words: list[str] = []   # low-confidence words (heatmap)
        self._enroll_active = False            # guided-reading recording active
        self._live_active = False              # Vosk live streaming active
        self._vosk: Any = None
        self._live_stream: Any = None
        self._live_queue: Any = None
        self._live_thread: threading.Thread | None = None   # the live worker
        self._live_seg_audio = bytearray()     # audio of the current utterance
        self._utt_deadline = 0.0               # polish after this (a real pause)
        self._live_pause_cur = 1.0             # current pause (auto mode tunes)
        self._live_gain = 1.0                  # running auto-gain factor
        self._resample_state = None            # audioop.ratecv state (mic → 16k)
        self._whisper_proc = WhisperProc()     # out-of-process Whisper (isolated)
        self._indicator: DictationIndicator | None = None
        self._window: Any = None         # DictationWindow (created on the GUI thread)
        self._window_hwnd: int = 0       # our window's native handle (to exclude)
        self._target_hwnd: int | None = None   # app to paste into on "einfügen"
        self._reselecting = False        # waiting for the user to pick a target
        self._reselect_start_fg = 0      # foreground at the start of re-selection
        self._reselect_timer: Any = None  # QTimer polling for the chosen app
        self._error_memory: Any = None   # ErrorMemory (lazy, from settings)

        # Re-theme the dictation window when the app switches light<->dark while
        # it is open (Qt caches palette() colours baked into stylesheet strings).
        bus.subscribe("theme.changed", self._on_theme_changed)

        # Listed in the actions table / favourites / conflict checks; the key
        # itself is handled by our own hook subscription below.
        action_manager.register(Action(
            id="dictation.toggle",
            label=_t("action"),
            callback=lambda: None,
        ))
        action_manager.register(Action(
            id="dictation.command",
            label=_t("action.command"),
            callback=lambda: None,
        ))

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._ensure_indicator()
        if self._window_mode():
            self._ensure_window()   # created hidden on the GUI thread
        self._refresh_trigger()
        if not self._kb_subscribed:
            shared_keyboard_hook.subscribe(self._on_key_event)
            self._kb_subscribed = True
        # Warm the model in the background so the first dictation is fast (no
        # wait while the model loads).  The local backend honours the opt-in
        # preload checkbox.  (Live is retired; its "live" value is migrated to
        # "local" on load, so it never reaches here.)
        backend = self._settings.get("backend", "cloud")
        if backend == "live":                       # safety net if not migrated
            backend = "local"
        if self._settings.get("preload_model") and backend == "local":
            threading.Thread(target=self._preload_model, daemon=True).start()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        if self._kb_subscribed:
            shared_keyboard_hook.unsubscribe(self._on_key_event)
            self._kb_subscribed = False
        self._abort_recording()
        if self._live_active:
            self.stop_live()
        self._whisper_proc.stop()       # shut down the out-of-process worker
        self._set_state("idle")
        if self._window is not None:
            self._window.hide()
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        return DictationSettingsWidget(self)

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        if self._settings.get("backend") == "live":   # live retired → local
            self._settings["backend"] = "local"
        self._error_memory = None       # rebuild from the new profile's data
        self.on_settings_changed()

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    def on_settings_changed(self) -> None:
        self._refresh_trigger()
        action_manager.assign_trigger(
            "dictation.toggle", self._trigger if self.enabled else "")
        action_manager.assign_trigger(
            "dictation.command", self._command_trigger if self.enabled else "")
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    # ------------------------------------------------------------------

    def _ensure_indicator(self) -> None:
        # Created lazily so a merely-loaded (never enabled) module opens no
        # overlay window.  It subscribes to dictation.state on creation.
        if self._indicator is None:
            self._indicator = DictationIndicator()

    # ------------------------------------------------------------------
    # Dictation window (output mode = "window")
    # ------------------------------------------------------------------

    def _window_mode(self) -> bool:
        return self._settings.get("output_mode", "window") == "window"

    def _on_theme_changed(self, **_kw: Any) -> None:
        """Live light<->dark switch: re-resolve the window's palette stylesheets
        (fires on the GUI thread, so touching widgets here is safe)."""
        if self._window is not None:
            try:
                self._window.reapply_theme()
            except Exception:
                pass

    def _ensure_window(self) -> Any:
        """Create the dictation window (must run on the Qt main thread)."""
        if self._window is None:
            try:
                from dictation_window import DictationWindow
                self._window = DictationWindow(
                    on_insert=self._insert_into_target,
                    on_copy=self._set_clipboard,
                    on_history_changed=self._save_history,
                    on_correction=self._learn_correction,
                    on_suggest=self.suggest_corrections,
                    on_reselect_target=self.reselect_target,
                    on_confirm_words=self.confirm_words,
                    on_add_vocab=self.add_spoken_form,
                    on_ai_action=self.run_ai_action,
                    on_edit_ai_action=self.edit_ai_action,
                    on_geometry_changed=self._save_geometry,
                    on_history_toggle=self._save_history_visible,
                    on_ai_toggle=self._save_ai_visible,
                    ai_actions=self.ai_actions(),
                    history_visible=bool(
                        self._settings.get("history_visible", False)),
                    ai_visible=bool(
                        self._settings.get("ai_panel_visible", True)),
                    geometry=self._settings.get("win_geo"),
                    history=list(self._settings.get("history", [])),
                    t=_t)
                # Cache our native handle (on the GUI thread) so target capture
                # can exclude our own window.
                try:
                    self._window_hwnd = int(self._window.winId())
                except Exception:
                    self._window_hwnd = 0
            except Exception:
                _log.exception("dictation window unavailable")
        return self._window

    def _save_history(self, items: list[str]) -> None:
        """Persist the dictation history across sessions (newest first, capped
        by the window).  Stored in the module's settings, which the core writes
        to disk on settings change."""
        self._settings["history"] = list(items)
        self.on_settings_changed()

    # ------------------------------------------------------------------
    # Error memory ("Fehler-Gedächtnis") – self-learning corrections
    # ------------------------------------------------------------------

    def _memory(self) -> Any:
        if self._error_memory is None:
            from correction import ErrorMemory
            self._error_memory = ErrorMemory(self._settings.get("error_memory"))
        return self._error_memory

    def _learn_correction(self, old: str, new: str) -> None:
        """Called by the window whenever a word was corrected."""
        mem = self._memory()
        mem.learn(old, new)
        self._settings["error_memory"] = mem.to_dict()
        self.on_settings_changed()

    def remove_correction(self, key: str) -> None:
        mem = self._memory()
        mem.remove(key)
        self._settings["error_memory"] = mem.to_dict()
        self.on_settings_changed()

    def edit_correction(self, key: str, new_value: str) -> None:
        mem = self._memory()
        mem.set_target(key, new_value)
        self._settings["error_memory"] = mem.to_dict()
        self.on_settings_changed()

    def reset_memory(self) -> None:
        mem = self._memory()
        mem.clear()
        self._settings["error_memory"] = mem.to_dict()
        self.on_settings_changed()

    def direct_correction(self, wrong: str) -> str:
        """The learned correction for ``wrong`` (whole word), or ""."""
        out = self._memory().direct(wrong)
        return out if out and out.casefold() != wrong.casefold() else ""

    def suggest_corrections(self, wrong: str) -> list[str]:
        """Correction-window suggestions from the learned memory + glossary."""
        from correction import suggest_alternatives
        out: list[str] = []
        direct = self.direct_correction(wrong)
        if direct:
            out.append(direct)      # a directly learned fix goes first
        out += suggest_alternatives(wrong, self.glossary_words(), limit=9)
        return out

    def _capture_target(self) -> None:
        """Remember the app that is focused right now (to paste into later).

        Skip our *own* dictation window – otherwise, once it has focus, pressing
        the key again would capture the window itself and "einfügen" would paste
        into the wrong place.  In that case we keep the last real target."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return
        if hwnd and hwnd != self._window_hwnd:
            self._target_hwnd = hwnd
        if self._window is not None:
            self._window.set_target(self._target_name())

    def _target_name(self) -> str:
        """Human-readable name (window title) of the remembered target app."""
        hwnd = self._target_hwnd
        if not hwnd:
            return ""
        try:
            import ctypes
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value.strip()
        except Exception:
            return ""

    def reselect_target(self) -> None:
        """Wait (no time limit) until the user brings another app to the front,
        then remember it as the paste target.  Escape cancels."""
        if self._reselecting:
            return
        self._reselecting = True
        try:
            import ctypes
            self._reselect_start_fg = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            self._reselect_start_fg = 0
        if self._window is not None:
            self._window.set_reselecting(True)      # highlight the button
        from PySide6.QtCore import QTimer
        if self._reselect_timer is None:
            self._reselect_timer = QTimer()
            self._reselect_timer.setInterval(250)
            self._reselect_timer.timeout.connect(self._reselect_poll)
        self._reselect_timer.start()

    def _reselect_poll(self) -> None:
        # Cancelled (e.g. via Escape from the hook thread): stop + restore.
        if not self._reselecting:
            if self._reselect_timer is not None:
                self._reselect_timer.stop()
            if self._window is not None:
                self._window.set_reselecting(False)
                self._window.set_target(self._target_name())
            return
        try:
            import ctypes
            cur = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return
        # Capture the first *different*, non-own window the user switches to.
        if cur and cur != self._window_hwnd and cur != self._reselect_start_fg:
            self._target_hwnd = cur
            self._reselecting = False
            if self._reselect_timer is not None:
                self._reselect_timer.stop()
            if self._window is not None:
                self._window.set_reselecting(False)
                self._window.set_target(self._target_name())
                # The user picked the target app (it's now in front) – but their
                # next step is dictating, so bring the dictation window back to
                # the foreground instead of leaving focus on the target app.
                self._window.request_open()

    def _save_geometry(self, geom: list) -> None:
        self._settings["win_geo"] = list(geom)
        self.on_settings_changed()

    def _save_history_visible(self, visible: bool) -> None:
        self._settings["history_visible"] = bool(visible)
        self.on_settings_changed()

    def _save_ai_visible(self, visible: bool) -> None:
        self._settings["ai_panel_visible"] = bool(visible)
        self.on_settings_changed()

    def _insert_into_target(self, text: str) -> bool:
        """Paste the finished text into the remembered target application.

        Returns ``True`` on success.  If the target is gone/invalid, the text is
        left on the clipboard instead (``False``) so the user can paste it."""
        valid = False
        try:
            import ctypes
            hwnd = self._target_hwnd
            valid = bool(hwnd) and bool(ctypes.windll.user32.IsWindow(hwnd))
            if valid:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.05)
        except Exception:
            valid = False
        if valid:
            self._paste_via_clipboard(
                text, keep=bool(self._settings.get("keep_in_clipboard", False)))
            return True
        self._set_clipboard(text)       # fallback: user can press Ctrl+V
        return False

    # ------------------------------------------------------------------
    # Hotkey handling (shared hook)
    # ------------------------------------------------------------------

    def _refresh_trigger(self) -> None:
        self._trigger = self._settings.get("hotkey", "")
        self._command_trigger = self._settings.get("command_hotkey", "")

    def _on_key_event(self, vk: int, scan: int, extended: bool,
                      injected: bool, is_press: bool) -> bool:
        """Hook-thread callback – must return fast, never block."""
        if injected or (not self._trigger and not self._command_trigger):
            return False
        if self._enroll_active:      # guided reading owns the microphone
            return False
        if is_altgr_fake_lctrl(vk, scan):
            return False

        hold_mode = self._settings.get("mode", "toggle") == "hold"

        if is_press:
            if vk == 0x1B and self._reselecting:
                self._reselecting = False   # poll (GUI thread) stops + restores
                return True
            if vk == 0x1B and self._live_active:
                threading.Thread(target=self.stop_live, daemon=True).start()
                return True
            if vk == 0x1B and self._state == "recording":
                threading.Thread(target=self._abort_recording,
                                 daemon=True).start()
                return True
            combo = current_combo_str(vk)
            # Dictation key: plain text (or auto-detect when no command key is
            # set).  Command key: always interpreted as a command.
            if combo == self._trigger:
                mode = "text" if self._command_trigger else "auto"
            elif self._command_trigger and combo == self._command_trigger:
                mode = "command"
            else:
                return False
            # Live backend: the key toggles continuous streaming.
            if self._settings.get("backend") == "live":
                if self._live_active:
                    threading.Thread(target=self.stop_live, daemon=True).start()
                elif self._state == "idle":
                    self._active_mode = mode
                    threading.Thread(target=self.start_live, daemon=True).start()
                return True
            if self._state == "recording" and not hold_mode:
                threading.Thread(target=self._stop_and_transcribe,
                                 daemon=True).start()
            elif self._state == "idle":
                self._active_mode = mode
                self._active_trigger = combo
                threading.Thread(target=self._start_recording,
                                 daemon=True).start()
            return True  # swallow the hotkey

        if (hold_mode and self._state == "recording" and self._active_trigger
                and vk_to_combo_str(vk) == self._active_trigger.split("+")[-1]):
            threading.Thread(target=self._stop_and_transcribe,
                             daemon=True).start()
        return False

    # ------------------------------------------------------------------
    # State / indicator
    # ------------------------------------------------------------------

    def _mode_label(self) -> str:
        """Human tag for the active recording mode, shown on the chip/window."""
        if self._active_mode == "command":
            return _t("chip.command")
        if self._active_mode == "text":
            return _t("chip.dictation")
        return ""

    def _set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        if state in ("recording", "transcribing") and not detail:
            detail = self._mode_label()
        bus.publish("dictation.state", state=state, detail=detail)
        if self._window is not None:
            self._window.set_state(state, detail)

    def _error(self, detail: str) -> None:
        _log.error("dictation error: %s", detail)
        self._set_state("idle")
        bus.publish("dictation.state", state="error", detail=detail)
        if self._window is not None:
            self._window.set_state("error")

    # ------------------------------------------------------------------
    # Recording (sounddevice)
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        if self._window_mode():
            self._capture_target()          # remember the app to paste into
            if self._window is not None:
                self._window.request_open()  # show the window (thread-safe)
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

            max_s = int(self._settings.get("max_seconds", 0))
            if max_s > 0:       # 0 = endless: no auto-stop timer
                self._max_timer = threading.Timer(
                    max_s, self._stop_and_transcribe)
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
        text = (text or "").strip()
        if text and self._settings.get("collect_training"):
            self._save_training_sample(wav, text)   # opt-in data for later
        if text and not self._settings.get("raw_recognition"):
            # „reine Erkennung“ off → apply the normal refinements.
            # User dictionary (spoken → written) is deterministic user intent.
            forms = self.spoken_forms()
            if forms:
                from vocabulary import apply_spoken_forms
                text = apply_spoken_forms(text, forms)
            # Learned corrections – but only where Whisper was uncertain (a
            # clearly-spoken word is trusted), so nothing is over-corrected.
            text = self._memory().apply(text, uncertain=self._last_low_words)
            # Optional AI cleanup – only on real dictation, never on a command
            # utterance (would rewrite "markiere Haus").  Runs on this worker
            # thread, so the UI stays responsive.
            if self._settings.get("ai_cleanup") and self._active_mode != "command":
                from commands_de import parse as _parse
                if _parse(text) is None:
                    text = self._ai_cleanup(text)
            # Restore the „?" on polite questions Whisper ended with a period
            # („Können Sie …") – only on real dictation, not commands.
            if self._active_mode != "command":
                from postprocess import fix_casing, fix_question_marks
                text = fix_casing(text)          # undo stray capitalisation
                text = fix_question_marks(text)
        self._set_state("idle")
        if text:
            if self._window_mode() and self._window is not None:
                # Route into the dictation window with the key's mode
                # (text / command / auto) + low-confidence words for the heatmap
                # (already-confirmed words are no longer flagged).
                confirmed = self._confirmed_set()
                low = [w for w in self._last_low_words
                       if w.casefold() not in confirmed]
                self._window.handle_transcript(text, self._active_mode, low)
            else:
                self._insert_text(text)

    def _training_dir(self) -> str:
        return os.path.join(str(app_config.CONFIG_DIR), "dictation_training")

    # -- guided reading / enrollment (gold training pairs) ---------------

    def enroll_start(self) -> bool:
        if self._state != "idle" or self._enroll_active:
            return False
        try:
            import sounddevice as sd
        except Exception:
            return False
        self._audio_chunks = []
        try:
            device = resolve_input_device(self._settings.get("input_device"))
        except Exception:
            device = None
        try:
            self._stream, self._rec_rate, self._rec_channels = open_input_stream(
                sd, device,
                lambda indata, *a: self._audio_chunks.append(bytes(indata)))
        except Exception:
            self._stream = None
            return False
        self._enroll_active = True
        self._set_state("recording")
        return True

    def enroll_stop(self, prompt: str) -> str:
        """Stop the recording and save it.  Returns the saved sample's id (so a
        re-take can replace it), or "" on failure/too-short."""
        if not self._enroll_active:
            return ""
        self._enroll_active = False
        wav = self._close_stream()
        self._set_state("idle")
        if len(wav) < 8000:
            return ""        # too short to be useful
        return self._save_enrollment(wav, prompt)

    def _save_enrollment(self, wav_bytes: bytes, prompt: str) -> str:
        try:
            import datetime
            folder = os.path.join(self._training_dir(), "enrollment")
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            with open(os.path.join(folder, stamp + ".wav"), "wb") as f:
                f.write(wav_bytes)
            with open(os.path.join(folder, stamp + ".txt"), "w",
                      encoding="utf-8") as f:
                f.write(prompt)
            return stamp
        except Exception:
            _log.exception("could not save enrollment sample")
            return ""

    def enroll_discard(self, stamp: str) -> None:
        """Delete a saved enrollment take (used when re-recording a sentence)."""
        if not stamp:
            return
        folder = os.path.join(self._training_dir(), "enrollment")
        for ext in (".wav", ".txt"):
            try:
                os.remove(os.path.join(folder, stamp + ext))
            except OSError:
                pass

    def _save_training_sample(self, wav_bytes: bytes, text: str) -> None:
        """Opt-in: store (audio, recognised text) pairs locally so a personal
        fine-tuning (voice adaptation) becomes possible later, e.g. on a GPU."""
        try:
            import datetime
            folder = self._training_dir()
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            with open(os.path.join(folder, stamp + ".wav"), "wb") as f:
                f.write(wav_bytes)
            with open(os.path.join(folder, stamp + ".txt"), "w",
                      encoding="utf-8") as f:
                f.write(text)
        except Exception:
            _log.exception("could not save training sample")

    # ------------------------------------------------------------------
    # Live dictation (Vosk stream + Whisper polish)
    # ------------------------------------------------------------------

    def _find_vosk_model(self) -> str:
        from live_asr import find_model
        return find_model(app_config.CONFIG_DIR)

    def _ensure_vosk(self) -> Any:
        """Load the Vosk model once and keep it in memory; later dictations only
        reset the recognizer (cheap) instead of reloading ~GB from disk – that
        reload was the long wait before every recording."""
        from live_asr import VoskStreamer
        with self._model_lock:
            path = self._find_vosk_model()
            # Guard the recognizer swap with the ASR lock too: a previous live
            # worker may still be finishing (Vosk final/accept) when the next
            # dictation starts – swapping the recognizer under it crashes.
            with self._asr_lock:
                if self._vosk is None or getattr(
                        self._vosk, "model_path", None) != path:
                    self._vosk = VoskStreamer(path, 16000)   # slow – first time
                else:
                    self._vosk.reset()                       # fast – reuse model
        return self._vosk

    @staticmethod
    def _chunk_rms(chunk: bytes) -> float:
        """Loudness (RMS) of a 16-bit mono PCM chunk – used as a noise gate.
        Uses stdlib audioop (not numpy) to keep heavy native libs out of the
        Vosk process."""
        if not chunk:
            return 0.0
        try:
            return float(audioop.rms(chunk, 2))
        except Exception:
            return 0.0

    # target speech level for the auto gain (int16; ~1/8 of full scale is a
    # comfortable, headroom-safe speaking level)
    _AGC_TARGET = 3500.0

    def _auto_gain(self, chunk: bytes, rms: float) -> bytes:
        """Automatic gain control: gently amplify quiet speech toward a target
        level so a soft/distant microphone doesn't starve the recognisers.
        Only boosts (never attenuates), smooths over time to avoid pumping, and
        is hard-clamped so it can never clip."""
        if rms <= 1:
            return chunk
        desired = max(1.0, min(self._AGC_TARGET / rms, 8.0))   # boost, cap 8×
        self._live_gain = 0.85 * self._live_gain + 0.15 * desired   # smooth
        try:
            peak = audioop.max(chunk, 2) or 1
        except Exception:
            return chunk
        g = max(1.0, min(self._live_gain, 32000.0 / peak))     # never clip
        if g <= 1.01:
            return chunk
        try:
            return audioop.mul(chunk, 2, g)
        except Exception:
            return chunk

    def _pcm_to_wav(self, pcm: bytes, rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()

    def _apply_text_pipeline(self, text: str, *, aggressive: bool = False) -> str:
        """Dictionary + learned corrections + inline punctuation.  ``aggressive``
        (used for the live Vosk text) applies *every* learned word, not only the
        well-established ones, so the user's own words show up live."""
        from commands_de import apply_inline_punctuation
        forms = self.spoken_forms()
        if forms:
            from vocabulary import apply_spoken_forms
            text = apply_spoken_forms(text, forms)
        mem = self._memory()
        text = mem.apply_all(text) if aggressive else mem.apply(text)
        from postprocess import fix_question_marks
        return fix_question_marks(apply_inline_punctuation(text))

    def _apply_live_partial(self, text: str) -> str:
        """Light pipeline for the grey provisional words: apply the user's own
        dictionary and *all* learned corrections (their „training") so live
        words reflect what they taught – but no punctuation rewriting (too noisy
        on a constantly-growing partial)."""
        forms = self.spoken_forms()
        if forms:
            from vocabulary import apply_spoken_forms
            text = apply_spoken_forms(text, forms)
        return self._memory().apply_all(text)

    def start_live(self) -> None:
        if self._live_active or self._state != "idle":
            return
        try:
            import sounddevice as sd
        except Exception:
            self._error(_t("err.no_audio_lib"))
            return
        use_vosk = bool(self._settings.get("live_use_vosk", False))
        if use_vosk:
            try:
                self._ensure_vosk()         # loads once, then just resets
            except Exception as exc:
                self._error(str(exc))
                return
        else:
            self._vosk = None               # Whisper-only: no Vosk at all
        # make sure the out-of-process Whisper worker can (re)start on demand
        self._whisper_proc.configure(self._live_model_name(),
                                     max(1, (os.cpu_count() or 4) // 2))
        if self._window_mode():
            self._capture_target()
            if self._window is not None:
                self._window.request_open()
        self._live_active = True
        self._live_queue = queue.Queue()
        self._live_seg_audio = bytearray()
        self._set_state("recording")

        # The recognisers need 16 kHz mono, but many mics/headsets only offer
        # stereo and/or 44.1/48 kHz (forcing 1 channel/16 kHz raised
        # "Invalid number of channels").  Capture in the device's own format and
        # down-mix + resample to 16 kHz mono ourselves.
        try:
            self._open_input_stream(sd)
        except Exception as exc:
            self._live_active = False
            self._error(_t("err.mic", err=str(exc)[:80]))
            return
        worker = self._live_worker if use_vosk else self._live_worker_whisper
        self._live_thread = threading.Thread(target=worker, daemon=True)
        self._live_thread.start()

    def _open_input_stream(self, sd: Any) -> None:
        """Open the mic and feed 16 kHz mono int16 to the live queue, converting
        from whatever format the device actually supports.  Tries 16 kHz mono
        first, then the device's native rate/channels with down-mix + resample
        (fixes headsets that only offer stereo and/or 44.1/48 kHz)."""
        self._resample_state = None
        try:
            info = sd.query_devices(kind="input")
            native_rate = int(round(float(info.get("default_samplerate")
                                          or 16000)))
            max_ch = int(info.get("max_input_channels") or 1)
        except Exception:
            native_rate, max_ch = 16000, 1

        def make_cb(channels: int, rate: int):
            def _cb(indata, _frames, _time, _status) -> None:
                data = bytes(indata)
                if channels >= 2:               # down-mix to mono
                    try:
                        data = audioop.tomono(data, 2, 0.5, 0.5)
                    except Exception:
                        pass
                if rate != 16000:               # resample to 16 kHz
                    try:
                        data, self._resample_state = audioop.ratecv(
                            data, 2, 1, rate, 16000, self._resample_state)
                    except Exception:
                        pass
                self._live_queue.put(data)
            return _cb

        attempts = [(16000, 1), (native_rate, 1),
                    (native_rate, min(2, max(1, max_ch)))]
        seen: set = set()
        last_exc: Exception | None = None
        for rate, ch in attempts:
            if (rate, ch) in seen or ch < 1 or rate < 8000:
                continue
            seen.add((rate, ch))
            try:
                stream = sd.RawInputStream(
                    samplerate=rate, channels=ch, dtype="int16",
                    blocksize=max(1024, int(rate * 0.25)),  # ~250 ms chunks
                    callback=make_cb(ch, rate))
                stream.start()
                self._live_stream = stream
                _log.info("live mic opened: %d Hz, %d ch → 16 kHz mono",
                          rate, ch)
                return
            except Exception as exc:
                last_exc = exc
        raise last_exc or RuntimeError("no usable microphone format")

    def _live_worker(self) -> None:
        """Stream Vosk word-by-word; polish a *whole utterance* with Whisper
        only after a real pause, so punctuation isn't broken by mid-sentence
        pauses (Vosk finalises on every pause)."""
        self._live_pause_cur = max(0.4, float(self._settings.get(
            "live_pause", 1.0)))
        gate = max(0.0, float(self._settings.get("live_noise_gate", 250)))
        agc = bool(self._settings.get("live_agc", True))
        self._live_gain = 1.0
        while self._live_active:
            try:
                chunk = self._live_queue.get(timeout=0.15)
            except queue.Empty:
                chunk = None
            now = time.monotonic()
            # Noise gate: a quiet chunk only counts as speech-start when we are
            # NOT already mid-utterance.  This keeps background noise/silence
            # from *starting* an utterance, but once you're speaking the audio
            # is fed to Vosk and Whisper *contiguously* (no gaps) – gapped audio
            # was making the Whisper polish go badly wrong.
            if chunk is not None:
                rms = self._chunk_rms(chunk)
                loud = rms >= gate
                if loud and agc:                  # auto-level quiet speech
                    chunk = self._auto_gain(chunk, rms)
                if loud or self._live_seg_audio:      # in an utterance
                    self._live_seg_audio += chunk
                    try:
                        with self._asr_lock:    # never overlap a Whisper polish
                            is_final, text = self._vosk.accept(chunk)
                    except Exception:
                        is_final, text = False, ""
                    if self._window is not None and text.strip():
                        if is_final:
                            self._window.live_final(
                                self._apply_text_pipeline(text, aggressive=True))
                        else:
                            self._window.live_partial(
                                self._apply_live_partial(text))
                    if loud:
                        # only real speech extends the deadline, so trailing
                        # silence still ends the utterance after `pause`
                        self._utt_deadline = now + self._live_pause_cur
            # Real pause reached → the utterance is complete; polish it as one.
            if self._live_seg_audio and self._utt_deadline and \
                    now >= self._utt_deadline:
                self._flush_polish()
        # Mic just turned off: feed whatever is still queued to Vosk so the last
        # words aren't lost and the grey provisional gets finalised + polished
        # (otherwise stopping early leaves unprocessed grey text behind).
        while True:
            try:
                chunk = self._live_queue.get_nowait()
            except queue.Empty:
                break
            rms = self._chunk_rms(chunk)
            if rms < gate and not self._live_seg_audio:
                continue                       # skip pre-speech background noise
            if rms >= gate and agc:
                chunk = self._auto_gain(chunk, rms)
            self._live_seg_audio += chunk
            try:
                with self._asr_lock:
                    self._vosk.accept(chunk)
            except Exception:
                pass
        self._flush_polish(final=True)   # on stop: commit the final sentence

    def _flush_polish(self, *, final: bool = False) -> None:
        """Vosk path: firm up the Vosk provisional at a pause, then polish the
        whole sentence-so-far with Whisper (see _polish_sentence)."""
        if not self._live_seg_audio:
            return
        try:
            with self._asr_lock:
                rem = self._vosk.final()
        except Exception:
            rem = ""
        if rem.strip() and self._window is not None:
            self._window.live_final(
                self._apply_text_pipeline(rem, aggressive=True))
        self._utt_deadline = 0.0
        # a pause in the Vosk path is also a sentence end (commit + auto-period)
        self._polish_sentence(final=final, sentence_end=not final)

    def _polish_sentence(self, *, final: bool = False,
                         sentence_end: bool = False) -> bool:
        """Transcribe the WHOLE current sentence with the (isolated) Whisper
        worker and show it.  ``sentence_end`` (a real pause) or ``final`` (stop)
        *commit* the sentence – and if it has no „.", „!", „?" yet, a period is
        added automatically (like Dragon/Google) so sentence marks aren't lost.
        Otherwise the sentence stays open unless Whisper already ended it.
        Returns True if the sentence was committed."""
        audio = bytes(self._live_seg_audio)     # whole sentence so far
        if len(audio) < 8000 and not final:
            return False
        raw, low = "", []
        try:
            raw, low = self._whisper_proc.transcribe(
                self._pcm_to_wav(audio),
                language=self._local_language(),
                hotwords=self._hotwords() or None)
        except Exception:
            raw, low = "", []
        self._last_low_words = low
        raw = self._postprocess_asr(raw)
        polished = self._apply_text_pipeline(raw)
        if not polished:
            if final:
                self._live_seg_audio = bytearray()
            return final
        ends_punct = polished.rstrip().endswith((".", "!", "?", "…", ":"))
        commit = (final or sentence_end or ends_punct
                  or len(audio) >= _SAMPLE_RATE * 2 * 25)   # 25 s safety cap
        if commit and not ends_punct:
            # a finished sentence with no punctuation → add the missing period
            tail = polished.rstrip()
            if tail and (tail[-1].isalnum() or tail[-1] in "\"»)“”'"):
                polished = tail + "."
        if self._window is not None:
            self._window.live_polish(polished, commit)
        if commit:
            self._live_seg_audio = bytearray()   # start a fresh sentence
        return commit

    def _live_worker_whisper(self) -> None:
        """Whisper-only live worker (no Vosk): accumulate the sentence audio and
        re-transcribe it with Whisper on a pause – and every few seconds during
        long continuous speech – so the text appears in ~1–2 s steps, self-
        correcting, and commits when the sentence ends."""
        pause = max(0.4, float(self._settings.get("live_pause", 1.0)))
        gate = max(0.0, float(self._settings.get("live_noise_gate", 250)))
        agc = bool(self._settings.get("live_agc", True))
        self._live_gain = 1.0
        interim = int(_SAMPLE_RATE * 2 * 3.0)   # ~3 s of new speech → update
        last_len = 0
        while self._live_active:
            try:
                chunk = self._live_queue.get(timeout=0.15)
            except queue.Empty:
                chunk = None
            now = time.monotonic()
            if chunk is not None:
                rms = self._chunk_rms(chunk)
                loud = rms >= gate
                if loud and agc:
                    chunk = self._auto_gain(chunk, rms)
                if loud or self._live_seg_audio:      # in a sentence
                    self._live_seg_audio += chunk
                if loud:
                    self._utt_deadline = now + pause
            n = len(self._live_seg_audio)
            pause_due = bool(n and self._utt_deadline and now >= self._utt_deadline)
            interim_due = n - last_len >= interim
            if pause_due or interim_due:
                # a pause ends the sentence (commit + auto-period); an interim
                # update during long speech just refines the text, stays open.
                committed = self._polish_sentence(final=False,
                                                  sentence_end=pause_due)
                last_len = 0 if committed else len(self._live_seg_audio)
                if pause_due:
                    self._utt_deadline = 0.0
        self._polish_sentence(final=True)   # on stop: commit the final sentence

    def stop_live(self) -> None:
        if not self._live_active:
            return
        self._live_active = False       # worker exits + flushes the utterance
        try:
            self._live_stream.stop()
            self._live_stream.close()
        except Exception:
            pass
        self._live_stream = None
        # Wait for the worker to finish draining + flushing before we return, so
        # a following start_live() can't reassign self._vosk / self._live_queue
        # while the old worker still feeds the (now shared) Vosk recognizer –
        # concurrent Vosk access from two threads crashes the process.
        t = self._live_thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=5.0)
        self._live_thread = None
        self._set_state("idle")

    def _auto_tune_pause(self, polished: str) -> None:
        """Optionally learn the pause length from Whisper's own punctuation:
        a whole utterance that Whisper ends with „.", „!" or „?" was a complete
        sentence → we can afford a slightly shorter pause (more responsive); one
        that ends mid-clause means the pause cut a sentence in half → lengthen
        it.  Converges on the user's natural sentence-end pause."""
        if not self._settings.get("live_pause_auto", False):
            return
        cur = float(getattr(self, "_live_pause_cur", None)
                    or self._settings.get("live_pause", 1.0))
        ended_sentence = polished.rstrip().endswith((".", "!", "?", "…"))
        if ended_sentence:
            new = max(0.6, round(cur - 0.05, 2))     # gentle decay
        else:
            new = min(2.5, round(cur + 0.25, 2))     # cut mid-sentence → back off
        if abs(new - cur) >= 0.01:
            self._live_pause_cur = new
            # applied live via _live_pause_cur; persist in-memory (the core
            # writes settings to disk on the next change / on close).  Don't
            # call on_settings_changed() here – this runs on a worker thread.
            self._settings["live_pause"] = new

    # ------------------------------------------------------------------
    # Transcription backends
    # ------------------------------------------------------------------

    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio using the configured backend (blocking)."""
        self._last_low_words = []
        if self._settings.get("backend", "cloud") == "local":
            return self._transcribe_local(wav_bytes)
        return self._transcribe_cloud(wav_bytes)

    def _postprocess_asr(self, text: str) -> str:
        """Strip typical Whisper hallucinations / repetition loops – but NOT in
        „reine Erkennung“ mode, where we want the recogniser's plain output for
        diagnosing punctuation/casing."""
        text = (text or "").strip()
        if self._settings.get("raw_recognition"):
            return text
        from postprocess import strip_hallucinations, strip_repetitions
        return strip_repetitions(strip_hallucinations(text))

    def _language(self) -> str | None:
        lang = self._settings.get("language", "auto")
        return None if lang in ("", "auto") else lang

    def _local_language(self) -> str:
        """German-first module: an unset/'auto' language falls back to German
        for *local* recognition.  Whisper otherwise drifts to English on short
        commands ("Cursor" → "Kaser", "markiere Haus" → "Make-A-House")."""
        return self._language() or "de"

    # -- unified custom dictionary (written + optional spoken form) ------
    # One list replaces the old glossary + spoken_forms.  Each entry is a dict:
    #   {"w": written, "s": spoken (may be ""), "src": "user"|"import"|"learned"}

    def _dictionary(self) -> list[dict]:
        entries = self._settings.get("dictionary")
        if entries is None:                         # migrate the legacy lists
            entries = self._migrate_dictionary()
            self._settings["dictionary"] = entries
        out = []
        for e in entries:
            if isinstance(e, dict) and str(e.get("w", "")).strip():
                out.append({"w": str(e.get("w", "")).strip(),
                            "s": str(e.get("s", "")).strip(),
                            "src": e.get("src") or "user"})
        return out

    def _migrate_dictionary(self) -> list[dict]:
        """Build the unified list from the legacy glossary + spoken_forms."""
        out, seen = [], set()
        for pair in self._settings.get("spoken_forms", []) or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                written, spoken = str(pair[1]).strip(), str(pair[0]).strip()
                if written and written.casefold() not in seen:
                    seen.add(written.casefold())
                    out.append({"w": written, "s": spoken, "src": "user"})
        raw = (self._settings.get("glossary", "") or "")
        raw = raw.replace(chr(10), ",").replace(";", ",")
        for word in [x.strip() for x in raw.split(",") if x.strip()]:
            if word.casefold() not in seen:
                seen.add(word.casefold())
                out.append({"w": word, "s": "", "src": "learned"})
        return out

    def _save_dictionary(self, entries: list[dict]) -> None:
        self._settings["dictionary"] = [
            {"w": e["w"], "s": e.get("s", ""), "src": e.get("src", "user")}
            for e in entries if str(e.get("w", "")).strip()]
        self._settings.pop("glossary", None)        # legacy keys – now unified
        self._settings.pop("spoken_forms", None)
        self.on_settings_changed()

    # origin -> short human label shown in the editor
    _SRC_LABELS = {"user": "von mir", "import": "Import", "learned": "gelernt"}

    def dictionary_rows(self, category: str = "all") -> list[tuple]:
        """Editor rows: ``(kind, key, trigger, result, source-label)``.
        ``kind`` is "dict" or "mem"; ``trigger`` = spoken/misheard (may be "");
        ``result`` = written/correct.  Learned corrections from the error memory
        appear as kind "mem" (category „corrected") so everything is in one list
        – the memory engine itself stays untouched underneath.  ``category``
        filters by origin, "spoken" (has a trigger) or "corrected"."""
        rows = []
        if category in ("all", "user", "import", "learned", "spoken"):
            for e in reversed(self._dictionary()):
                src = e["src"]
                if category in ("user", "import", "learned") and src != category:
                    continue
                if category == "spoken" and not e["s"]:
                    continue
                rows.append(("dict", e["w"], e["s"], e["w"],
                             self._SRC_LABELS.get(src, src)))
        if category in ("all", "corrected"):
            subs = self._memory().substitutions()   # {folded misheard: correct}
            for misheard, correct in reversed(list(subs.items())):
                rows.append(("mem", misheard, misheard, correct, "korrigiert"))
        return rows

    def dictionary_count(self) -> int:
        return len(self._dictionary()) + len(self._memory().substitutions())

    def dictionary_edit(self, kind: str, key: str, trigger: str,
                        result: str) -> None:
        """Route an edit from the unified dialog to the right store."""
        if kind == "mem":
            self.edit_correction(key, result)        # change the correction
        else:
            self.edit_dictionary_entry(key, result, trigger)

    def dictionary_remove(self, kind: str, key: str) -> None:
        if kind == "mem":
            self.remove_correction(key)
        else:
            self.remove_dictionary_entry(key)

    def clear_dictionary_category(self, category: str) -> int:
        """Bulk-remove all entries of a category (quick cleanup).  Returns how
        many were removed."""
        mem_n = len(self._memory().substitutions())
        if category == "corrected":
            self.reset_memory()
            return mem_n
        entries = self._dictionary()
        if category == "all":
            self._save_dictionary([])
            self.reset_memory()
            return len(entries) + mem_n
        if category in ("user", "import", "learned"):
            keep = [e for e in entries if e["src"] != category]
            self._save_dictionary(keep)
            return len(entries) - len(keep)
        if category == "spoken":
            keep = [e for e in entries if not e["s"]]
            self._save_dictionary(keep)
            return len(entries) - len(keep)
        return 0

    def add_dictionary_entry(self, written: str, spoken: str = "",
                             src: str = "user") -> None:
        written, spoken = (written or "").strip(), (spoken or "").strip()
        if not written:
            return
        entries = self._dictionary()
        for e in entries:
            if e["w"].casefold() == written.casefold():
                if spoken:
                    e["s"] = spoken                 # update the spoken form
                self._save_dictionary(entries)
                return
        entries.append({"w": written, "s": spoken, "src": src})
        self._save_dictionary(entries)

    def edit_dictionary_entry(self, key: str, written: str,
                              spoken: str) -> None:
        written, spoken = (written or "").strip(), (spoken or "").strip()
        entries = self._dictionary()
        for e in entries:
            if e["w"].casefold() == (key or "").casefold():
                if written:            # empty written -> keep (use the X button)
                    e["w"] = written
                e["s"] = spoken
                self._save_dictionary(entries)
                return

    def remove_dictionary_entry(self, key: str) -> None:
        self._save_dictionary([e for e in self._dictionary()
                               if e["w"].casefold() != (key or "").casefold()])

    def add_learned_word(self, word: str) -> None:
        self.add_dictionary_entry(word, "", "learned")

    # -- backward-compatible accessors used elsewhere -------------------

    def glossary_words(self) -> list[str]:
        """All written forms (used for hotword biasing + suggestions)."""
        return [e["w"] for e in self._dictionary()]

    def spoken_forms(self) -> list[tuple[str, str]]:
        """(spoken, written) pairs -- entries that have a spoken form."""
        return [(e["s"], e["w"]) for e in self._dictionary() if e["s"]]

    def add_spoken_form(self, spoken: str, written: str) -> None:
        self.add_dictionary_entry(written, spoken, "user")   # window button

    def add_glossary_word(self, word: str) -> None:
        self.add_dictionary_entry(word, "", "user")

    # -- plain-text export / import -------------------------------------

    def export_dictionary(self, path: str) -> int:
        """Write the whole dictionary to a text file, one entry per line:
        ``gesprochen = geschrieben`` (or just ``geschrieben`` when there is no
        spoken form).  Returns the number of entries written."""
        nl = chr(10)
        entries = self._dictionary()
        with open(path, "w", encoding="utf-8") as f:
            f.write("# WithEase Woerterbuch - eine Zeile je Eintrag:" + nl)
            f.write("#   gesprochen = geschrieben   (oder nur: geschrieben)" + nl)
            for e in entries:
                line = (e["s"] + " = " + e["w"]) if e["s"] else e["w"]
                f.write(line + nl)
        return len(entries)

    def import_dictionary(self, path: str) -> int:
        """Merge a text dictionary (see export_dictionary) into the current one;
        existing written forms are updated, new ones appended (source
        "Import").  Returns the number of entries imported."""
        tab = chr(9)
        entries = self._dictionary()
        by_written = {e["w"].casefold(): e for e in entries}
        count = 0
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                spoken, written = "", ""
                if "=" in line:
                    spoken, written = line.split("=", 1)
                elif tab in line:
                    spoken, written = line.split(tab, 1)
                else:
                    written = line          # a bare term, no spoken form
                spoken, written = spoken.strip(), written.strip()
                if not written:
                    continue
                key = written.casefold()
                if key in by_written:
                    if spoken:
                        by_written[key]["s"] = spoken
                else:
                    e = {"w": written, "s": spoken, "src": "import"}
                    entries.append(e)
                    by_written[key] = e
                count += 1
        self._save_dictionary(entries)
        return count

    def _confirmed_set(self) -> set[str]:
        return {w.casefold() for w in self._settings.get("confirmed_words", [])}

    def confirm_words(self, words: list[str]) -> None:
        """Remember words that were flagged as uncertain but accepted unchanged,
        so they are no longer marked as low-confidence in future."""
        cw = list(self._settings.get("confirmed_words", []))
        have = {w.casefold() for w in cw}
        changed = False
        for w in words:
            w = (w or "").strip()
            if w and w.casefold() not in have:
                cw.append(w)
                have.add(w.casefold())
                changed = True
        if changed:
            self._settings["confirmed_words"] = cw[-1000:]   # keep it bounded
            self.on_settings_changed()

    def _initial_prompt(self) -> str:
        """A German biasing prompt ("dictionary") that keeps Whisper decoding
        German and nudges it toward the command words and the user's glossary."""
        commands = (
            "Cursor vor, Cursor hinter, markiere, lösche, entferne, ersetze, "
            "korrigiere, nimm eins, nimm zwei, einfügen, kopieren, schließen, "
            "neue Zeile, neuer Absatz, großschreiben, kleinschreiben, "
            "rückgängig, Punkt, Komma, Fragezeichen, buchstabieren, "
            "von, bis, alles, Satz, Absatz")
        return "Deutsches Diktat mit Sprachbefehlen. Befehle: " + commands + "."

    def _hotwords(self) -> str:
        """Words to bias recognition toward, so the user's terms come out right
        immediately: the glossary + confirmed words + learned corrections."""
        words = list(self.glossary_words())
        words += list(self._settings.get("confirmed_words", []))[-40:]
        words += list(self._memory().substitutions().values())
        words += [w for _s, w in self.spoken_forms()]     # written forms
        seen: set[str] = set()
        out: list[str] = []
        for w in words:
            w = (w or "").strip()
            if w and w.casefold() not in seen:
                seen.add(w.casefold())
                out.append(w)
        return " ".join(out[:80])

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
        return self._postprocess_asr(resp.json().get("text", ""))

    # -- Local (faster-whisper) ------------------------------------------

    def _whisper_device(self) -> tuple[str, str]:
        """Pick the fastest working backend: GPU float16 if a usable CUDA GPU is
        present, otherwise CPU with int8 (much faster than float32)."""
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "float16"
        except Exception:
            pass
        return "cpu", "int8"

    def _live_model_name(self) -> str:
        """The live polish is the *final* text, so it needs good accuracy;
        tiny/base/small are weaker for German.  Default the live polish to
        'medium' (a user who explicitly picked small or large-v3 keeps it)."""
        m = self._settings.get("local_model", "base")
        return m if m in ("small", "medium", "large-v3") else "medium"

    def _ensure_model_loaded(self, model_name: str | None = None) -> Any:
        """Load the faster-whisper model (once).  Guarded by a lock so a
        background preload and a live dictation can't load – or *import* – it
        twice at the same time (concurrent first import crashes the process)."""
        if model_name is None:
            model_name = self._settings.get("local_model", "base")
        with self._model_lock:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise RuntimeError(_t("err.no_local"))
            if self._local_model is None or self._local_model_name != model_name:
                device, compute_type = self._whisper_device()
                threads = max(1, (os.cpu_count() or 4) // 2)
                _log.info("loading local whisper %r on %s/%s (%d threads)",
                          model_name, device, compute_type, threads)
                self._local_model = WhisperModel(
                    model_name, device=device, compute_type=compute_type,
                    cpu_threads=threads)
                self._local_model_name = model_name
        return self._local_model

    def _preload_model(self) -> None:
        """Load the model ahead of time so the first dictation is fast."""
        try:
            self._ensure_model_loaded()
            _log.info("whisper model preloaded")
        except Exception:
            _log.exception("model preload failed")

    def _preload_live(self) -> None:
        """Warm the live pipeline at startup: keep the Vosk model resident in
        THIS process, and start the out-of-process Whisper worker.  Whisper's
        native libs never load into this (Vosk) process – that isolation is
        what removes the crashes."""
        if bool(self._settings.get("live_use_vosk", False)):
            try:
                self._ensure_vosk()
            except Exception:
                _log.exception("vosk preload failed")
        try:
            threads = max(1, (os.cpu_count() or 4) // 2)
            if self._whisper_proc.start(self._live_model_name(), threads):
                _log.info("whisper worker ready")
        except Exception:
            _log.exception("whisper worker start failed")

    def _transcribe_local(self, wav_bytes: bytes, *, live: bool = False) -> str:
        self._ensure_model_loaded(self._live_model_name() if live else None)

        language = self._local_language()
        # The German command "dictionary" prompt helps the batch/command path,
        # but on the short clips of the LIVE polish Whisper tends to *echo* the
        # prompt (inventing words the user never said) – so drop it there.
        prompt = (None if live
                  else (self._initial_prompt() if language == "de" else None))
        hotwords = self._hotwords() or None     # bias toward the user's terms
        # A scalar temperature disables faster-whisper's fallback re-decode, so
        # repetitive/low-confidence hallucinations ("Und so. Und so. …") are
        # kept.  A temperature *list* re-enables that guard for the live polish.
        temperature: Any = ([0.0, 0.2, 0.4, 0.6, 0.8, 1.0] if live else 0.0)
        # Native ASR must never run concurrently (see _asr_lock): a second
        # decode – or a live Vosk chunk – running at the same time crashes the
        # whole process.  Serialise every Whisper decode here.
        with self._asr_lock:
            segments, _info = self._local_model.transcribe(
                io.BytesIO(wav_bytes),
                language=language,
                initial_prompt=prompt,
                hotwords=hotwords,
                beam_size=5,
                temperature=temperature,
                condition_on_previous_text=False,
                vad_filter=True,        # skip silence → far fewer hallucinations
                # anti-hallucination thresholds (drop invented/repetitive text)
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                word_timestamps=True,   # per-word probs → confidence heatmap
            )
            parts, low = [], []
            for seg in segments:        # iterating drives the actual inference
                # On the live path, drop segments Whisper itself flags as most
                # likely non-speech + low-confidence – the usual hallucinations.
                if live and getattr(seg, "no_speech_prob", 0.0) > 0.6 \
                        and getattr(seg, "avg_logprob", 0.0) < -1.0:
                    continue
                parts.append(seg.text.strip())
                for w in (getattr(seg, "words", None) or []):
                    if getattr(w, "probability", 1.0) < 0.55:
                        token = (w.word or "").strip().strip(" .,;:!?…\"'„“”")
                        if token:
                            low.append(token)
            self._last_low_words = low
        return self._postprocess_asr(" ".join(parts))

    # -- optional AI cleanup (local Ollama / cloud chat) -----------------

    def _ai_cleanup(self, text: str) -> str:
        """Lightly correct grammar/punctuation via an LLM; on any failure keep
        the original text so dictation is never blocked."""
        backend = self._settings.get("ai_backend", "local")
        try:
            cleaned = (self._ai_cloud_chat(text) if backend == "cloud"
                       else self._ai_local_chat(text))
        except Exception:
            _log.exception("AI cleanup failed – keeping original text")
            return text
        from postprocess import guard_cleanup
        return guard_cleanup(text, cleaned)

    # Local AI providers – both run on the user's own PC.  Ollama speaks its
    # own /api/chat; LM Studio exposes an OpenAI-compatible /v1 API.  The model
    # lists come from each program's running instance (see list_ai_models).
    _AI_LOCAL_DEFAULTS = {
        "ollama": {"chat": "http://localhost:11434/api/chat",
                   "models": "http://localhost:11434/api/tags"},
        "lmstudio": {"chat": "http://localhost:1234/v1/chat/completions",
                     "models": "http://localhost:1234/v1/models"},
    }

    def _ai_local_provider(self) -> str:
        """Normalised local AI provider: ``"ollama"`` or ``"lmstudio"``.

        Legacy profiles stored ``"local"`` (Ollama only) – treat that as
        Ollama so nothing breaks on upgrade."""
        return ("lmstudio"
                if self._settings.get("ai_backend") == "lmstudio"
                else "ollama")

    def _ai_local_chat(self, text: str, system: str | None = None,
                       temperature: float = 0) -> str:
        if self._ai_local_provider() == "lmstudio":
            return self._ai_lmstudio_chat(text, system, temperature)
        return self._ai_ollama_chat(text, system, temperature)

    def _ai_ollama_chat(self, text: str, system: str | None = None,
                        temperature: float = 0) -> str:
        import requests
        from postprocess import build_cleanup_prompt
        model = self._settings.get("ai_model") or "llama3.2"
        url = (self._settings.get("ai_local_url")
               or self._AI_LOCAL_DEFAULTS["ollama"]["chat"])
        payload = {
            "model": model, "stream": False,
            "think": False,          # skip „thinking“ models' slow reasoning
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system or build_cleanup_prompt()},
                {"role": "user", "content": text},
            ],
        }
        resp = requests.post(url, json=payload, timeout=180)
        if not resp.ok:                     # surface Ollama's own error text
            try:
                err = resp.json().get("error")
            except Exception:
                err = None
            raise RuntimeError(err or f"{resp.status_code} {resp.reason}")
        return resp.json().get("message", {}).get("content", "")

    def _ai_lmstudio_chat(self, text: str, system: str | None = None,
                          temperature: float = 0) -> str:
        import requests
        from postprocess import build_cleanup_prompt
        model = self._settings.get("ai_model") or ""
        url = (self._settings.get("ai_lmstudio_url")
               or self._AI_LOCAL_DEFAULTS["lmstudio"]["chat"])
        payload = {
            "model": model, "stream": False, "temperature": temperature,
            "messages": [
                {"role": "system", "content": system or build_cleanup_prompt()},
                {"role": "user", "content": text},
            ],
        }
        resp = requests.post(url, json=payload, timeout=180)
        if not resp.ok:                     # surface LM Studio's own error text
            try:
                err = resp.json().get("error")
                msg = err.get("message") if isinstance(err, dict) else err
            except Exception:
                msg = None
            raise RuntimeError(msg or f"{resp.status_code} {resp.reason}")
        return resp.json()["choices"][0]["message"]["content"]

    def list_ai_models(self) -> list[str]:
        """Model names offered by the running local AI provider – Ollama's
        pulled models or LM Studio's loaded models.  Blocking (short timeout);
        returns ``[]`` on any error or when the program is not running, and for
        the cloud backend (there the model is free-text)."""
        if self._settings.get("ai_backend") == "cloud":
            return []
        import requests
        provider = self._ai_local_provider()
        url = self._AI_LOCAL_DEFAULTS[provider]["models"]
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        if provider == "lmstudio":     # OpenAI shape: {"data": [{"id": ...}]}
            return [str(m["id"]) for m in data.get("data", []) if m.get("id")]
        # Ollama shape: {"models": [{"name": ...}]}
        return [str(m["name"]) for m in data.get("models", []) if m.get("name")]

    def _ai_cloud_chat(self, text: str, system: str | None = None,
                       temperature: float = 0) -> str:
        import requests
        from postprocess import build_cleanup_prompt
        base_url, _style, _model, api_key = self._cloud_config()
        if not base_url or not api_key:
            raise RuntimeError("cloud AI not configured")
        model = self._settings.get("ai_model") or "gpt-4o-mini"
        payload = {
            "model": model, "temperature": temperature,
            "messages": [
                {"role": "system", "content": system or build_cleanup_prompt()},
                {"role": "user", "content": text},
            ],
        }
        resp = requests.post(
            f"{base_url}/chat/completions", json=payload,
            headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
        if not resp.ok:                     # surface the provider's error text
            try:
                err = resp.json().get("error")
                msg = err.get("message") if isinstance(err, dict) else err
            except Exception:
                msg = None
            raise RuntimeError(msg or f"{resp.status_code} {resp.reason}")
        return resp.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # KI-Aktionen: user-defined prompt buttons in the dictation window
    # ------------------------------------------------------------------

    _DEFAULT_AI_ACTIONS = [
        {"name": "E-Mail",
         "prompt": "Formuliere den folgenden diktierten Text als höfliche, gut "
                   "strukturierte deutsche E-Mail (passende Anrede und "
                   "Grußformel, sinnvolle Absätze). Gib nur die E-Mail zurück, "
                   "ohne Erklärungen."},
        {"name": "Stichpunkte",
         "prompt": "Fasse den folgenden Text als klare, kurze Stichpunkte "
                   "zusammen. Gib nur die Liste zurück."},
        {"name": "Sauber formulieren",
         "prompt": "Formuliere den folgenden Text in klarem, korrektem Deutsch "
                   "aus (Rechtschreibung, Grammatik, Zeichensetzung, flüssige "
                   "Sätze), ohne die Bedeutung zu verändern. Gib nur den Text "
                   "zurück."},
    ]

    def ai_actions(self) -> list[tuple[str, str]]:
        """The configured (name, prompt) buttons; seeded with examples once."""
        raw = self._settings.get("ai_actions")
        if raw is None:
            raw = [dict(a) for a in self._DEFAULT_AI_ACTIONS]
            self._settings["ai_actions"] = raw
        out = []
        for a in raw:
            if isinstance(a, dict) and str(a.get("name", "")).strip() \
                    and str(a.get("prompt", "")).strip():
                out.append((str(a["name"]).strip(), str(a["prompt"]).strip()))
        return out

    def ai_actions_raw(self) -> list[dict]:
        """The configured actions as editable dicts (for the settings editor)."""
        self.ai_actions()      # ensure the defaults are seeded once
        return [dict(a) for a in self._settings.get("ai_actions", [])]

    def save_ai_actions(self, actions: list[dict]) -> None:
        self._settings["ai_actions"] = [
            {"name": str(a.get("name", "")).strip(),
             "prompt": str(a.get("prompt", "")).strip()}
            for a in actions
            if str(a.get("name", "")).strip() and str(a.get("prompt", "")).strip()]
        self.on_settings_changed()
        if self._window is not None:
            self._window.set_ai_actions(self.ai_actions())

    def edit_ai_action(self, index: int) -> None:
        """Open the KI-Aktionen editor pre-selected on one action – wired to the
        right-click menu on the window's buttons for quick editing."""
        from settings_dialogs import AiActionsDialog
        dlg = AiActionsDialog(self.ai_actions_raw(),
                              on_save=self.save_ai_actions,
                              select_index=index, parent=self._window)
        dlg.exec()

    def run_ai_action(self, prompt: str) -> None:
        """Apply a prompt to the dictation buffer via the configured LLM and put
        the result back into the window (undoable).  Runs off the GUI thread."""
        if self._window is None:
            return
        text = self._window.text().strip()
        if not text:
            self._window.ai_message("(kein Text im Diktierfenster)")
            return
        backend = self._settings.get("ai_backend", "local")
        self._window.ai_busy(True)

        def work() -> None:
            try:
                if backend == "cloud":
                    result = self._ai_cloud_chat(text, system=prompt,
                                                 temperature=0.3)
                else:
                    result = self._ai_local_chat(text, system=prompt,
                                                 temperature=0.3)
            except Exception as exc:
                _log.exception("KI-Aktion fehlgeschlagen")
                if self._window is not None:
                    self._window.ai_busy(False)
                    self._window.ai_message(
                        "KI nicht erreichbar/konfiguriert: " + str(exc)[:70])
                return
            import re
            # drop any <think>…</think> reasoning that „thinking“ models emit
            result = re.sub(r"(?is)<think>.*?</think>", "", result or "")
            result = result.strip().strip("\"'„“”").strip()
            if result:
                # finish the KI result like a dictation: restore „?" on polite
                # questions the model missed, and apply the user's dictionary.
                from postprocess import fix_question_marks
                result = fix_question_marks(result)
                forms = self.spoken_forms()
                if forms:
                    from vocabulary import apply_spoken_forms
                    result = apply_spoken_forms(result, forms)
            if self._window is None:
                return
            if result:
                self._window.ai_result(result)
            else:
                self._window.ai_busy(False)
                self._window.ai_message("KI lieferte kein Ergebnis.")

        threading.Thread(target=work, daemon=True).start()

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
