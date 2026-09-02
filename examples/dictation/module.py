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

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
from withease.gui import theme as _core_theme
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
# A little longer than the undo bar's own 20 s, so the files are still there
# for the whole time the button is offered – and gone shortly after.
_UNDO_PURGE_MS = 25_000
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
        "group.output": "▸ Textausgabe",
        "group.output.open": "▾ Textausgabe",
        "group.vocab_ai": "Wörterbuch & KI",
        "group.vocab": "▸ Wörterbuch",
        "group.vocab.open": "▾ Wörterbuch",
        "group.vocab.desc":
            "Eigene Wörter, Namen und Fachbegriffe, die die Erkennung "
            "sicherer treffen soll – und optional, wie sie geschrieben "
            "werden.",
        "group.ai": "▸ KI",
        "group.ai.open": "▾ KI",
        "group.advanced": "▸ Erweitert",
        "group.advanced.open": "▾ Erweitert",
        "action": "Diktat starten/stoppen",
        "action.command": "Sprachbefehl starten/stoppen",
        "hotkey": "Diktier-Taste",
        "hotkey.command": "Befehls-Taste (optional)",
        "hotkey.command.hint": "Wenn gesetzt: Diese Taste ist nur für Befehle (Cursor, markiere …), die Diktier-Taste nur für Text. So werden Befehl und Diktat sauber getrennt.",
        "mode": "Aufnahmemodus",
        "mode.hint": "Halten: Aufnahme läuft, solange die Taste gedrückt wird – sie endet von selbst.\nUmschalten: Einmal drücken startet, noch einmal beendet – besser, wenn längeres Halten schwerfällt.",
        "mode.toggle": "Umschalten",
        "mode.toggle.hint": "Taste startet/stoppt",
        "mode.hold": "Halten",
        "mode.hold.hint": "Sprechen solange gedrückt",
        "backend": "Erkennung",
        "backend.hint": "Lokal: Die Aufnahme verlässt diesen PC nie. Braucht einmalig einen Download und mehr Rechenleistung.\nCloud-Dienst: Schneller und genauer, dafür wird die Aufnahme an den Anbieter gesendet.",
        "backend.cloud": "Cloud-Dienst",
        "backend.cloud.hint": "Die Aufnahme wird an einen Anbieter geschickt (OpenRouter, OpenAI, Groq …) – den wählst du unten unter „Anbieter“.",
        "backend.local": "Lokal auf diesem PC",
        "backend.local.missing": "nicht installiert",
        "backend.live": "Live-Diktat",
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
        "provider.custom": "Eigene URL",
        "provider.custom.hint": "Beliebiger Dienst, der die OpenAI-Schnittstelle spricht.",
        "base_url": "Server-URL",
        "api_key": "API-Schlüssel",
        "api_key.hint": "Wird gerätweit gespeichert (nicht im Profil), derzeit im Klartext in app.json.",
        "model": "Modell",
        "model.hint": "Welches Modell der Anbieter verwenden soll. Im Zweifel die Vorauswahl lassen – größere Modelle erkennen genauer, brauchen aber länger und kosten beim Anbieter mehr.",
        "local_model": "Whisper-Modell",
        "local.hint": "Beim ersten Diktat wird das Modell heruntergeladen (tiny ≈ 75 MB … large-v3 ≈ 1,5 GB). Größer = genauer, aber langsamer.",
        "local_model.load": "Jetzt laden",
        "local_model.load.hint": "Lädt das gewählte Modell sofort herunter und in den Speicher.\nOhne das passiert es beim ersten Diktat – dann steht minutenlang nur „Erkenne Text …“ da, ohne dass etwas über den Fortschritt gesagt wird.",
        "local_model.changed": "Geändert. Das Modell wird beim ersten Diktat geladen – bei großen Modellen kann das einige Minuten dauern. Mit „Jetzt laden“ gleich erledigen.",
        "local_model.loading": "Modell wird geladen … (kann dauern)",
        "local_model.ready": "Modell ist geladen und einsatzbereit.",
        "local_model.failed": "Laden fehlgeschlagen: {err}",
        "local.not_installed": "Die lokale Erkennung ist auf diesem PC noch nicht installiert. Du kannst sie mit einem Klick automatisch installieren lassen – es sind keine Vorkenntnisse nötig.",
        "local.frozen_note": "Die lokale Erkennung kann auf diesem PC eingerichtet werden. Beim ersten Mal lädt WithEase dafür eine kleine, eigene Spracherkennungs-Umgebung herunter (Internetverbindung nötig, einige Minuten). Ein Klick genügt – keine Vorkenntnisse erforderlich. Alles bleibt auf diesem PC.",
        "local.setup.uv": "Installationswerkzeug wird geladen …",
        "local.setup.python": "Spracherkennungs-Umgebung wird eingerichtet … Das kann einige Minuten dauern. Du kannst das Fenster geöffnet lassen.",
        "local.setup.packages": "Spracherkennung wird installiert … Das kann einige Minuten dauern. Du kannst das Fenster geöffnet lassen.",
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
        "chip_size": "Statusanzeige",
        "chip_size.hint": "Größe des Status-Chips oben am Bildschirm "
                          "(Aufnahme-Anzeige und Ziel-App-Hinweis).",
        "chip_size.sync": "Größe von der Allgemein-Seite übernehmen",
        "chip_size.sync.same":
            "Die Größe stimmt bereits mit der Allgemein-Seite überein.",
        "lang.auto": "Automatisch erkennen",
        "lang.de": "Deutsch",
        "lang.en": "Englisch",
        "lang.fr": "Französisch",
        "lang.es": "Spanisch",
        "lang.it": "Italienisch",
        "lang.nl": "Niederländisch",
        "lang.pl": "Polnisch",
        "lang.pt": "Portugiesisch",
        "lang.ru": "Russisch",
        "lang.tr": "Türkisch",
        "lang.uk": "Ukrainisch",
        "lang.zh": "Chinesisch",
        "lang.ja": "Japanisch",
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
        "ai.enable": "Diktierten Text von einer KI glätten",
        "ai.hint": "Korrigiert nur Grammatik/Zeichensetzung, ändert die Bedeutung nicht. Läuft nur bei reinem Diktat (nicht bei Befehlen); Ergebnis erscheint im Diktierfenster.",
        "ai.backend": "Wo die KI läuft",
        "ai.backend.hint":
            "Ollama: Läuft lokal auf diesem PC – der Text bleibt hier.\n"
            "LM Studio: Ebenfalls lokal, für alle, die dieses Programm "
            "schon nutzen.\n"
            "Cloud: Der diktierte Text wird zum Glätten an den Anbieter "
            "gesendet.",
        "ai.local": "Lokal (Ollama, bleibt auf dem PC)",
        "ai.ollama": "Ollama",
        "ai.ollama.hint": "Läuft lokal – der Text bleibt auf diesem PC.",
        "ai.lmstudio": "LM Studio",
        "ai.lmstudio.hint": "Läuft lokal – der Text bleibt auf diesem PC.",
        "ai.cloud": "Cloud",
        "ai.cloud.hint": "Der Text wird an den Anbieter gesendet.",
        "ai.model": "KI-Modell",
        "ai.model.hint": "Bei Ollama/LM Studio aus der Liste wählbar (Aktualisieren-Knopf lädt die im Programm verfügbaren Modelle); Cloud als Freitext, z. B. „gpt-4o-mini“.",
        "ai.model.refresh.hint": "Modell-Liste vom laufenden Programm (Ollama/LM Studio) neu laden",
        "ai.model.none": "Keine Modelle gefunden – läuft Ollama bzw. LM Studio und ist ein Modell geladen?",
        "raw": "Nur reine Erkennung",
        "raw.hint": "Zeigt die reine Ausgabe der Spracherkennung – ohne unsere Nachbearbeitung (keine Satzzeichen-Korrektur, kein Wörterbuch, kein Fehler-Gedächtnis, keine Halluzinations-Filter, keine KI-Bereinigung). Zum Diagnostizieren: So sieht man, ob Fehler von der Erkennung selbst oder von der Nachbearbeitung kommen.",
        "numeric_dates": "Datumsangaben als Zahlen schreiben",
        "numeric_dates.hint": "Gesprochene Datumsangaben werden in die kurze Schreibweise umgesetzt: „20. August 2026“ oder „zwanzigsten August 2026“ wird zu „20.08.2026“.\nNur bei einem echten Monatsnamen. Ein unmöglicher Tag („40. August“) und alles andere im Satz bleiben unberührt. Ausschalten, wenn du die ausgeschriebene Form behalten möchtest.",
        "ai.actions": "KI-Aktionen",
        "ai.actions.hint": "Frei belegbare Buttons links im Diktierfenster: Jeder Button schickt deinen Prompt zusammen mit dem Fensterinhalt an die KI (z. B. „mach daraus eine E-Mail“) und ersetzt den Text durch das Ergebnis. Nutzt das oben eingestellte KI-Backend.",
        "snippets": "Textbausteine",
        "snippets.note": "Textbausteine werden bei den MAKROS verwaltet – dort, wo sie auch stehen. Ein Makro vom Typ „Text“ fügst du im Diktierfenster mit „füge <Name> ein“ oder „Baustein <Name>“ ein; eine Taste musst du dafür nicht vergeben.\nSo legst du deine Grußformel nur EINMAL an und siehst alle an einer Stelle.",
        "snippets.goto": "Zu den Makros",
        "snippets.move": "{n} alte Textbausteine übernehmen",
        "snippets.move.failed": "Makromodul ist nicht aktiv",
        "output": "Ausgabe",
        "output.hint": "Diktierfenster: Der Text landet zuerst in einem Fenster, in dem du ihn per Sprache korrigieren kannst.\nDirekt: Der Text geht sofort dorthin, wo gerade der Cursor steht.",
        "output.window": "Diktierfenster",
        "output.window.hint": "Mit Sprachbefehlen und Korrekturmöglichkeit.",
        "output.direct": "Direkt in die aktive Anwendung einfügen",
        "insert": "Text einfügen per",
        "insert.hint": "Zwischenablage: Schnell, überschreibt aber, was gerade kopiert ist.\nTippen: Langsamer, funktioniert dafür auch in Programmen, die Strg+V nicht annehmen.",
        "insert.clipboard": "Zwischenablage",
        "insert.clipboard.hint": "Fügt über Strg+V ein – schnell.",
        "insert.type": "Tippen",
        "insert.type.hint": "Schreibt Zeichen für Zeichen – langsamer, dafür überall zuverlässig.",
        "keep_clipboard": "Erkannten Text zusätzlich in der Zwischenablage behalten",
        "join_dictations": "Diktate aneinanderhängen",
        "join_dictations.hint": "Beim direkten Einfügen wird das nächste Diktat passend an das vorige angehängt: Leerzeichen davor, und groß oder klein weitergeschrieben, je nachdem ob ein Satzzeichen davor steht.\nNur solange dasselbe Programm im Vordergrund ist. Klickst du zwischen zwei Diktaten woanders hin, beginnt der Text wieder wie gesprochen.",
        "take_selection": "Markierten Text ins Diktierfenster holen",
        "take_selection.hint": "Ist beim Start des Diktats etwas markiert, landet dieser Text im Diktierfenster – zum Weiterdiktieren oder Bearbeiten. Beim Einfügen ersetzt er die Markierung.\nStandardmäßig aus: Zum Holen wird Strg+C an das Programm geschickt, und in einem Konsolenfenster bricht Strg+C den laufenden Befehl ab. Nur einschalten, wenn du nicht in solche Programme diktierst. Deine Zwischenablage bleibt unverändert.",
        "max_seconds": "Max. Aufnahmedauer",
        "max_seconds.off": "Endlos",
        "hallucination": "Halluzinationen filtern",
        "hallucination.hint": "Whisper erfindet am Ende einer Aufnahme manchmal Text, der gar nicht gesprochen wurde. „Normal“ entfernt solche eindeutig erfundenen Stellen. „Stark“ filtert aggressiver (auch den letzten Satz) – falls am Ende noch etwas übrig bleibt; kann in seltenen Fällen ein leise gesprochenes Wort verschlucken. „Aus“ schaltet die Prüfung ab.",
        "mic.quiet": "Mikrofon sehr leise – bitte Aufnahmepegel erhöhen.",
        "hallucination.off": "Aus",
        "hallucination.normal": "Normal",
        "hallucination.normal.hint": "Empfohlene Einstellung.",
        "hallucination.strong": "Stark",
        "pause_media": "Medien während des Diktats pausieren",
        "pause_media.hint": "Sobald du den Diktierknopf drückst und die Aufnahme läuft, wird die Medienwiedergabe (Musik, Video) pausiert. Sie wird automatisch fortgesetzt, wenn das Diktat fertig ist – auch erst, nachdem die Erkennung die Aufnahme berechnet hat.",
        "preload": "Spracherkennung beim Start vorladen",
        "preload.hint": "Lädt das Whisper-Modell schon beim Start, damit das erste Diktat sofort schnell ist. Erscheint nur, wenn „Mit Windows starten“ (Allgemein) aktiv ist.",
        "device": "Mikrofon",
        "device.default": "Standardgerät",
        "test": "Test: 3 Sekunden aufnehmen und erkennen",
        "test.recording": "🎙 Aufnahme läuft (3 s) …",
        "test.result": "Erkannter Text:\n\n{text}",
        "test.error": "Test fehlgeschlagen:\n\n{err}",
        "chip.recording": "Aufnahme … (Esc bricht ab)",
        "nothing.heard": "Nichts erkannt – noch einmal versuchen",
        "nothing.quiet": "Nichts erkannt – Mikrofon zu leise",
        "nothing.short": "Zu kurz – Taste etwas länger halten",
        "chip.warn.dismiss": "verschwindet von selbst",
        "chip.transcribing": "Erkenne Text …",
        "chip.dictation": "Diktat",
        "chip.command": "Befehl",
        "chip.error": "Diktat-Fehler",
        "chip.error.fix": "Klicken, um die Einstellungen zu öffnen",
        "setup.todo": "Noch zu tun:",
        "setup.hotkey": "Diktier-Taste festlegen",
        "setup.local": "Spracherkennung installieren (Knopf unten)",
        "setup.key": "API-Schlüssel eintragen",
        "setup.url": "Server-URL eintragen",
        "setup.test": "Mit „Test“ prüfen, ob alles sitzt",
        "setup.ready": "Eingerichtet. Mit „Test“ prüfen, ob die Erkennung sitzt.",
        "group.data": "▸ Deine Daten",
        "group.data.open": "▾ Deine Daten",
        "data.desc": "Was WithEase beim Diktieren über dich speichert – alles nur auf diesem PC, und alles hier löschbar.",
        "data.history": "Verlauf",
        "data.history.value": "{n} gespeicherte Diktate",
        "data.history.hint": "Die zuletzt diktierten Texte, im Klartext in deinem Profil. Praktisch zum Zurückholen – aber sie stehen dort, bis du sie löschst.\nMit „Anzahl“ = 0 wird gar nichts mehr gespeichert.",
        "data.history.limit": "Anzahl",
        "data.training": "Sprachaufnahmen (ältere Version)",
        "data.training.value": "{n} Aufnahmen · {size}",
        "data.training.hint": "Tonaufnahmen deiner Diktate, die eine ältere Version von WithEase gesammelt hat.\nNeue Aufnahmen entstehen nicht mehr: WithEase speichert deine Stimme nicht. Diese Zeile ist nur noch zum Aufräumen da und verschwindet, sobald du gelöscht hast.",
        "data.dictionary": "Wörterbuch & Korrekturen",
        "data.dictionary.value": "{n} Einträge",
        "data.dictionary.hint": "Eigene Wörter und gelernte Korrekturen. Über „Bearbeiten“ im Bereich Wörterbuch einsehen, ändern und einzeln löschen.",
        "data.key": "API-Schlüssel",
        "data.key.set": "gespeichert (im Klartext in app.json)",
        "data.key.unset": "keiner gespeichert",
        "data.key.hint": "Wird gerätweit gespeichert, nicht im Profil – und derzeit unverschlüsselt.",
        "data.delete": "Löschen",
        "data.deleted": "Gelöscht.",
        "data.confirm.history": "Alle {n} gespeicherten Diktate entfernen? Das lässt sich nicht rückgängig machen.",
        "data.confirm.training": "Alle {n} Sprachaufnahmen ({size}) unwiderruflich löschen?",
        "data.confirm.key": "Den gespeicherten API-Schlüssel entfernen?",
        "undo.history": "{n} Diktate gelöscht.",
        "undo.recordings": "{n} Sprachaufnahmen gelöscht.",
        "undo.api_key": "API-Schlüssel entfernt.",
        "data.nothing": "Nichts zu löschen.",
        "chip.reselect": "Ziel-App-Modus",
        "chip.reselect.hint": "Zur gewünschten App wechseln, dann Leertaste · Esc bricht ab",
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
        "group.output": "▸ Text output",
        "group.output.open": "▾ Text output",
        "group.vocab_ai": "Dictionary & AI",
        "group.vocab": "▸ Dictionary",
        "group.vocab.open": "▾ Dictionary",
        "group.vocab.desc":
            "Your own words, names and technical terms that recognition "
            "should get right – and optionally how they are written.",
        "group.ai": "▸ AI",
        "group.ai.open": "▾ AI",
        "group.advanced": "▸ Advanced",
        "group.advanced.open": "▾ Advanced",
        "deps_missing": "⚠ This add-on is missing components. To enable it, run in the program folder:  pip install sounddevice requests  (for local recognition also: faster-whisper)",
        "action": "Start/stop dictation",
        "action.command": "Start/stop voice command",
        "hotkey": "Dictation key",
        "hotkey.command": "Command key (optional)",
        "hotkey.command.hint": "When set: this key is for commands only (Cursor, select …) and the dictation key for text only – a clean split between command and dictation.",
        "mode": "Recording mode",
        "mode.hint": "Hold: recording runs for as long as the key is held – it stops by itself.\nToggle: press once to start, again to stop – better if holding a key for longer is difficult.",
        "mode.toggle": "Toggle",
        "mode.toggle.hint": "Key starts/stops",
        "mode.hold": "Hold",
        "mode.hold.hint": "Speak while pressed",
        "backend": "Recognition",
        "backend.hint": "Local: the recording never leaves this PC. Needs a one-off download and more computing power.\nCloud service: faster and more accurate, but the recording is sent to the provider.",
        "backend.cloud": "Cloud service",
        "backend.cloud.hint": "The recording is sent to a provider (OpenRouter, OpenAI, Groq …) – pick it below under “Provider”.",
        "backend.local": "Locally on this PC",
        "backend.local.missing": "not installed",
        "backend.live": "Live dictation",
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
        "provider.custom": "Custom URL",
        "provider.custom.hint": "Any service that speaks the OpenAI API.",
        "base_url": "Server URL",
        "api_key": "API key",
        "api_key.hint": "Stored device-wide (not in the profile), currently in plain text in app.json.",
        "model": "Model",
        "model.hint": "Which model the provider should use. When in doubt keep the preselection – bigger models recognise more accurately but take longer and cost more at the provider.",
        "local_model": "Whisper model",
        "local.hint": "The model is downloaded on first use (tiny ≈ 75 MB … large-v3 ≈ 1.5 GB). Bigger = more accurate but slower.",
        "local_model.load": "Load now",
        "local_model.load.hint": "Downloads the chosen model and loads it into memory right away.\nWithout this it happens during the first dictation – where it just says „Erkenne Text …“ for minutes with nothing about the progress.",
        "local_model.changed": "Changed. The model is fetched on the first dictation – for large models that can take several minutes. Use „Load now“ to get it over with.",
        "local_model.loading": "Loading the model … (may take a while)",
        "local_model.ready": "Model loaded and ready.",
        "local_model.failed": "Loading failed: {err}",
        "local.not_installed": "Local recognition is not installed on this PC yet. You can have it installed automatically with one click – no technical knowledge needed.",
        "local.frozen_note": "Local recognition can be set up on this PC. The first time, WithEase downloads a small dedicated speech-recognition environment for it (internet connection required, a few minutes). One click is enough – no technical knowledge needed. Everything stays on this PC.",
        "local.setup.uv": "Downloading the setup tool …",
        "local.setup.python": "Setting up the speech-recognition environment … This may take a few minutes. You can keep this window open.",
        "local.setup.packages": "Installing speech recognition … This may take a few minutes. You can keep this window open.",
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
        "chip_size": "Status display",
        "chip_size.hint": "Size of the status chip at the top of the screen "
                          "(recording indicator and target-app hint).",
        "chip_size.sync": "Copy the size from the General page",
        "chip_size.sync.same":
            "The size already matches the General page.",
        "lang.auto": "Detect automatically",
        "lang.de": "German",
        "lang.en": "English",
        "lang.fr": "French",
        "lang.es": "Spanish",
        "lang.it": "Italian",
        "lang.nl": "Dutch",
        "lang.pl": "Polish",
        "lang.pt": "Portuguese",
        "lang.ru": "Russian",
        "lang.tr": "Turkish",
        "lang.uk": "Ukrainian",
        "lang.zh": "Chinese",
        "lang.ja": "Japanese",
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
        "ai.enable": "Smooth dictated text with an AI",
        "ai.hint": "Fixes only grammar/punctuation, never the meaning. Runs on plain dictation (not commands); result appears in the dictation window.",
        "ai.backend": "Where the AI runs",
        "ai.backend.hint":
            "Ollama: runs locally on this PC – the text stays here.\n"
            "LM Studio: also local, for anyone already using that "
            "program.\n"
            "Cloud: the dictated text is sent to the provider for "
            "smoothing.",
        "ai.local": "Local (Ollama, stays on this PC)",
        "ai.ollama": "Ollama",
        "ai.ollama.hint": "Runs locally – the text stays on this PC.",
        "ai.lmstudio": "LM Studio",
        "ai.lmstudio.hint": "Runs locally – the text stays on this PC.",
        "ai.cloud": "Cloud",
        "ai.cloud.hint": "The text is sent to the provider.",
        "ai.model": "AI model",
        "ai.model.hint": "For Ollama/LM Studio pick from the list (the refresh button loads the models available in the program); cloud is free-text, e.g. \"gpt-4o-mini\".",
        "ai.model.refresh.hint": "Reload the model list from the running program (Ollama/LM Studio)",
        "ai.model.none": "No models found – is Ollama or LM Studio running with a model loaded?",
        "raw": "Raw recognition only",
        "raw.hint": "Shows the recogniser's plain output – without any of our post-processing (no punctuation fixes, no dictionary, no error memory, no hallucination filter, no AI cleanup). For diagnosing whether errors come from recognition itself or from post-processing.",
        "numeric_dates": "Write dates as numbers",
        "numeric_dates.hint": "Spoken dates are turned into the short form: „20. August 2026“ or „zwanzigsten August 2026“ becomes „20.08.2026“.\nOnly with a real month name. An impossible day („40. August“) and everything else in the sentence are left untouched. Switch it off to keep the spelled-out form.",
        "ai.actions": "AI actions",
        "ai.actions.hint": "Custom buttons on the left of the dictation window: each sends your prompt together with the window text to the AI (e.g. \"turn this into an email\") and replaces the text with the result. Uses the AI backend set above.",
        "snippets": "Text blocks",
        "snippets.note": "Text blocks are managed with the MACROS – where they already live. A macro of type „Text“ is inserted in the dictation window with „füge <name> ein“ or „Baustein <name>“; it does not need a key at all.\nSo your sign-off is created ONCE and everything is in one place.",
        "snippets.goto": "Go to the macros",
        "snippets.move": "Take over {n} old text blocks",
        "snippets.move.failed": "The macros module is not active",
        "output": "Output",
        "output.hint": "Dictation window: the text lands in a window first, where you can correct it by voice.\nDirect: the text goes straight to wherever the cursor is.",
        "output.window": "Dictation window",
        "output.window.hint": "With voice commands and correction.",
        "output.direct": "Insert directly into the active application",
        "insert": "Insert text via",
        "insert.hint": "Clipboard: fast, but overwrites whatever is currently copied.\nTyping: slower, but also works in programs that do not accept Ctrl+V.",
        "insert.clipboard": "Clipboard",
        "insert.clipboard.hint": "Pastes via Ctrl+V – fast.",
        "insert.type": "Typing",
        "insert.type.hint": "Writes character by character – slower, but works everywhere.",
        "keep_clipboard": "Also keep the recognised text in the clipboard",
        "join_dictations": "Join dictations together",
        "join_dictations.hint": "When inserting directly, the next dictation is attached to the previous one: a space in front, and continued in upper or lower case depending on whether a sentence mark comes before it.\nOnly while the same program is in front. Click somewhere else between two dictations and the text starts exactly as spoken again.",
        "take_selection": "Take the selected text into the dictation window",
        "take_selection.hint": "If something is selected when dictation starts, that text appears in the dictation window – to continue or edit it. On insert it replaces the selection.\nOff by default: fetching it sends Ctrl+C to the program, and in a console window Ctrl+C aborts the running command. Only switch it on if you do not dictate into such programs. Your clipboard is left untouched.",
        "max_seconds": "Max. recording length",
        "max_seconds.off": "Endless",
        "hallucination": "Hallucination filter",
        "hallucination.hint": "Whisper sometimes invents text on the silence at the end of a recording. \"Normal\" removes such clearly invented bits. \"Strong\" filters more aggressively (including the last sentence) – if something still slips through; in rare cases it may swallow a very quietly spoken word. \"Off\" disables the check.",
        "mic.quiet": "Microphone very quiet – please raise the recording level.",
        "hallucination.off": "Off",
        "hallucination.normal": "Normal",
        "hallucination.normal.hint": "The recommended setting.",
        "hallucination.strong": "Strong",
        "pause_media": "Pause media while dictating",
        "pause_media.hint": "As soon as you press the dictation key and recording starts, media playback (music, video) is paused. It resumes automatically once dictation is finished – only after recognition has finished processing the audio.",
        "preload": "Preload speech recognition at start",
        "preload.hint": "Loads the Whisper model at start so the first dictation is fast right away. Only shown when 'Start with Windows' (General) is on.",
        "device": "Microphone",
        "device.default": "Default device",
        "test": "Test: record 3 seconds and transcribe",
        "test.recording": "🎙 Recording (3 s) …",
        "test.result": "Recognised text:\n\n{text}",
        "test.error": "Test failed:\n\n{err}",
        "chip.recording": "Recording … (Esc cancels)",
        "nothing.heard": "Nothing recognised – please try again",
        "nothing.quiet": "Nothing recognised – microphone too quiet",
        "nothing.short": "Too short – hold the key a little longer",
        "chip.warn.dismiss": "disappears by itself",
        "chip.transcribing": "Transcribing …",
        "chip.dictation": "Dictation",
        "chip.command": "Command",
        "chip.error": "Dictation error",
        "chip.error.fix": "Click to open the settings",
        "setup.todo": "Still to do:",
        "setup.hotkey": "Set a dictation key",
        "setup.local": "Install speech recognition (button below)",
        "setup.key": "Enter an API key",
        "setup.url": "Enter a server URL",
        "setup.test": "Use „Test“ to check that it works",
        "setup.ready": "Set up. Use „Test“ to check that recognition works.",
        "group.data": "▸ Your data",
        "group.data.open": "▾ Your data",
        "data.desc": "What WithEase stores about you while dictating – all of it on this PC only, and all of it deletable here.",
        "data.history": "History",
        "data.history.value": "{n} stored dictations",
        "data.history.hint": "The most recent dictations, in plain text in your profile. Handy for getting one back – but they stay there until you delete them.\nWith „Anzahl“ = 0 nothing is stored at all.",
        "data.history.limit": "Number",
        "data.training": "Voice recordings (older version)",
        "data.training.value": "{n} recordings · {size}",
        "data.training.hint": "Audio recordings of your dictations that an older version of WithEase collected.\nNo new ones are made: WithEase does not store your voice. This row is only here for tidying up and disappears once you have deleted them.",
        "data.dictionary": "Dictionary & corrections",
        "data.dictionary.value": "{n} entries",
        "data.dictionary.hint": "Your own words and learned corrections. Use „Bearbeiten“ in the dictionary section to view, change and remove them one by one.",
        "data.key": "API key",
        "data.key.set": "stored (in plain text in app.json)",
        "data.key.unset": "none stored",
        "data.key.hint": "Stored device-wide, not in the profile – and currently unencrypted.",
        "data.delete": "Delete",
        "data.deleted": "Deleted.",
        "data.confirm.history": "Remove all {n} stored dictations? This cannot be undone.",
        "data.confirm.training": "Permanently delete all {n} voice recordings ({size})?",
        "data.confirm.key": "Remove the stored API key?",
        "undo.history": "{n} dictations deleted.",
        "undo.recordings": "{n} voice recordings deleted.",
        "undo.api_key": "API key removed.",
        "data.nothing": "Nothing to delete.",
        "chip.reselect": "Target-app mode",
        "chip.reselect.hint": "Switch to the app you want, then press Space · Esc cancels",
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


# Delegate to the core theme so hints look EXACTLY like those on the built-in
# module pages (Maus/Tastatur/…).  This used to be a self-contained
# palette(windowText) rule, but that is the full-brightness text colour – the
# descriptions came out plain white in the dark theme while every core page
# showed them in the secondary grey, which read as two different designs.
# theme.hint_color() is the accessible (≥4.5:1) secondary grey per scheme.
def _hint_style() -> str:
    return _core_theme.hint_style()


class _HintLabel(QLabel):
    """A word-wrapped hint that fills from the field column to the right edge
    and whose height tracks the *actual* wrapped text.

    A plain QLabel reports its height for a narrow „preferred" width, so a form
    over-allocates it and leaves a big empty gap below the text (the uneven
    spacing).  Pinning the height to heightForWidth(currentWidth) removes it."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setWordWrap(True)
        self.setStyleSheet(_hint_style())
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding,
                           QSizePolicy.Policy.Minimum)

    def resizeEvent(self, event: object) -> None:  # type: ignore[override]
        super().resizeEvent(event)      # type: ignore[arg-type]
        self.setFixedHeight(self.heightForWidth(self.width()))


def _warn_style() -> str:
    return "color: #D9534F; font-size: smaller;"   # readable on light + dark


def _title_style() -> str:
    return "font-weight: bold; font-size: larger;"


# Fixed point size for section-header icons – matches theme.py's
# QLabel#cardIcon (15pt) so both icon-rendering paths look the same size.
_SECTION_ICON_PT = 15


def _section_icon_side() -> int:
    """Pixel side length for a section-header icon at the fixed point size –
    computed from that FIXED font, never the app's current font, so it stays
    constant regardless of the font-size setting."""
    font = QFont()
    font.setPointSize(_SECTION_ICON_PT)
    return QFontMetrics(font).height()


def _option_hint(combo, index: int, text: str) -> None:
    """Explain ONE dropdown entry, shown when it is hovered in the open list.

    Same two places for an explanation as the core UI, so nothing has to be
    re-learned when switching between a built-in page and this add-on: the ⓘ
    after a setting's NAME, and the entry itself for a CHOICE.  Falls back to a
    plain tooltip if the core is older than the shared helper – an add-on can
    be installed next to any WithEase version.
    """
    try:
        from withease.gui.ui_utils import set_option_hint
        set_option_hint(combo, index, text)
    except Exception:
        combo.setItemData(index, text, Qt.ItemDataRole.ToolTipRole)


def _label_with_hint(text: str, tooltip: str):
    """``ui_utils.label_with_hint`` with a fallback for an OLDER core.

    An add-on module is installed independently of the program itself, so it
    can end up next to a core that predates a helper it uses.  Importing that
    helper unguarded turns "one row looks plainer" into "the whole app refuses
    to start with an ImportError" – which is exactly what happened with a
    packaged build older than the module.  Degrade instead: plain caption, the
    explanation still reachable as its tooltip.
    """
    try:
        from withease.gui.ui_utils import label_with_hint
        return label_with_hint(text, tooltip)
    except Exception:
        lbl = QLabel(text)
        lbl.setToolTip(tooltip)
        return lbl


def _mark_danger(button):
    """``ui_utils.mark_danger`` with a fallback for an OLDER core.

    An add-on is installed independently of the program, so a missing helper
    must never be more than a missing tint."""
    try:
        from withease.gui.ui_utils import mark_danger
        return mark_danger(button)
    except Exception:
        return button


def _checkbox_with_hint(checkbox, tooltip: str):
    """``ui_utils.checkbox_with_hint`` with a fallback for an older core."""
    try:
        from withease.gui.ui_utils import checkbox_with_hint
        return checkbox_with_hint(checkbox, tooltip)
    except Exception:
        checkbox.setToolTip(tooltip)
        return checkbox


def _undo_possible() -> bool:
    """True if this core has the undo bar.  Asked BEFORE deleting: on an older
    core the user must be asked first instead, because deleting with no way
    back is the one behaviour that is never acceptable."""
    try:
        from withease.gui.widgets.undo_bar import show_undo  # noqa: F401
        return True
    except Exception:
        return False


def _show_undo(widget, text: str, on_undo) -> bool:
    """``widgets.undo_bar.show_undo`` with a fallback for an older core.
    Returns True if the bar is actually up."""
    try:
        from withease.gui.widgets.undo_bar import show_undo
        return show_undo(widget, text, on_undo) is not None
    except Exception:
        return False


def _wrap_tip(text: str) -> str:
    """``ui_utils.wrap_tooltip`` with a fallback for an OLDER core.

    Without it Qt lays a tool-tip out as ONE line, so a two-sentence
    explanation stretches from screen edge to screen edge."""
    try:
        from withease.gui.ui_utils import wrap_tooltip
        return wrap_tooltip(text)
    except Exception:
        return text


def _setting_note(text: str):
    """``ui_utils.setting_note`` with a fallback for an older core.

    A permanently visible explanation under a single setting – used only where
    the user cannot decide without it (does my voice leave this PC, how big is
    the download, where does the key end up)."""
    try:
        from withease.gui.ui_utils import setting_note
        return setting_note(text)
    except Exception:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(_hint_style())
        return lbl


def _fixed_size_icon(glyph: str) -> QIcon:
    """Render a glyph/emoji to a QIcon at a FIXED point size, independent of
    the app's font-size setting – used for section icons so they stay put next
    to text that does scale (matches theme.py's QLabel#cardIcon convention)."""
    font = QFont()
    font.setPointSize(_SECTION_ICON_PT)
    side = _section_icon_side()
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return QIcon(pm)


class _Collapsible(QWidget):
    """A titled section that shows/hides its content on click – used to tuck
    rarely-needed expert options away so the page stays calm by default.

    The header is a chevron button (▸ closed / ▾ open), not a checkbox, so it
    never reads as a feature toggle and the "expand" hint disappears once it is
    open."""

    toggled = Signal(bool)

    def __init__(self, title_closed: str, title_open: str,
                 icon: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title_closed = title_closed
        self._title_open = title_open

        # The WHOLE section – header included – lives inside one card frame,
        # not just the content: a collapsed section used to be a bare
        # chevron button floating between cards, which didn't read as its
        # own list item.  Same objectName("card") every other card uses, so
        # it looks identical to CollapsibleSection (mouse/keyboard settings)
        # while collapsed.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._frame = QFrame()
        self._frame.setObjectName("card")
        outer.addWidget(self._frame)

        v = QVBoxLayout(self._frame)
        v.setContentsMargins(0, 0, 0, 0)   # padding comes from the card QSS
        v.setSpacing(6)
        self._btn = QToolButton()
        self._btn.setText(title_closed)
        if icon:
            # Qt's own icon+text layout keeps the icon at setIconSize()
            # regardless of the button's (font-size-driven) text size.  A
            # QToolButton shows only the icon by default – tell it to keep
            # the text alongside.
            self._btn.setIcon(_fixed_size_icon(icon))
            side = _section_icon_side()
            self._btn.setIconSize(QSize(side, side))
            self._btn.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._btn.setCheckable(True)
        self._btn.setChecked(False)
        self._btn.setAutoRaise(True)
        # The header is an accessible click target in BOTH directions: full
        # card width and at least the standard target height.  It used to be
        # only as wide as its own text, so opening (whole card) was easy but
        # closing meant hitting the short title exactly.
        self._btn.setMinimumHeight(_core_theme.target_px())
        self._btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 2px 0;"
            " text-align: left; }")
        self._btn.toggled.connect(self._on_toggled)
        v.addWidget(self._btn)
        self._content = QWidget()
        self._content.setVisible(False)
        self._content_body = QVBoxLayout(self._content)
        self._content_body.setContentsMargins(0, 6, 0, 2)
        self._content_body.setSpacing(10)
        v.addWidget(self._content)
        self._frame.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Clicking anywhere on a COLLAPSED card opens it.

        Aiming at the small chevron/title strip is exactly the kind of
        precision this app exists to avoid, so the whole card is the target.
        Only while collapsed, though: once open, a stray click beside a
        control must not fold the section away again (and clicks on the
        controls themselves never reach this handler anyway)."""
        if not self._btn.isChecked():
            self._btn.setChecked(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_toggled(self, on: bool) -> None:
        self._btn.setText(self._title_open if on else self._title_closed)
        self._content.setVisible(on)
        # A closed card is one big button – say so with the cursor.
        self._frame.setCursor(Qt.CursorShape.ArrowCursor if on
                              else Qt.CursorShape.PointingHandCursor)
        self.toggled.emit(on)
        if on:
            # Scroll the freshly opened card fully into view (same helper the
            # core's CollapsibleSection uses, so both behave identically).
            try:
                from withease.gui.ui_utils import ensure_card_visible
                ensure_card_visible(self)
            except Exception:
                pass

    def set_open(self, on: bool) -> None:
        """Open/close the section programmatically (e.g. to restore a saved
        state).  Emits ``toggled`` like a user click."""
        self._btn.setChecked(on)

    def content_body(self) -> QVBoxLayout:
        """Layout to add the section's own content (e.g. a QFormLayout) into –
        the content widget itself already owns a QVBoxLayout (see __init__),
        so callers must not install a layout directly on it."""
        return self._content_body


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


def local_recognition_ready() -> bool:
    """Frozen-aware: can local recognition actually run on this PC right now?

    Source build: identical to :func:`local_backend_available` (faster-whisper
    importable in this interpreter).  Packaged .exe: whether the dedicated local
    runtime has been set up (see ``local_runtime``) – the frozen interpreter can
    never import faster-whisper itself, so it delegates to that runtime."""
    import local_runtime
    return local_runtime.runtime_ready()


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


def _send_media_play_pause() -> None:
    """Tap the system Play/Pause media key (Windows only).

    This is the same ``keybd_event`` mechanism the app already uses for key
    injection.  The key is a *toggle* handled by whatever media session is
    active, so we send it once to pause playback while dictating and once more
    to resume it afterwards – tracked by ``_media_paused`` so we only ever
    resume what we paused."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        vk = 0xB3            # VK_MEDIA_PLAY_PAUSE
        extended = 0x0001    # KEYEVENTF_EXTENDEDKEY (media keys are extended)
        keyup = 0x0002       # KEYEVENTF_KEYUP
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, extended, 0)
        user32.keybd_event(vk, 0, extended | keyup, 0)
    except Exception:        # never let a missing/blocked key break dictation
        _log.debug("could not send media play/pause key", exc_info=True)


def _system_audio_playing() -> bool | None:
    """Is the default output device *currently* playing audio?

    ``True``/``False`` when it can be determined, ``None`` when it can't.  Used
    to make the media-pause feature safe: the Play/Pause key is a toggle, so if
    nothing is actually playing (e.g. Spotify open but paused) sending it would
    *start* playback.  We therefore only send it when audio is really coming out.

    Implemented dependency-free via the WASAPI peak meter
    (``IAudioMeterInformation`` on the default multimedia render endpoint), so it
    works in the packaged .exe without any extra package.  Fails open (returns
    ``None``) on anything unexpected so the feature never breaks."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import POINTER, byref, c_float, c_void_p
    try:
        ole32 = ctypes.windll.ole32
    except Exception:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def guid(text: str) -> "GUID":
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(g))
        return g

    def vcall(pobj, index, argtypes, *args):
        vtable = ctypes.cast(pobj, POINTER(c_void_p))[0]
        func = ctypes.cast(vtable, POINTER(c_void_p))[index]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)
        return proto(func)(pobj, *args)

    def release(pobj):
        try:
            vtable = ctypes.cast(pobj, POINTER(c_void_p))[0]
            func = ctypes.cast(vtable, POINTER(c_void_p))[2]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(func)(pobj)
        except Exception:
            pass

    clsid_enum = guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")  # MMDeviceEnumerator
    iid_enum = guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")    # IMMDeviceEnumerator
    iid_meter = guid("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")   # IAudioMeterInformation
    CLSCTX_ALL = 23
    eRender, eMultimedia = 0, 1

    hr = ole32.CoInitializeEx(None, 0)     # MTA; S_OK=0 / S_FALSE=1 = we inited
    inited = hr in (0, 1)
    p_enum = c_void_p()
    p_dev = c_void_p()
    p_meter = c_void_p()
    try:
        if ole32.CoCreateInstance(byref(clsid_enum), None, CLSCTX_ALL,
                                  byref(iid_enum), byref(p_enum)) != 0 \
                or not p_enum:
            return None
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable index 4)
        if vcall(p_enum, 4, (ctypes.c_int, ctypes.c_int, POINTER(c_void_p)),
                 eRender, eMultimedia, byref(p_dev)) != 0 or not p_dev:
            return None
        # IMMDevice::Activate (index 3) → IAudioMeterInformation
        if vcall(p_dev, 3, (POINTER(GUID), ctypes.c_uint32, c_void_p,
                            POINTER(c_void_p)),
                 byref(iid_meter), CLSCTX_ALL, None, byref(p_meter)) != 0 \
                or not p_meter:
            return None
        # IAudioMeterInformation::GetPeakValue (index 3) – sample briefly to ride
        # over a momentary silent instant during playback.
        best = 0.0
        for _ in range(3):
            peak = c_float()
            if vcall(p_meter, 3, (POINTER(c_float),), byref(peak)) == 0:
                best = max(best, peak.value)
            if best > 0.0005:
                break
            time.sleep(0.015)
        return best > 0.0005
    except Exception:
        _log.debug("system audio check failed", exc_info=True)
        return None
    finally:
        for p in (p_meter, p_dev, p_enum):
            if p:
                release(p)
        if inited:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


# --- Media control via SMTC (System Media Transport Controls) --------------
# The media Play/Pause key only reaches ONE session (the system's "current"
# one), so with several players open (Spotify + YouTube) it can't pause them
# all.  Windows' SMTC API can enumerate every media session, pause exactly the
# ones that are playing, and later resume exactly those.  We reach it through a
# short PowerShell/WinRT snippet – dependency-free and working in the .exe.

def _powershell_exe() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                        "powershell.exe")


_SMTC_HEADER = r"""$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$g=([System.WindowsRuntimeSystemExtensions].GetMethods()|?{$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'})[0]
function Await($o,$t){$m=$g.MakeGenericMethod($t);$k=$m.Invoke($null,@($o));$k.Wait(-1)|Out-Null;$k.Result}
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]|Out-Null
$mgr=Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
"""

# Pause every session whose PlaybackStatus is Playing (enum value 4) and print
# the app id of each one paused (one per line).
_SMTC_PAUSE = _SMTC_HEADER + r"""foreach($s in $mgr.GetSessions()){
  if([int]$s.GetPlaybackInfo().PlaybackStatus -eq 4){
    try{ Await ($s.TryPauseAsync()) ([bool])|Out-Null; Write-Output $s.SourceAppUserModelId }catch{}
  }
}
"""

# Resume exactly the app ids passed in via the environment (newline-separated).
_SMTC_PLAY = _SMTC_HEADER + r"""$want=$env:WITHEASE_RESUME_IDS -split "`n" | ?{ $_ -ne '' }
foreach($s in $mgr.GetSessions()){
  if($want -contains $s.SourceAppUserModelId){
    try{ Await ($s.TryPlayAsync()) ([bool])|Out-Null }catch{}
  }
}
"""


def _run_powershell(script: str, extra_env: dict | None = None,
                    timeout: int = 20) -> str | None:
    """Run a PowerShell snippet hidden; return stdout, or None on any failure."""
    if sys.platform != "win32":
        return None
    import subprocess
    try:
        import local_runtime
        env = local_runtime.clean_child_env()   # never inherit bundled DLLs
    except Exception:
        env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            [_powershell_exe(), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=timeout, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        _log.debug("powershell invocation failed", exc_info=True)
        return None
    if result.returncode != 0:
        _log.debug("powershell rc=%s: %s", result.returncode,
                   (result.stderr or "")[-200:])
        return None
    return result.stdout


def _smtc_pause_playing() -> list[str] | None:
    """Pause every currently-playing media session.  Returns the list of app ids
    paused (possibly empty) or ``None`` if SMTC was unavailable (→ fall back)."""
    out = _run_powershell(_SMTC_PAUSE)
    if out is None:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _smtc_play_apps(apps: list[str]) -> None:
    """Resume exactly the given media sessions."""
    if not apps:
        return
    _run_powershell(_SMTC_PLAY, extra_env={"WITHEASE_RESUME_IDS": "\n".join(apps)})


class ConfigError(RuntimeError):
    """A failure that a SETTING has to fix (missing key, URL, recogniser).

    Distinguished from a passing glitch so the message can stay on screen and
    lead the user to the page instead of vanishing after a few seconds."""


def _hallucination_params(level: str) -> dict:
    """Segment-filter + decode settings for the end-of-dictation hallucination
    filter.  ``level`` is 'off' | 'normal' | 'strong'.

    Whisper tends to invent text on the trailing silence of a clip.  The most
    reliable cure is to drop segments it itself flags as most-likely non-speech
    (high ``no_speech_prob``) and/or low-confidence (very negative
    ``avg_logprob``), plus its own ``hallucination_silence_threshold`` at decode
    time.  'normal' is deliberately conservative (both conditions must hold, so
    only near-certain junk is dropped); 'strong' is more aggressive and also
    scrutinises the very last segment (the classic trailing hallucination)."""
    if level == "off":
        return {"drop": False, "hall_sil": None,
                "word_prob": None, "word_gap": None}
    if level == "strong":
        return {"drop": True, "ns": 0.5, "lp": -0.9, "combine": "or",
                "trail_ns": 0.4, "hall_sil": 1.0,
                "word_prob": 0.5, "word_gap": 0.7}
    return {"drop": True, "ns": 0.6, "lp": -1.0, "combine": "and",   # normal
            "trail_ns": 2.0, "hall_sil": 2.0,
            "word_prob": 0.35, "word_gap": 1.5}


def _clip_seconds(wav_bytes: bytes, rate: int = _SAMPLE_RATE) -> float:
    """Length of a 16-bit mono WAV in seconds (header ≈ 44 bytes)."""
    if not wav_bytes or len(wav_bytes) <= 44:
        return 0.0
    return (len(wav_bytes) - 44) / 2.0 / max(1, rate)


# Below this a recording cannot contain "a long dictation plus invented text
# at the end" – it is one short utterance, and the aggressive rules would be
# judging the whole of it rather than a trailing tail.
_SHORT_CLIP_S = 1.8


def _effective_hall_level(level: str, *, is_command: bool,
                          seconds: float) -> str:
    """The filter level to really use for THIS recording.

    „Stark“ is tuned for the trailing silence of a long dictation: its rules
    are OR-combined and it scrutinises the LAST segment especially hard
    (``trail_ns`` 0.4).  On a two-word command the only segment *is* the last
    one, so the whole utterance is dropped – or its trailing word is trimmed
    and „nimm drei“ arrives as „nimm“, which matches no command at all.  From
    the outside that looks exactly like the program ignoring you.

    A word swallowed while dictating is visible and can be corrected; one
    swallowed in a command silently does nothing.  So a command – and any
    utterance too short to *have* a trailing tail – is filtered at most at
    „Normal“.  „Aus“ stays off, because that is an explicit choice.
    """
    if level != "strong":
        return level
    if is_command or seconds <= _SHORT_CLIP_S:
        return "normal"
    return level


def _seg_is_hallucination(no_speech_prob: float, avg_logprob: float,
                          is_last: bool, params: dict) -> bool:
    """Whether one Whisper segment should be dropped as a likely hallucination,
    per the ``_hallucination_params`` settings.  Self-contained so the worker
    process can use the same rule."""
    if not params.get("drop"):
        return False
    ns, lp = params.get("ns", 0.6), params.get("lp", -1.0)
    if params.get("combine") == "or":
        hit = no_speech_prob > ns or avg_logprob < lp
    else:
        hit = no_speech_prob > ns and avg_logprob < lp
    if not hit and is_last and no_speech_prob > params.get("trail_ns", 2.0):
        hit = True     # the trailing segment on silence – the usual offender
    return hit


def _trailing_trim_count(words: list, params: dict) -> int:
    """How many words to drop from the END of the last segment.

    End-of-clip hallucinations show up as trailing words that are either very
    low-confidence *or* separated from real speech by a silence gap (Whisper
    jumped over the trailing silence and invented text).  Scan from the end and
    count such words, stopping at the first solid, contiguous word.  Returns 0
    when nothing should be trimmed.  Self-contained so the worker can reuse it."""
    wp = params.get("word_prob")
    wg = params.get("word_gap")
    if not words or (wp is None and wg is None):
        return 0
    n = 0
    for i in range(len(words) - 1, -1, -1):
        w = words[i]
        prob = getattr(w, "probability", 1.0)
        start = getattr(w, "start", 0.0) or 0.0
        prev_end = (getattr(words[i - 1], "end", start) or start) if i > 0 else start
        gap = start - prev_end
        if (wp is not None and prob < wp) or (wg is not None and gap > wg):
            n += 1
        else:
            break
    return n


class WhisperProc:
    """Runs the faster-whisper worker in a *separate process* and talks to it
    over line-based JSON.  Keeping Whisper's native libraries out of the main
    (Vosk) process removes the native-runtime conflict that crashed the app."""

    def __init__(self) -> None:
        self._proc: Any = None
        self._lock = threading.Lock()      # one request at a time
        self._start_args: tuple | None = None
        self._running_model: str | None = None   # model the live worker loaded

    def configure(self, model: str, threads: int) -> None:
        """Remember how to (re)start the worker without starting it now, so a
        later transcribe() can lazily spin it up under its own lock."""
        self._start_args = (model, threads)

    def start(self, model: str, threads: int) -> bool:
        import subprocess

        import local_runtime
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "whisper_worker.py")
        self._start_args = (model, threads)
        # In the packaged .exe the frozen interpreter has no pip and cannot run
        # a .py worker, so we use the dedicated local runtime instead.  It is
        # None until the user sets it up ("Automatisch installieren").
        py = local_runtime.worker_python()
        if not py:
            _log.error("no local Python runtime available for the whisper worker")
            self._proc = None
            return False
        try:
            self._proc = subprocess.Popen(
                [py, worker, model, str(threads), "auto"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                env=local_runtime.clean_child_env(),
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
        self._running_model = model
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

    def transcribe(self, wav_bytes: bytes, *, model: str | None = None,
                   threads: int | None = None, language: str | None = None,
                   hotwords: str | None = None,
                   initial_prompt: str | None = None,
                   live: bool = True, hall: dict | None = None) -> tuple[str, list]:
        import tempfile
        with self._lock:
            # Which model should be loaded?  Falls back to whatever configure()
            # / a previous start remembered (the live path relies on that).
            want_model = model if model is not None else (
                self._start_args[0] if self._start_args else None)
            want_threads = threads if threads is not None else (
                self._start_args[1] if self._start_args else 4)
            # (Re)start when the worker is down or is running a different model –
            # the batch path may want a larger model than the live polish.
            if not self.alive() or (
                    want_model is not None and self._running_model != want_model):
                if self.alive():
                    self.stop()
                if want_model is None or not self.start(want_model, want_threads):
                    return "", []
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            f.write(wav_bytes)
            f.close()
            try:
                req = {"wav": f.name, "language": language,
                       "initial_prompt": initial_prompt, "hotwords": hotwords,
                       "live": live, "hall": hall}
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
        self._running_model = None
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
    "reselect": "#2E7D32",      # green – picking a target app
}
_CHIP_FG = "#FFFFFF"
_CHIP_RADIUS = 6
_CHIP_MARGIN = 12
_CHIP_DEFAULT_H = 28
# Allowed chip heights in px.  Deliberately the SAME range as the shared
# Sticky-Keys/macro chip size on the Allgemein page, so the "take the
# value from there" button can transfer any value 1:1 – with a narrower
# range here a larger central value was silently clamped and the button
# looked like it had done nothing.
_CHIP_MIN_H = 16
_CHIP_MAX_H = 64
_CHIP_SUB_GAP = 6               # gap between the chip and its hint line
# The hint line's height is DERIVED from its font size (see _sub_h): it used
# to be a fixed 20px while the font grew with the chip size, so from about
# scale 1.6 upwards the text was cut off – exactly where a larger chip was
# chosen because the text was hard to read in the first place.
_CHIP_ERROR_MS = 3500
_CHIP_PULSE_MS = 40
_CHIP_PULSE_PERIOD_MS = 1100


class _ChipBridge(QObject):
    level = Signal(float)
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
        self._suppressed = False       # hidden over a fullscreen window
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
        self._fixable = False
        self._bridge.state.connect(self._apply_state)
        self._bridge.level.connect(self._apply_level)
        self._level = 0.0
        bus.subscribe("dictation.state", self._on_state)
        bus.subscribe("dictation.level", self._on_level)

    def _on_state(self, state: str, detail: str = "",
                  fixable: bool = False, **_: object) -> None:
        self._fixable = bool(fixable)
        self._bridge.state.emit(state, detail)

    def _on_level(self, level: float = 0.0, **_: object) -> None:
        # Published from the AUDIO thread – hand over to the GUI thread.
        self._bridge.level.emit(float(level))

    def _apply_level(self, level: float) -> None:
        if self._state != "recording":
            return
        self._level = max(0.0, min(1.0, level))
        self.update()

    def _apply_state(self, state: str, detail: str) -> None:
        self._error_timer.stop()
        self._state = state
        self._detail = detail
        if self._suppressed and state != "idle":
            self._stop_pulse()
            return                     # a fullscreen window is in front
        if state == "recording":
            self._start_pulse()
        else:
            self._stop_pulse()
        if state in ("recording", "transcribing", "reselect"):
            self._update_geometry()
            self.show()
            self._to_front()
            self.update()
        elif state in ("error", "warn"):
            # "warn" is a one-off note (e.g. a very quiet microphone): shown
            # like an error but worded as advice, and it disappears by itself.
            self._update_geometry()
            self.show()
            self._to_front()
            self.update()
            if not (state == "error" and getattr(self, "_fixable", False)):
                self._error_timer.start()
            # A configuration error stays: it will still be broken in four
            # seconds, and the message is the only pointer to the fix.
        else:  # idle
            self.hide()

    def _to_front(self) -> None:
        """Put the chip in front of the other always-on-top windows.

        Shown with WA_ShowWithoutActivating, which deliberately does NOT change
        the stacking position – so the chip reappeared wherever it had been
        pushed to, i.e. behind the dictation window.  A status chip that is
        covered is the same as no chip at all."""
        self.raise_()

    def register_with_coordinator(self) -> None:
        """Let the core's overlay coordinator keep this chip in front, the same
        way it does for the sticky-keys and macro chips.  It also hides the
        chip while a fullscreen window is in front.  Guarded: on an older core
        the chip simply keeps the one-off raise above."""
        try:
            from withease.gui.widgets.cursor_indicator import (
                IndicatorCoordinator)
            IndicatorCoordinator.get().register_suppressible(self)
        except Exception:
            pass

    def set_suppressed(self, suppressed: bool) -> None:
        """Required by the coordinator: hide over a fullscreen window."""
        self._suppressed = bool(suppressed)
        if self._suppressed:
            self.hide()
        elif self._state != "idle":
            self._update_geometry()
            self.show()
            self._to_front()

    def set_chip_scale(self, scale: float) -> None:
        """Scale the chip relative to its default height (label/hint sizes and
        widths all derive from ``_chip_h``, so this resizes the whole chip)."""
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 1.0
        self._chip_h = max(_CHIP_MIN_H,
                           min(_CHIP_MAX_H, round(_CHIP_DEFAULT_H * scale)))
        if self._state != "idle":
            self._update_geometry()
            self.update()

    def _clear_error(self) -> None:
        if self._state in ("error", "warn"):
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
        if self._state == "warn":
            return f"💡 {self._detail}"
        if self._state == "reselect":
            return f"🎯 {_t('chip.reselect')}"
        return ""

    def _subtitle(self) -> str:
        """A second, smaller line shown *under* the chip (e.g. how to pick)."""
        if self._state == "reselect":
            return _t("chip.reselect.hint")
        if self._state == "error" and getattr(self, "_fixable", False):
            return _t("chip.error.fix")
        return ""

    def mousePressEvent(self, event: object) -> None:  # noqa: N802
        """Clicking a configuration error opens the dictation settings."""
        if self._state == "error" and getattr(self, "_fixable", False):
            bus.publish("app.open_settings", module_id="dictation")
            self._state = "idle"
            self.hide()
            return
        super().mousePressEvent(event)

    def _text_w(self, text: str, px: int, bold: bool = True) -> int:
        from PySide6.QtGui import QFontMetrics
        font = self.font()
        font.setPixelSize(px)
        font.setBold(bold)
        return QFontMetrics(font).horizontalAdvance(text)

    def _label_px(self) -> int:
        return max(10, int(self._chip_h * 0.5))

    def _sub_px(self) -> int:
        return max(9, int(self._chip_h * 0.46))

    def _sub_h(self) -> int:
        """Height of the hint pill – always enough for its own font."""
        from PySide6.QtGui import QFontMetrics
        font = self.font()
        font.setPixelSize(self._sub_px())
        return QFontMetrics(font).height() + 6      # + a little breathing room

    def _chip_w(self) -> int:
        return self._text_w(self._label(), self._label_px()) + 28

    def _content_w(self) -> int:
        w = self._chip_w()
        sub = self._subtitle()
        if sub:
            w = max(w, self._text_w(sub, self._sub_px(), bold=False) + 24)
        return w

    def _update_geometry(self) -> None:
        content_w = self._content_w()
        total_h = self._chip_h
        if self._subtitle():
            total_h += _CHIP_SUB_GAP + self._sub_h()
        self.setFixedSize(content_w + 2 * _CHIP_MARGIN,
                          total_h + 2 * _CHIP_MARGIN)
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

        content_w = self._content_w()
        chip_w = self._chip_w()
        chip_x = _CHIP_MARGIN + (content_w - chip_w) // 2

        path = QPainterPath()
        path.addRoundedRect(chip_x, _CHIP_MARGIN, chip_w,
                            self._chip_h, _CHIP_RADIUS, _CHIP_RADIUS)
        p.fillPath(path, QColor(_CHIP_COLORS.get(self._state, "#444444")))

        p.setPen(QColor(_CHIP_FG))
        font = p.font()
        font.setPixelSize(self._label_px())
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRect(chip_x, _CHIP_MARGIN, chip_w, self._chip_h),
                   Qt.AlignmentFlag.AlignCenter, self._label())

        if self._state == "recording":
            # A slim level bar along the bottom edge of the chip: nearly full
            # means "loud and clear", a stub means the microphone is barely
            # picking you up.  Inside the chip, so it costs no extra space.
            bar_h = max(2, round(self._chip_h * 0.09))
            bar_y = _CHIP_MARGIN + self._chip_h - bar_h - 2
            inset = 8
            track = QRect(chip_x + inset, bar_y, chip_w - 2 * inset, bar_h)
            p.fillRect(track, QColor(255, 255, 255, 60))
            level = getattr(self, "_level", 0.0)
            if level > 0:
                lit = QRect(track.x(), track.y(),
                            max(1, round(track.width() * level)), bar_h)
                # Amber below the level at which Whisper starts inventing text.
                weak = level < 0.12
                p.fillRect(lit, QColor(255, 205, 120) if weak
                           else QColor(255, 255, 255, 235))
        sub = self._subtitle()
        if sub:
            # Its own dark, semi-transparent pill so the hint is readable over
            # any desktop background (the widget itself is transparent).
            sub_y = _CHIP_MARGIN + self._chip_h + _CHIP_SUB_GAP
            sub_path = QPainterPath()
            sub_h = self._sub_h()
            sub_path.addRoundedRect(_CHIP_MARGIN, sub_y, content_w, sub_h,
                                    _CHIP_RADIUS, _CHIP_RADIUS)
            p.fillPath(sub_path, QColor(0, 0, 0, 190))
            p.setPen(QColor(_CHIP_FG))
            sfont = p.font()
            sfont.setPixelSize(self._sub_px())
            sfont.setBold(False)
            p.setFont(sfont)
            p.drawText(QRect(_CHIP_MARGIN, sub_y, content_w, sub_h),
                       Qt.AlignmentFlag.AlignCenter, sub)
        p.end()


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

class _TestBridge(QObject):
    finished = Signal(bool, str)   # ok, text-or-error


class _InstallBridge(QObject):
    finished = Signal(bool, str)   # ok, error text
    progress = Signal(str)         # setup stage id (frozen local-runtime setup)


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
        self._install_bridge.progress.connect(self._on_install_progress)
        self._deps_bridge = _InstallBridge()
        self._deps_bridge.finished.connect(self._on_deps_install_finished)
        # Model loading answers from a worker thread – same bridge pattern.
        self._model_bridge = _InstallBridge()
        self._model_bridge.finished.connect(self._on_model_loaded)
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
        # Never scroll sideways (same rule as the core pages, see
        # MainWindow._scrollable): with a horizontal scrollbar the page could
        # end up scrolled right, and then the cards' left edge disappeared
        # behind the sidebar.  Vertical scrolling is unaffected.
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll = scroll          # so expanding a section can scroll to it
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

        # Heading → separator → description, like the core module pages.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        desc = QLabel(_t("description.long"))
        desc.setStyleSheet(_hint_style())
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # What is still missing before a single word can be dictated.  A
        # freshly installed module has no key and no recogniser, and NOTHING on
        # a page of fifteen settings said which two of them matter first.
        self._setup_note = QLabel("")
        self._setup_note.setWordWrap(True)
        self._setup_note.setVisible(False)
        layout.addWidget(self._setup_note)

        self._deps_box = self._build_deps_box()
        layout.addWidget(self._deps_box)
        self._deps_box.setVisible(not audio_available())

        # Every settings card/section is collected here so the whole block can
        # be greyed out while the module is off (like the Mouse/Keyboard pages).
        self._sections: list[QWidget] = []

        def _group(title: str, icon: str = "") -> QFormLayout:
            # Use the app's card helper: it gives a BOLD title label inside the
            # card (Qt ignores font-weight on a QGroupBox::title, so the group
            # headings looked non-bold).  Consistent with the General page.
            from withease.gui.ui_utils import card as _card
            card_w, body = _card(title, icon)
            f = QFormLayout()
            f.setSpacing(10)
            f.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            body.addLayout(f)
            layout.addWidget(card_w)
            self._sections.append(card_w)
            return f

        def _group_foldable(title: str, title_open: str,
                            icon: str = "") -> QFormLayout:
            """Like _group(), but the card starts collapsed.

            Used for the cards that are not needed to get dictation running –
            this page showed 23 controls at once while every other module page
            shows 4-7, which is exactly the overload the collapsible pattern
            exists to prevent."""
            sec = _Collapsible(title, title_open, icon=icon)
            f = QFormLayout()
            f.setSpacing(10)
            f.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            sec.content_body().addLayout(f)
            sec.toggled.connect(lambda on, x=sec: self._reveal_section(x, on))
            layout.addWidget(sec)
            self._sections.append(sec)
            return f

        from withease.gui.ui_utils import em
        label_with_hint = _label_with_hint

        # -- (1) Grundeinstellungen ------------------------------------
        basics = _group(_t("group.basics"), "⚙️")

        self._hotkey = HotkeyEdit(self._settings.get("hotkey", ""),
                                  action_id="dictation.toggle")
        self._hotkey.key_changed.connect(lambda k: self._save("hotkey", k))
        self._hotkey.key_changed.connect(
            lambda _k: self._refresh_setup_note())
        basics.addRow(_t("hotkey"), self._hotkey)

        self._mode = QComboBox()
        self._mode.addItem(_t("mode.toggle"), "toggle")
        self._mode.setItemData(0, _t("mode.toggle.hint"),
                               Qt.ItemDataRole.ToolTipRole)
        self._mode.addItem(_t("mode.hold"), "hold")
        self._mode.setItemData(1, _t("mode.hold.hint"),
                               Qt.ItemDataRole.ToolTipRole)
        if self._settings.get("mode", "toggle") == "hold":
            self._mode.setCurrentIndex(1)
        self._mode.currentIndexChanged.connect(
            lambda i: self._save("mode", self._mode.itemData(i)))
        basics.addRow(
            label_with_hint(_t("mode"), _t("mode.hint")),
            self._mode)

        # Flags in front of the names, exactly like the Allgemein page's
        # language box – same setting, same picture.  A language without a flag
        # PNG simply gets no icon instead of a placeholder, so the list stays
        # usable while more flags are added.
        _LANG_COUNTRY = {"de": "de", "en": "gb", "fr": "fr", "es": "es",
                         "it": "it", "nl": "nl", "pl": "pl", "pt": "pt",
                         "ru": "ru", "tr": "tr", "uk": "ua", "zh": "cn",
                         "ja": "jp"}
        self._lang = QComboBox()
        self._lang.setIconSize(QSize(em(1.1), round(em(1.1) * 2 / 3)))
        for code in LANGUAGES:
            label = _t("lang.auto") if code == "auto" else _t(f"lang.{code}")
            icon = QIcon()
            country = _LANG_COUNTRY.get(code)
            if country:
                try:
                    from withease.core import resources as _res
                    path = _res.flag_icon_path(country)
                    if path.exists():
                        icon = QIcon(str(path))
                except Exception:
                    pass
            self._lang.addItem(icon, label, code)
        saved_lang = self._settings.get("language", "auto")
        if saved_lang in LANGUAGES:
            self._lang.setCurrentIndex(LANGUAGES.index(saved_lang))
        self._lang.currentIndexChanged.connect(
            lambda i: self._save("language", self._lang.itemData(i)))
        basics.addRow(_t("language"), self._lang)

        # Size of the status chip (recording indicator + target-app hint) shown
        # at the top of the screen – some users want it bigger/more visible.
        # In actual pixels (like the shared Sticky-Keys/macro chip size on the
        # Allgemein page) instead of named sizes, plus a button to copy that
        # page's value directly instead of having to go check it first.  Same
        # range as the central spin box (see _CHIP_MIN_H/_CHIP_MAX_H) so that
        # button can transfer any value unchanged.
        self._chip_size = QSpinBox()
        self._chip_size.setRange(_CHIP_MIN_H, _CHIP_MAX_H)
        self._chip_size.setSuffix(" px")
        saved_scale = float(self._settings.get("chip_scale", 1.0))
        self._chip_size.setValue(round(saved_scale * _CHIP_DEFAULT_H))
        self._chip_size.valueChanged.connect(self._on_chip_size_px_changed)
        # A real icon (not a text glyph) so it grows with the font-size
        # setting instead of staying visually tiny – setIconSize must be set
        # explicitly, otherwise the icon stays at Qt's small default even
        # though the button box grows.  A refresh glyph (not a down-arrow):
        # the button re-reads the value from the Allgemein page, so "update
        # from there" reads truer than "download".
        self._chip_sync_btn = QPushButton()
        self._chip_sync_btn.setIconSize(QSize(em(1.2), em(1.2)))
        self._chip_sync_btn.setFixedSize(em(2), em(2))
        self._chip_sync_btn.setToolTip(_wrap_tip(_t("chip_size.sync")))
        self._chip_sync_btn.setAccessibleName(_t("chip_size.sync"))
        self._chip_sync_btn.clicked.connect(self._on_chip_size_sync)
        self._chip_size.valueChanged.connect(
            lambda _v: self._update_chip_sync_btn())
        self._update_chip_sync_btn()
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        chip_row.addWidget(self._chip_size)
        chip_row.addWidget(self._chip_sync_btn)
        chip_row.addStretch(1)
        basics.addRow(label_with_hint(_t("chip_size"), _t("chip_size.hint")),
                     chip_row)

        # -- (2) Spracherkennung ---------------------------------------
        rec = _group(_t("group.recognition"), "🎙️")
        self._form_rec = rec

        self._backend = QComboBox()
        self._backend.addItem(_t("backend.cloud"), "cloud")
        # The provider list used to be spelled out in the label itself, which
        # made this the widest control in the app – at a large font size it no
        # longer fitted its card and the text was cut off.  It belongs in a
        # tooltip anyway: the actual provider is picked in "Anbieter" below.
        self._backend.setItemData(0, _t("backend.cloud.hint"),
                                  Qt.ItemDataRole.ToolTipRole)
        local_label = _t("backend.local")
        if not local_recognition_ready():
            local_label += f" ({_t('backend.local.missing')})"
        self._backend.addItem(local_label, "local")
        saved_backend = self._settings.get("backend", "local")
        if saved_backend == "live":          # retire an old "live" selection
            saved_backend = "local"
        idx = self._backend.findData(saved_backend)
        if idx >= 0:
            self._backend.setCurrentIndex(idx)
        self._backend.currentIndexChanged.connect(self._on_backend_changed)
        self._backend.currentIndexChanged.connect(
            lambda _i: self._refresh_setup_note())
        # Visible, never behind a hover: this is the one setting that decides
        # whether the user's voice leaves this PC.
        rec.addRow(_t("backend"), self._backend)
        rec.addRow("", _setting_note(_t("backend.hint")))

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

        # Pause playing media (music/video) while the mic is live, resume when
        # the take is fully done – sits right under the microphone it relates to.
        self._pause_media_cb = QCheckBox(_t("pause_media"))
        self._pause_media_cb.setChecked(
            bool(self._settings.get("pause_media", False)))
        self._pause_media_cb.toggled.connect(
            lambda v: self._save("pause_media", v))
        from withease.gui.widgets.hint_icon import HintIcon
        pause_media_row = QHBoxLayout()
        pause_media_row.setContentsMargins(0, 0, 0, 0)
        pause_media_row.setSpacing(6)
        pause_media_row.addWidget(self._pause_media_cb)
        pause_media_row.addWidget(HintIcon(_t("pause_media.hint")))
        pause_media_row.addStretch(1)
        rec.addRow("", pause_media_row)

        # Cloud fields
        self._provider = QComboBox()
        for pid in PROVIDERS:
            self._provider.addItem(_t(f"provider.{pid}"), pid)
            if pid == "custom":
                _option_hint(self._provider, self._provider.count() - 1,
                             _t("provider.custom.hint"))
        saved_provider = self._settings.get("provider", "openrouter")
        ids = list(PROVIDERS.keys())
        if saved_provider in ids:
            self._provider.setCurrentIndex(ids.index(saved_provider))
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        self._provider.currentIndexChanged.connect(
            lambda _i: self._refresh_setup_note())
        rec.addRow(_t("provider"), self._provider)

        self._base_url = QLineEdit(self._settings.get("base_url", ""))
        self._base_url.setPlaceholderText("https://.../v1")
        self._base_url.setMinimumWidth(em(11))
        self._base_url.editingFinished.connect(
            lambda: self._save("base_url", self._base_url.text().strip()))
        rec.addRow(_t("base_url"), self._base_url)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setMinimumWidth(em(11))
        self._api_key.setText(self._module.get_api_key(saved_provider))
        self._api_key.editingFinished.connect(self._on_api_key_changed)
        self._api_key.editingFinished.connect(self._refresh_setup_note)
        # Where the key ends up (unencrypted, device-wide) has to be readable
        # BEFORE it is typed in, not on hover afterwards.
        rec.addRow(_t("api_key"), self._api_key)
        self._api_key_note = _setting_note(_t("api_key.hint"))
        rec.addRow("", self._api_key_note)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._fill_models(saved_provider)
        saved_model = self._settings.get("model", "")
        if saved_model:
            self._model.setEditText(saved_model)
        self._model.currentTextChanged.connect(
            lambda t: self._save("model", t.strip()))
        rec.addRow(
            label_with_hint(_t("model"), _t("model.hint")),
            self._model)

        # Local fields
        self._local_model = QComboBox()
        for m in LOCAL_MODELS:
            self._local_model.addItem(m, m)
        saved_local = self._settings.get("local_model", "base")
        if saved_local in LOCAL_MODELS:
            self._local_model.setCurrentIndex(LOCAL_MODELS.index(saved_local))
        self._local_model.currentIndexChanged.connect(
            lambda i: self._save("local_model", self._local_model.itemData(i)))
        from withease.gui.ui_utils import wrap_tooltip
        self._model_load_btn = QPushButton(_t("local_model.load"))
        self._model_load_btn.setToolTip(wrap_tooltip(_t("local_model.load.hint")))
        self._model_load_btn.clicked.connect(self._on_load_model)
        self._model_status = QLabel("")
        self._model_status.setStyleSheet(_hint_style())
        self._model_status.setWordWrap(True)
        self._model_status.setVisible(False)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        model_row.addWidget(self._local_model)
        model_row.addWidget(self._model_load_btn)
        model_row.addStretch(1)
        # A one-off download of up to 1.5 GB is not something to discover by
        # hovering – it belongs next to the choice that triggers it.
        rec.addRow(_t("local_model"), model_row)
        self._local_model_note = _setting_note(_t("local.hint"))
        rec.addRow("", self._local_model_note)
        rec.addRow("", self._model_status)
        # Changing the model means the next dictation would silently download
        # it – say so, right where the choice was made.
        self._local_model.currentIndexChanged.connect(
            lambda _i: self._note_model_change())

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
        # Always available: the source build installs via pip, the packaged .exe
        # sets up a dedicated local runtime (see local_runtime / _on_install_local).
        install_btns.addWidget(self._install_btn)
        howto_btn = QPushButton(_t("local.howto"))
        howto_btn.clicked.connect(self._on_show_howto)
        # The manual how-to is command-line pip, which does not apply in the
        # packaged .exe – there the automatic button is the only path.
        # Only ever HIDE explicitly – see the deps-box button above for why.
        install_btns.addWidget(howto_btn)
        if self._frozen:
            howto_btn.setVisible(False)
        install_btns.addStretch()
        install_layout.addLayout(install_btns)
        self._install_status = QLabel("")
        self._install_status.setWordWrap(True)
        self._install_status.setVisible(False)
        install_layout.addWidget(self._install_status)
        rec.addRow("", self._install_box)
        self._update_install_note()

        self._test_btn = QPushButton(_t("test"))
        self._test_btn.clicked.connect(self._on_test)
        rec.addRow("", self._test_btn)

        # -- (3) Textausgabe -------------------------------------------
        out = _group_foldable(_t("group.output"),
                              _t("group.output.open"), "📋")

        self._output_mode = QComboBox()
        self._output_mode.addItem(_t("output.window"), "window")
        self._output_mode.addItem(_t("output.direct"), "direct")
        _option_hint(self._output_mode, 0, _t("output.window.hint"))
        if self._settings.get("output_mode", "window") == "direct":
            self._output_mode.setCurrentIndex(1)
        self._output_mode.currentIndexChanged.connect(
            lambda i: self._save("output_mode", self._output_mode.itemData(i)))
        # Decides the whole workflow, and is met once while setting up.
        out.addRow(_t("output"), self._output_mode)
        out.addRow("", _setting_note(_t("output.hint")))

        self._insert = QComboBox()
        self._insert.addItem(_t("insert.clipboard"), "clipboard")
        self._insert.addItem(_t("insert.type"), "type")
        for i, key in enumerate(("clipboard", "type")):
            _option_hint(self._insert, i, _t(f"insert.{key}.hint"))
        if self._settings.get("insert_method", "clipboard") == "type":
            self._insert.setCurrentIndex(1)
        self._insert.currentIndexChanged.connect(
            lambda i: self._save("insert_method", self._insert.itemData(i)))
        out.addRow(
            label_with_hint(_t("insert"), _t("insert.hint")),
            self._insert)

        self._keep_clipboard = QCheckBox(_t("keep_clipboard"))
        self._keep_clipboard.setChecked(
            bool(self._settings.get("keep_in_clipboard", False)))
        self._keep_clipboard.toggled.connect(
            lambda v: self._save("keep_in_clipboard", v))
        out.addRow("", self._keep_clipboard)

        self._join_cb = QCheckBox(_t("join_dictations"))
        self._join_cb.setChecked(
            bool(self._settings.get("join_dictations", True)))
        self._join_cb.toggled.connect(
            lambda v: self._save("join_dictations", v))
        out.addRow("", _checkbox_with_hint(
            self._join_cb, _t("join_dictations.hint")))

        self._take_sel_cb = QCheckBox(_t("take_selection"))
        self._take_sel_cb.setChecked(
            bool(self._settings.get("take_selection", False)))
        self._take_sel_cb.toggled.connect(
            lambda v: self._save("take_selection", v))
        out.addRow("", _checkbox_with_hint(
            self._take_sel_cb, _t("take_selection.hint")))

        # -- (4) Woerterbuch -------------------------------------------
        # One full-width row (the card is already titled "Wörterbuch", so no
        # extra label column – that duplicated the heading and, being top-
        # aligned against the tall buttons, sat above the word-count line).
        # Everything is vertically centred so the count and buttons align.
        # Two columns like every other row: the word count on the left, the
        # two buttons left-aligned in the field column – one consistent picture.
        vocab = _group_foldable(_t("group.vocab"),
                                _t("group.vocab.open"), "📖")
        # One always-visible line saying what the dictionary is FOR; the
        # long how-to stays in the tooltip.  Full-width row, so it reads
        # like the description under a card heading.
        vocab_desc = QLabel(_t("group.vocab.desc"))
        vocab_desc.setStyleSheet(_hint_style())
        vocab_desc.setWordWrap(True)
        vocab.addRow(vocab_desc)
        self._dict_summary = QLabel(self._dict_summary_text())
        self._dict_summary.setStyleSheet(_hint_style())
        from withease.gui.ui_utils import wrap_tooltip
        self._dict_summary.setToolTip(wrap_tooltip(_t("vocab.hint")))
        self._dict_summary.setAlignment(Qt.AlignmentFlag.AlignLeft
                                        | Qt.AlignmentFlag.AlignVCenter)
        dict_row = QHBoxLayout()
        dict_learn = QPushButton(_t("glossary.learn"))
        dict_learn.clicked.connect(self._open_learn_text)
        dict_row.addWidget(dict_learn)
        dict_edit = QPushButton(_t("edit"))
        dict_edit.clicked.connect(self._open_dictionary)
        dict_row.addWidget(dict_edit)
        dict_row.addStretch(1)
        # Match the label height to the buttons so the count sits on their line.
        self._dict_summary.setMinimumHeight(dict_learn.sizeHint().height())
        vocab.addRow(self._dict_summary, dict_row)

        # -- (5) KI (folded away while unused) -------------------------
        # A chevron section (▸/▾) like "Erweitert" below – not a checkable box,
        # so it never looks like an on/off toggle and shows no stale "expand"
        # hint once it is open.
        ki_section = _Collapsible(
            _t("group.ai"), _t("group.ai.open"), icon="🤖")
        ai = QFormLayout()
        ki_section.content_body().addLayout(ai)
        ai.setSpacing(8)
        ai.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form_ai = ai
        # Always start collapsed (default), so the page stays calm – the KI
        # options are opened on demand and not remembered as open.
        ki_section.toggled.connect(
            lambda on, s=ki_section: self._reveal_section(s, on))
        layout.addWidget(ki_section)
        self._sections.append(ki_section)

        # Button on the "KI-Aktionen" row; its long description sits on its own
        # full-width row below so it wraps freely instead of being clipped.
        aiact_row = QHBoxLayout()
        aiact_btn = QPushButton(_t("edit"))
        aiact_btn.clicked.connect(self._open_ai_actions)
        aiact_row.addWidget(aiact_btn)      # left-aligned in the field column
        aiact_row.addStretch(1)
        ai.addRow(label_with_hint(_t("ai.actions"), _t("ai.actions.hint")),
                 aiact_row)

        # Text blocks live with the MACROS – one list, not two.  Keeping a
        # second list here meant the same sign-off had to be created twice and
        # neither list showed the other's entries, which is confusing in
        # exactly the place where people go looking for it.  So this row is a
        # signpost, not an editor.
        snip_row = QHBoxLayout()
        snip_row.setContentsMargins(0, 0, 0, 0)
        snip_row.setSpacing(8)
        self._snip_goto = QPushButton(_t("snippets.goto"))
        self._snip_goto.clicked.connect(self._open_macros)
        snip_row.addWidget(self._snip_goto)
        self._snip_move = QPushButton("")
        self._snip_move.clicked.connect(self._move_snippets_to_macros)
        snip_row.addWidget(self._snip_move)
        snip_row.addStretch(1)
        ai.addRow(_t("snippets"), snip_row)
        ai.addRow("", _setting_note(_t("snippets.note")))
        self._update_snippet_row()

        self._ai_enable = QCheckBox(_t("ai.enable"))
        self._ai_enable.setChecked(bool(self._settings.get("ai_cleanup", False)))
        self._ai_enable.toggled.connect(lambda v: self._save("ai_cleanup", v))
        self._ai_enable.toggled.connect(lambda _v: self._update_ai_rows())
        ai.addRow(label_with_hint(_t("ai"), _t("ai.hint")), self._ai_enable)

        self._ai_backend = QComboBox()
        self._ai_backend.addItem(_t("ai.ollama"), "ollama")
        self._ai_backend.addItem(_t("ai.lmstudio"), "lmstudio")
        self._ai_backend.addItem(_t("ai.cloud"), "cloud")
        for i, key in enumerate(("ollama", "lmstudio", "cloud")):
            _option_hint(self._ai_backend, i, _t(f"ai.{key}.hint"))
        saved_ai_backend = self._settings.get("ai_backend", "ollama")
        if saved_ai_backend == "local":          # legacy value → Ollama
            saved_ai_backend = "ollama"
        bidx = self._ai_backend.findData(saved_ai_backend)
        if bidx >= 0:
            self._ai_backend.setCurrentIndex(bidx)
        self._ai_backend.currentIndexChanged.connect(self._on_ai_backend_changed)
        ai.addRow(
            label_with_hint(_t("ai.backend"), _t("ai.backend.hint")),
            self._ai_backend)
        self._ai_backend_label = ai.labelForField(self._ai_backend)

        # Model as an editable dropdown, populated from the running local
        # provider (Ollama / LM Studio); still free-text for the cloud backend.
        self._ai_model = QComboBox()
        self._ai_model.setEditable(True)
        self._ai_model.setMinimumWidth(em(10))
        from withease.gui.ui_utils import wrap_tooltip
        self._ai_model.setToolTip(wrap_tooltip(_t("ai.model.hint")))
        saved_ai_model = self._settings.get("ai_model", "")
        if saved_ai_model:
            self._ai_model.setEditText(saved_ai_model)
        self._ai_model.currentTextChanged.connect(
            lambda t: self._save("ai_model", t.strip()))
        # A Unicode glyph ("↻") in a fixed-width button rendered inconsistently
        # across fonts/sizes – a drawn icon is crisp at any font size.
        self._ai_model_refresh = QPushButton()
        self._ai_model_refresh.setIcon(
            _core_theme.refresh_icon(_core_theme.action_color()))
        self._ai_model_refresh.setFixedSize(em(2), em(2))
        self._ai_model_refresh.setIconSize(QSize(em(1.2), em(1.2)))
        self._ai_model_refresh.setToolTip(
            _wrap_tip(_t("ai.model.refresh.hint")))
        self._ai_model_refresh.setAccessibleName(_t("ai.model.refresh.hint"))
        self._ai_model_refresh.clicked.connect(self._refresh_ai_models)
        self._ai_model.setMinimumWidth(em(10))
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
            _t("group.advanced"), _t("group.advanced.open"), icon="🔧")
        adv = QFormLayout()
        adv_section.content_body().addLayout(adv)
        adv.setSpacing(8)
        adv.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form_adv = adv
        adv_section.toggled.connect(
            lambda on, s=adv_section: self._reveal_section(s, on))
        layout.addWidget(adv_section)
        self._sections.append(adv_section)

        self._command_hotkey = HotkeyEdit(
            self._settings.get("command_hotkey", ""),
            action_id="dictation.command")
        self._command_hotkey.key_changed.connect(
            lambda k: self._save("command_hotkey", k))
        adv.addRow(label_with_hint(_t("hotkey.command"),
                                   _t("hotkey.command.hint")),
                  self._command_hotkey)

        self._max_seconds = QSpinBox()
        self._max_seconds.setRange(0, 3600)     # 0 = endless (no auto-stop)
        self._max_seconds.setSuffix(" s")
        self._max_seconds.setSpecialValueText(_t("max_seconds.off"))
        self._max_seconds.setValue(int(self._settings.get("max_seconds", 0)))
        self._max_seconds.valueChanged.connect(
            lambda v: self._save("max_seconds", v))
        adv.addRow(_t("max_seconds"), self._max_seconds)

        self._hall_filter = QComboBox()
        for level in ("off", "normal", "strong"):
            self._hall_filter.addItem(_t(f"hallucination.{level}"), level)
            if level == "normal":
                _option_hint(self._hall_filter,
                             self._hall_filter.count() - 1,
                             _t("hallucination.normal.hint"))
        saved_hall = self._settings.get("hallucination_filter", "strong")
        hi = self._hall_filter.findData(saved_hall)
        self._hall_filter.setCurrentIndex(hi if hi >= 0 else 2)
        self._hall_filter.currentIndexChanged.connect(
            lambda i: self._save("hallucination_filter",
                                 self._hall_filter.itemData(i)))
        adv.addRow(label_with_hint(_t("hallucination"), _t("hallucination.hint")),
                  self._hall_filter)

        self._preload_cb = QCheckBox(_t("preload"))
        self._preload_cb.setChecked(
            bool(self._settings.get("preload_model", False)))
        from withease.gui.ui_utils import wrap_tooltip
        self._preload_cb.setToolTip(wrap_tooltip(_t("preload.hint")))
        self._preload_cb.toggled.connect(
            lambda v: self._save("preload_model", v))
        adv.addRow("", self._preload_cb)
        self._update_preload_row()

        self._raw_cb = QCheckBox(_t("raw"))
        self._raw_cb.setChecked(
            bool(self._settings.get("raw_recognition", False)))
        self._raw_cb.toggled.connect(
            lambda v: self._save("raw_recognition", v))
        raw_row = QHBoxLayout()
        raw_row.setContentsMargins(0, 0, 0, 0)
        raw_row.setSpacing(6)
        raw_row.addWidget(self._raw_cb)
        raw_row.addWidget(HintIcon(_t("raw.hint")))
        raw_row.addStretch(1)
        adv.addRow("", raw_row)

        self._dates_cb = QCheckBox(_t("numeric_dates"))
        self._dates_cb.setChecked(
            bool(self._settings.get("numeric_dates", True)))
        self._dates_cb.toggled.connect(
            lambda v: self._save("numeric_dates", v))
        adv.addRow("", _checkbox_with_hint(self._dates_cb,
                                           _t("numeric_dates.hint")))

        # -- (7) Deine Daten -------------------------------------------
        # One place that says what this module keeps about the user, how much
        # of it there is, and lets every piece be removed.  Until now the
        # history could not be cleared at all and the training recordings grew
        # without limit, unseen (1868 files / 1.4 GB on the author's machine).
        data = _group_foldable(_t("group.data"), _t("group.data.open"), "🗄️")
        data_desc = QLabel(_t("data.desc"))
        data_desc.setStyleSheet(_hint_style())
        data_desc.setWordWrap(True)
        data.addRow(data_desc)

        def _data_row(label_key: str, hint_key: str, value_label: QLabel,
                      on_delete) -> None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            value_label.setStyleSheet(_hint_style())
            row.addWidget(value_label, 1)
            btn = QPushButton(_t("data.delete"))
            _mark_danger(btn)
            btn.clicked.connect(on_delete)
            row.addWidget(btn)
            data.addRow(label_with_hint(_t(label_key), _t(hint_key)), row)

        self._data_history = QLabel("")
        _data_row("data.history", "data.history.hint", self._data_history,
                  self._on_clear_history)

        self._history_limit = QSpinBox()
        self._history_limit.setRange(0, 100)
        self._history_limit.setValue(
            int(self._settings.get("history_limit", 20)))
        self._history_limit.valueChanged.connect(
            lambda v: self._save("history_limit", v))
        data.addRow(_t("data.history.limit"), self._history_limit)

        # Storing recordings is gone: what it wrote was the audio plus
        # Whisper's OWN output, which teaches a model nothing, and the switch
        # for it had been lost in 49ee0b3 while the writing carried on.  Only
        # the clean-up is left – shown while a leftover folder from an older
        # version still exists, and gone for good once it is emptied.
        self._data_training = QLabel("")
        if self._module.training_stats()[0]:
            _data_row("data.training", "data.training.hint",
                      self._data_training, self._on_clear_training)

        self._data_dict = QLabel("")
        self._data_dict.setStyleSheet(_hint_style())
        data.addRow(label_with_hint(_t("data.dictionary"),
                                    _t("data.dictionary.hint")),
                   self._data_dict)

        self._data_key = QLabel("")
        _data_row("data.key", "data.key.hint", self._data_key,
                  self._on_clear_api_key)

        self._refresh_data_stats()

        layout.addStretch()
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._on_backend_changed(self._backend.currentIndex())
        self._refresh_setup_note()
        self._update_ai_rows()
        self._update_enabled_state(self._module.enabled)

    # ------------------------------------------------------------------

    def _save(self, key: str, value: Any) -> None:
        self._settings[key] = value
        self._module.on_settings_changed()

    def _on_chip_size_px_changed(self, px: int) -> None:
        scale = px / _CHIP_DEFAULT_H
        self._save("chip_scale", scale)
        ind = getattr(self._module, "_indicator", None)
        if ind is not None:                      # live-preview the new size
            ind.set_chip_scale(scale)

    def _update_chip_sync_btn(self) -> None:
        """Colour the sync button only while it would actually change the value.

        A permanently blue action icon reads as "press me"; if pressing it does
        nothing because the size already matches the Allgemein page, that is a
        promise the button cannot keep.  Greyed out and disabled it says
        "already in sync" without needing a word of explanation."""
        from withease.core import config as app_config
        from withease.gui.ui_utils import wrap_tooltip
        btn = getattr(self, "_chip_sync_btn", None)
        if btn is None:
            return
        central = int(app_config.load_app_config().get("overlay_chip_size", 28))
        differs = self._chip_size.value() != central
        btn.setIcon(_core_theme.refresh_icon(
            _core_theme.action_color() if differs else _core_theme.hint_color()))
        btn.setEnabled(differs)
        btn.setToolTip(wrap_tooltip(
            _t("chip_size.sync") if differs else _t("chip_size.sync.same")))

    def _on_chip_size_sync(self) -> None:
        """Copy the shared Sticky-Keys/macro chip size from the Allgemein
        page – QSpinBox.setValue clamps to our own (narrower) range and
        fires valueChanged, so this reuses the normal save/preview path."""
        from withease.core import config as app_config
        central = int(app_config.load_app_config().get("overlay_chip_size", 28))
        self._chip_size.setValue(central)

    def _reveal_section(self, section: QWidget, opened: bool) -> None:
        """When a collapsible section (KI/Erweitert) is opened, scroll it into
        view so its freshly revealed content is visible.  Deferred so the layout
        has already grown before we scroll."""
        if not opened:
            return

        def do_scroll() -> None:
            import shiboken6
            if (not shiboken6.isValid(section)
                    or not shiboken6.isValid(self._scroll)):
                return
            self._scroll.ensureWidgetVisible(section, 0, 0)

        QTimer.singleShot(0, do_scroll)

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
        self._ai_model.setToolTip(_wrap_tip(
            _t("ai.model.hint") if models else _t("ai.model.none")))

    # -- "what is still missing" -----------------------------------------

    def _missing_steps(self) -> list[str]:
        """The steps left before dictation can work at all, in order."""
        steps: list[str] = []
        if not (self._settings.get("hotkey") or "").strip():
            steps.append(_t("setup.hotkey"))
        backend = self._backend.currentData() if hasattr(self, "_backend")             else self._settings.get("backend", "local")
        if backend == "local":
            if not local_recognition_ready():
                steps.append(_t("setup.local"))
        else:
            # The key of the PROVIDER that is actually selected – see
            # DictationModule.stored_api_keys() for why not _settings.
            provider = (self._provider.currentData()
                        if hasattr(self, "_provider") else "")
            if not self._module.get_api_key(provider).strip():
                steps.append(_t("setup.key"))
            if (self._provider.currentData() == "custom"
                    and not (self._settings.get("base_url") or "").strip()):
                steps.append(_t("setup.url"))
        return steps

    def _refresh_setup_note(self) -> None:
        """Show the remaining steps – and nothing once there are none."""
        note = getattr(self, "_setup_note", None)
        if note is None:
            return
        steps = self._missing_steps()
        if not steps:
            note.setVisible(False)
            return
        numbered = "  ".join(f"{i}. {t}" for i, t in enumerate(steps, 1))
        # The test button is always the last step: it is the only way to find
        # out whether the setup actually took.
        numbered += f"  {len(steps) + 1}. {_t('setup.test')}"
        note.setText(f"{_t('setup.todo')}  {numbered}")
        note.setStyleSheet(_warn_style())
        note.setVisible(True)

    # -- "your data" section ---------------------------------------------

    @staticmethod
    def _human_size(num: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if num < 1024 or unit == "GB":
                return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} GB"

    def _refresh_data_stats(self) -> None:
        """Fill in how much of each kind of data is currently stored."""
        if not hasattr(self, "_data_history"):
            return
        self._data_history.setText(
            _t("data.history.value", n=str(self._module.history_count())))
        count, size = self._module.training_stats()
        self._data_training.setText(
            _t("data.training.value", n=str(count),
               size=self._human_size(size)))
        self._data_dict.setText(
            _t("data.dictionary.value", n=str(self._dict_entry_count())))
        self._data_key.setText(_t("data.key.set") if self._module.has_api_key()
                               else _t("data.key.unset"))

    def _dict_entry_count(self) -> int:
        try:
            return self._module.dictionary_count()
        except Exception:
            return 0

    def _confirm(self, text: str) -> bool:
        return QMessageBox.question(
            self, _t("group.data").lstrip("▸▾ "), text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    # Delete first, offer to undo – see widgets/undo_bar.py for why that is
    # kinder than asking first.  Where the core is too old to have the bar,
    # _show_undo returns False and the old confirmation is used instead: no
    # path may ever delete with no way back.

    def _on_clear_history(self) -> None:
        n = self._module.history_count()
        if not n:
            self._data_history.setText(_t("data.nothing"))
            return
        if not _undo_possible():
            if not self._confirm(_t("data.confirm.history", n=str(n))):
                return
        removed = self._module.clear_history()
        self._refresh_data_stats()

        def undo() -> None:
            self._module.restore_history(removed)
            self._refresh_data_stats()

        _show_undo(self, _t("undo.history", n=str(n)), undo)

    def _on_clear_training(self) -> None:
        count, size = self._module.training_stats()
        if not count:
            self._data_training.setText(_t("data.nothing"))
            return
        if not _undo_possible():
            if not self._confirm(_t("data.confirm.training", n=str(count),
                                    size=self._human_size(size))):
                return
        moved = self._module.clear_training_data()
        self._refresh_data_stats()
        if not moved:
            return                       # nothing was moved – nothing to undo

        def undo(path: str = moved) -> None:
            if self._module.restore_training_data(path):
                self._refresh_data_stats()

        if _show_undo(self, _t("undo.recordings", n=str(count)), undo):
            # Only once the offer has expired do the files really go.
            QTimer.singleShot(
                _UNDO_PURGE_MS,
                lambda p=moved: self._module.purge_training_data(p))
        else:
            self._module.purge_training_data(moved)

    def _on_clear_api_key(self) -> None:
        if not self._module.has_api_key():
            self._data_key.setText(_t("data.nothing"))
            return
        if not _undo_possible():
            if not self._confirm(_t("data.confirm.key")):
                return
        old = self._module.clear_api_key()
        self._api_key.setText("")
        self._refresh_data_stats()
        self._refresh_setup_note()

        def undo() -> None:
            self._module.restore_api_keys(old)
            provider = self._provider.currentData()
            self._api_key.setText(self._module.get_api_key(provider))
            self._refresh_data_stats()
            self._refresh_setup_note()

        _show_undo(self, _t("undo.api_key"), undo)

    def _note_model_change(self) -> None:
        self._model_status.setText(_t("local_model.changed"))
        self._model_status.setStyleSheet(_hint_style())
        self._model_status.setVisible(True)

    def _on_load_model(self) -> None:
        self._model_load_btn.setEnabled(False)
        self._model_status.setText(_t("local_model.loading"))
        self._model_status.setStyleSheet(_hint_style())
        self._model_status.setVisible(True)
        self._module.load_model_now(self._model_bridge.finished.emit)

    def _on_model_loaded(self, ok: bool, err: str) -> None:
        self._model_load_btn.setEnabled(True)
        if ok:
            self._model_status.setText(_t("local_model.ready"))
            self._model_status.setStyleSheet(_hint_style())
        else:
            self._model_status.setText(_t("local_model.failed", err=err))
            self._model_status.setStyleSheet(_warn_style())

    def _update_snippet_row(self) -> None:
        """Show the "move them over" button only while there is still an old
        list to move – afterwards there is one place and nothing to explain."""
        left = len(self._module.snippets_raw())
        self._snip_move.setVisible(bool(left))
        if left:
            self._snip_move.setText(_t("snippets.move", n=str(left)))

    def _open_macros(self) -> None:
        """Send the user to the macros page – where text blocks are edited."""
        bus.publish("app.open_settings", module_id="macros")

    def _move_snippets_to_macros(self) -> None:
        """Hand this module's remaining text blocks to the macros module.

        Over the bus, like the read side: neither module imports the other, and
        if the macros module is not running nothing happens and the old entries
        simply stay where they are."""
        items = self._module.snippets_raw()
        if not items:
            return
        moved = []
        for item in items:
            name = str(item.get("name", "")).strip()
            text = str(item.get("prompt", ""))
            if not name or not text:
                continue
            out: list = []
            try:
                bus.publish("macros.add_text_block", name=name, text=text,
                            out=out)
            except Exception:
                break
            moved.append(item)
        if not moved:
            self._snip_move.setText(_t("snippets.move.failed"))
            return
        rest = [i for i in items if i not in moved]
        self._module.save_snippets(rest)
        self._update_snippet_row()
        self._open_macros()

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
        for widget in (self._provider, self._api_key, self._api_key_note,
                       self._model):
            self._form_rec.setRowVisible(widget, cloud)
        # The model row's field is a layout (combo + "Jetzt laden"), so the
        # row is addressed through the combo; its note is its own row.
        self._form_rec.setRowVisible(self._local_model, not cloud)
        self._form_rec.setRowVisible(self._local_model_note, not cloud)
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
        gpu = _has_nvidia_gpu()
        if local_recognition_ready():
            self._install_note.setStyleSheet(_hint_style())
            self._install_note.setText(
                _t("local.ready_gpu") if gpu else _t("local.ready"))
            self._install_btn.setText(
                _t("local.install.gpu") if gpu else _t("local.install"))
        elif self._frozen:
            # In the .exe the first setup downloads a small dedicated runtime.
            self._install_note.setStyleSheet(_hint_style())
            self._install_note.setText(_t("local.frozen_note"))
            self._install_btn.setText(
                _t("local.install.gpu") if gpu else _t("local.install"))
        else:
            self._install_note.setStyleSheet(_warn_style())
            self._install_note.setText(_t("local.not_installed"))
            self._install_btn.setText(_t("local.install"))

    def _set_install_status(self, text: str, style: str | None = None) -> None:
        """Set the install-progress note, hiding it entirely when empty so it
        doesn't reserve a blank line of height above the Test button."""
        if style is not None:
            self._install_status.setStyleSheet(style)
        self._install_status.setText(text)
        self._install_status.setVisible(bool(text))

    def _on_install_local(self) -> None:
        self._install_btn.setEnabled(False)
        self._set_install_status(_t("local.install.running"), _hint_style())

        import subprocess
        import sys

        gpu = _has_nvidia_gpu()

        # Packaged .exe: the frozen interpreter has no pip, so set up a dedicated
        # local runtime (downloads uv + a small CPython + faster-whisper).
        if self._frozen:
            def run_frozen() -> None:
                try:
                    import local_runtime
                    local_runtime.provision(
                        self._install_bridge.progress.emit, with_gpu=gpu)
                    self._install_bridge.finished.emit(True, "")
                except Exception as exc:
                    self._install_bridge.finished.emit(False, str(exc)[:400])
            threading.Thread(target=run_frozen, daemon=True,
                             name="dictation-localrt").start()
            return

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

    def _on_install_progress(self, stage: str) -> None:
        """Show which setup step is running (frozen local-runtime bootstrap)."""
        key = {"uv": "local.setup.uv",
               "python": "local.setup.python",
               "packages": "local.setup.packages"}.get(stage)
        if key:
            self._set_install_status(_t(key), _hint_style())

    def _on_install_finished(self, ok: bool, err: str) -> None:
        self._install_btn.setEnabled(True)
        if ok and local_recognition_ready():
            self._set_install_status("")
            self._backend.setItemText(1, _t("backend.local"))
            self._update_install_note()          # box stays visible
            QMessageBox.information(self, _t("local.install"),
                                    _t("local.install.done"))
        else:
            self._set_install_status(
                _t("local.install.failed", err=err), _warn_style())
        self._refresh_setup_note()      # a finished install clears a step

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
        btns.addWidget(self._deps_install_btn)
        # Only ever HIDE explicitly.  setVisible(True) here would pop the
        # button up as its own top-level window for a moment: addWidget on a
        # layout that is not itself attached to a widget yet does not reparent,
        # so the button is still parentless at this point.  Left alone it
        # simply becomes visible together with its parent.
        if getattr(_sys, "frozen", False):
            self._deps_install_btn.setVisible(False)
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
        # Grey out every settings card/section while the module is off (so all
        # labels, hints and inputs dim together, like the Mouse/Keyboard pages);
        # the enable-checkbox and module description above stay active.
        for sec in getattr(self, "_sections", ()):
            sec.setEnabled(enabled)


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
        # One-shot "capture" requests from the core (e.g. the settings
        # search box): the result is published on the bus instead of
        # being typed into the focused application.
        self._capture_token = ""
        self._active_trigger = ""       # which key started it (for hold mode)
        self._state = "idle"            # idle | recording | transcribing
        self._state_lock = threading.Lock()
        self._media_paused = False      # we paused media playback for this take
        self._media_lock = threading.Lock()
        self._media_paused_apps: list[str] = []   # SMTC app ids we paused
        self._media_key_active = False            # fallback media-key was sent
        self._media_pause_thread: threading.Thread | None = None
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
        # What THIS module last typed straight into another app, and
        # where.  Used to continue a sentence correctly (see
        # _insert_text): in direct mode we cannot read the target's
        # text, but we do know what we ourselves put there last.
        self._last_direct_text: str = ""
        self._last_direct_hwnd: int = 0
        self._last_direct_at: float = 0.0
        self._reselecting = False        # waiting for the user to pick a target
        self._reselect_timer: Any = None  # QTimer restoring the UI after a pick
        self._error_memory: Any = None   # ErrorMemory (lazy, from settings)

        # Re-theme the dictation window when the app switches light<->dark while
        # it is open (Qt caches palette() colours baked into stylesheet strings).
        bus.subscribe("theme.changed", self._on_theme_changed)
        bus.subscribe("dictation.capture_request", self._on_capture_request)
        bus.subscribe("dictation.capture_stop", self._on_capture_stop)

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
        backend = self._settings.get("backend", "local")
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
            self._indicator.register_with_coordinator()
            self._indicator.set_chip_scale(
                float(self._settings.get("chip_scale", 1.0)))

    # ------------------------------------------------------------------
    # Dictation window (output mode = "window")
    # ------------------------------------------------------------------

    def _window_mode(self) -> bool:
        # A one-shot capture (settings search) never uses the dictation
        # window: the text goes straight back to whoever asked for it, so
        # popping the window open would be pure noise.  Guarding here covers
        # every window path at once (open on record, route the transcript,
        # live mode) instead of one guard per call site.
        if self._capture_token:
            return False
        return self._settings.get("output_mode", "window") == "window"

    def _on_capture_request(self, token: str = "", **_kw: Any) -> None:
        """Record once and hand the text back over the bus (see
        `dictation.capture_result`).  Used by the settings search so the user
        can speak the term instead of typing it."""
        if not self.enabled or not token:
            return
        if self._state != "idle" or self._live_active:
            bus.publish("dictation.capture_result", token=token, text="")
            return
        self._capture_token = token
        self._active_mode = "capture"
        threading.Thread(target=self._start_recording, daemon=True).start()

    def _on_capture_stop(self, token: str = "", **_kw: Any) -> None:
        """Stop a running capture (the search's microphone button was pressed
        a second time).  Same path the hotkey takes, so the transcript still
        goes back over the bus."""
        if not token or token != self._capture_token:
            return
        if self._state == "recording":
            threading.Thread(target=self._stop_and_transcribe,
                             daemon=True).start()

    def _finish_capture(self, text: str) -> str:
        """Answer a pending capture request (see _on_capture_request).

        Always called on every path out of _stop_and_transcribe, including the
        "too short" and error paths – otherwise the requester would wait for a
        reply that never comes."""
        if not self._capture_token:
            return ""
        token, self._capture_token = self._capture_token, ""
        self._active_mode = "auto"
        # A search term is not a sentence: the recogniser (and our own
        # sentence post-processing) ends it with "." / "?" / "!", which would
        # be searched for literally and match nothing.
        text = (text or "").strip().rstrip(".!?,;: ").strip()
        bus.publish("dictation.capture_result", token=token, text=text)
        return token

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
                    on_lookup_snippet=self.lookup_snippet,
                    history_visible=bool(
                        self._settings.get("history_visible", False)),
                    ai_visible=bool(
                        self._settings.get("ai_panel_visible", True)),
                    geometry=self._settings.get("win_geo"),
                    history=list(self._settings.get("history", []))[
                        :max(0, int(self._settings.get("history_limit", 20)))],
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
        """Persist the dictation history across sessions (newest first).

        Capped by the user's own limit: dictated text is kept in plain text in
        the profile, so "how much of it is kept" has to be their decision – 0
        means nothing is stored at all."""
        limit = int(self._settings.get("history_limit", 20))
        self._settings["history"] = list(items)[:max(0, limit)]
        self.on_settings_changed()

    # ------------------------------------------------------------------
    # Error memory ("Fehler-Gedächtnis") – self-learning corrections
    # ------------------------------------------------------------------

    def _memory(self) -> Any:
        if self._error_memory is None:
            from correction import ErrorMemory
            self._error_memory = ErrorMemory(self._settings.get("error_memory"))
        return self._error_memory

    def _learn_correction(self, old: str, new: str) -> str:
        """Called by the window whenever a word was corrected.

        Returns which stage the correction reached, so the window can say so
        instead of leaving the rule invisible:

        ``"always"``     – confirmed twice, applied from now on in every case
        ``"uncertain"``  – noted; applied where recognition is unsure
        ``""``           – not learnable (a phrase, or a word under 3 letters)
        """
        mem = self._memory()
        before = mem.strength(old)
        mem.learn(old, new)
        after = mem.strength(old)
        self._settings["error_memory"] = mem.to_dict()
        self.on_settings_changed()
        if after <= before:
            return ""                    # the memory refused it
        return "always" if after >= 2 else "uncertain"

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
        # While the correction sub-window is open, a spoken correction fills its
        # field – we are NOT choosing a paste target, so keep the current one
        # (otherwise the correction window itself would become the target and the
        # user would have to reselect their app afterwards).
        if self._window is not None and self._window.is_correcting():
            return
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
        """Enter target-app selection: hide our window, show a hint chip, and
        wait for the user to switch to the wanted app and press Space (the hook
        confirms; Escape cancels).  Called on the GUI thread (button/command)."""
        if self._reselecting:
            return
        self._reselecting = True
        # Get our own window out of the way so it can't be picked as the target
        # and doesn't cover the app the user wants to switch to.
        if self._window is not None:
            self._window.set_reselecting(True)
            self._window.request_hide()
        # A standalone chip explains the mode + that Space selects (the window is
        # hidden, so this is the only visible cue).
        bus.publish("dictation.state", state="reselect")
        from PySide6.QtCore import QTimer
        if self._reselect_timer is None:
            self._reselect_timer = QTimer()
            self._reselect_timer.setInterval(150)
            self._reselect_timer.timeout.connect(self._reselect_poll)
        self._reselect_timer.start()

    def confirm_reselect_target(self) -> None:
        """Called from the hook when the user presses Space: remember whatever
        app is in front now as the paste target.  The poll then restores the UI."""
        try:
            import ctypes
            cur = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            cur = 0
        if cur and cur != self._window_hwnd:
            self._target_hwnd = cur
        self._reselecting = False

    def _reselect_poll(self) -> None:
        # Runs on the GUI thread; the hook flips ``_reselecting`` to False on
        # Space (confirm, target already captured) or Escape (cancel).
        if self._reselecting:
            return
        if self._reselect_timer is not None:
            self._reselect_timer.stop()
        bus.publish("dictation.state", state="idle")     # hide the chip
        if self._window is not None:
            self._window.set_reselecting(False)
            self._window.set_target(self._target_name())
            # The user's next step is dictating, so bring our window back.
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
        if is_altgr_fake_lctrl(vk, scan):
            return False

        hold_mode = self._settings.get("mode", "toggle") == "hold"

        if is_press:
            if self._reselecting:
                if vk == 0x1B:              # Escape cancels (keep current target)
                    self._reselecting = False   # poll (GUI thread) restores UI
                    return True
                if vk == 0x20:              # Space picks the app in front now
                    self.confirm_reselect_target()   # poll (GUI thread) restores
                    return True
                # Swallow our own trigger keys so no recording starts while the
                # user is picking a target app; let everything else pass through.
                picking = current_combo_str(vk)
                if picking == self._trigger or (
                        self._command_trigger
                        and picking == self._command_trigger):
                    return True
                return False
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
        prev = self._state
        self._state = state
        # Optionally pause media while the mic is live and resume it once we are
        # fully done.  Media stays paused through "transcribing" (the AI is still
        # working on the audio) and only resumes on the return to "idle".
        if state == "recording" and prev == "idle":
            self._pause_media_if_enabled()
        elif state == "idle" and prev != "idle":
            self._resume_media_if_paused()
        if state in ("recording", "transcribing") and not detail:
            detail = self._mode_label()
        bus.publish("dictation.state", state=state, detail=detail)
        if self._window is not None:
            self._window.set_state(state, detail)

    def _pause_media_if_enabled(self) -> None:
        """Pause every *currently playing* media session while dictating.

        Uses SMTC so several players (Spotify + YouTube …) are all paused, and
        remembers exactly which ones so only those are resumed.  Runs in a
        background thread so it never delays the start of recording.  If SMTC is
        unavailable it falls back to the media Play/Pause key, and only when
        audio is actually playing (so a paused player is never toggled on)."""
        if self._media_paused or not self._settings.get("pause_media", False):
            return
        self._media_paused = True
        with self._media_lock:
            self._media_paused_apps = []
            self._media_key_active = False

        def work() -> None:
            apps = _smtc_pause_playing()
            if apps is None:                       # SMTC unavailable → fallback
                if _system_audio_playing() is not False:
                    _send_media_play_pause()
                    with self._media_lock:
                        self._media_key_active = True
            else:
                with self._media_lock:
                    self._media_paused_apps = apps

        t = threading.Thread(target=work, daemon=True,
                             name="dictation-media-pause")
        with self._media_lock:
            self._media_pause_thread = t
        t.start()

    def _resume_media_if_paused(self) -> None:
        """Resume exactly the players we paused (or undo the fallback key).

        Driven by our own state, not the current setting, so turning the option
        off mid-take still restores playback.  Waits for the pause to finish
        first, so a very short dictation can't resume before we know what to."""
        if not self._media_paused:
            return
        self._media_paused = False
        with self._media_lock:
            pause_thread = self._media_pause_thread

        def work() -> None:
            if pause_thread is not None:
                pause_thread.join(timeout=8)
            with self._media_lock:
                apps = list(self._media_paused_apps)
                self._media_paused_apps = []
                key = self._media_key_active
                self._media_key_active = False
            if apps:
                _smtc_play_apps(apps)
            if key:
                _send_media_play_pause()

        threading.Thread(target=work, daemon=True,
                         name="dictation-media-resume").start()

    def _error(self, detail: str, fixable: bool = False) -> None:
        """Report a failure.  ``fixable`` marks a CONFIGURATION problem.

        A missing API key or an uninstalled recogniser is not a passing glitch
        – it stays broken until someone changes a setting.  Such a message
        must therefore not delete itself after a few seconds, and clicking it
        opens the page where it can be fixed."""
        _log.error("dictation error: %s", detail)
        self._set_state("idle")
        bus.publish("dictation.state", state="error", detail=detail,
                    fixable=fixable)
        if self._window is not None:
            self._window.set_state("error")

    # ------------------------------------------------------------------
    # Recording (sounddevice)
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        if self._window_mode():
            self._capture_target()          # remember the app to paste into
            selected = ""
            if bool(self._settings.get("take_selection", False)):
                # Bring a selection from the target app into the window, so an
                # existing sentence can be edited or continued by voice instead
                # of being retyped.  Off by default on purpose – see the
                # setting's own explanation.
                try:
                    selected = self._copy_selection_from_target()
                except Exception:
                    _log.exception("could not read the target's selection")
                    selected = ""
            if self._window is not None:
                self._window.request_open()  # show the window (thread-safe)
                if selected:
                    self._window.take_selected_text(selected)
        with self._state_lock:
            if self._state != "idle":
                return
            try:
                import sounddevice as sd
            except Exception:
                self._error(_t("err.no_audio_lib"), fixable=True)
                return

            self._audio_chunks = []

            self._live_level = 0.0
            self._level_sent = 0.0

            def callback(indata, _frames, _time, _status) -> None:
                block = bytes(indata)
                self._audio_chunks.append(block)
                # Publish the input level a few times a second.  Seeing that
                # the microphone actually picks you up WHILE you speak is the
                # thing a distant microphone makes impossible to judge – and
                # far more useful than finding out afterwards.
                if audioop is None:
                    return
                try:
                    peak = audioop.max(block, 2) / 32768.0
                except Exception:
                    return
                self._live_level = max(self._live_level * 0.6, peak)
                now = time.monotonic()
                if now - self._level_sent >= 0.1:
                    self._level_sent = now
                    bus.publish("dictation.level", level=self._live_level)

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

    # The stop key is pressed AFTER the last word, so the tail of every
    # recording holds the click of that very key press – exactly the kind of
    # short noise burst Whisper turns into an invented word.  Cutting it is
    # cheap and removes the cause instead of filtering the symptom.
    _TAIL_TRIM_S = 0.25

    def _measure_level(self, raw: bytes, width: int = 2) -> float:
        """Peak level of the recording as a 0..1 fraction of full scale.

        Used only for the "microphone very quiet" hint – a quiet, far-field
        recording is the situation in which Whisper hallucinates most."""
        if audioop is None or not raw:
            return -1.0
        try:
            return audioop.max(raw, width) / 32768.0
        except Exception:
            return -1.0

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
        rate = getattr(self, "_rec_rate", _SAMPLE_RATE)
        channels = getattr(self, "_rec_channels", _CHANNELS)
        self._last_level = self._measure_level(raw)
        # Drop the final quarter second (the stop key's own click).  Never cut
        # into a recording that is barely longer than the trim itself.
        frame = 2 * channels
        cut = int(self._TAIL_TRIM_S * rate) * frame
        if cut and len(raw) > cut * 3:
            raw = raw[:-cut]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(raw)
        return buf.getvalue()

    def _abort_recording(self) -> None:
        with self._state_lock:
            if self._state != "recording":
                return
            self._close_stream()
            self._set_state("idle")

    # Below this peak level a recording is quiet enough that Whisper starts
    # inventing text; measured as a fraction of full scale.
    _QUIET_LEVEL = 0.12

    def _maybe_warn_quiet_mic(self) -> None:
        """Say ONCE that the microphone is very quiet.

        A far-field microphone (webcam, notebook lid) is the single biggest
        cause of invented words, and it is invisible from the settings: nothing
        looks wrong, the text is just occasionally made up.  Saying it once,
        with what to do about it, saves guessing.  Never repeated – the flag is
        stored with the settings."""
        level = getattr(self, "_last_level", -1.0)
        if level < 0 or level >= self._QUIET_LEVEL:
            return
        if self._settings.get("quiet_mic_warned"):
            return
        self._settings["quiet_mic_warned"] = True
        self.on_settings_changed()
        # Shown through the status chip the module already owns, not through a
        # dialog: the recognition is still running and must not be interrupted.
        _log.info("dictation: quiet microphone (peak %.2f)", level)
        bus.publish("dictation.state", state="warn", detail=_t("mic.quiet"))

    def _stop_and_transcribe(self) -> None:
        with self._state_lock:
            if self._state != "recording":
                return
            wav = self._close_stream()
            duration = time.monotonic() - self._record_started
            if duration < 0.4 or len(wav) < 8000:
                self._set_state("idle")  # too short to contain speech
                self._say_nothing_heard("short")
                self._finish_capture("")
                return
            self._set_state("transcribing")
        self._maybe_warn_quiet_mic()
        try:
            text = self.transcribe(wav)
        except Exception as exc:
            self._error(str(exc)[:120], fixable=isinstance(exc, ConfigError))
            self._finish_capture("")
            return
        text = (text or "").strip()
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
                from postprocess import (fix_casing, fix_dates,
                                         fix_question_marks)
                text = fix_casing(text)          # undo stray capitalisation
                text = fix_question_marks(text)
                if self._settings.get("numeric_dates", True):
                    # "20. August 2026" → "20.08.2026": people SAY a date the
                    # long way and want to READ it short.
                    text = fix_dates(text)
        self._set_state("idle")
        if self._capture_token:
            # One-shot capture (settings search): answer the requester and
            # never type into whatever app happens to be focused.
            self._finish_capture(text)
            return
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
        else:
            # Never end in silence.  An empty result is indistinguishable from
            # "the program is broken" unless it says something, and with a
            # far-field microphone it is the most common outcome of all.
            self._say_nothing_heard("empty")

    def _say_nothing_heard(self, why: str) -> None:
        """Tell the user that nothing came of that recording, and why."""
        if self._capture_token:
            return                      # the requester handles its own feedback
        if why == "short":
            detail = _t("nothing.short")
        elif getattr(self, "_last_level", 1.0) < self._QUIET_LEVEL:
            detail = _t("nothing.quiet")
        else:
            detail = _t("nothing.heard")
        bus.publish("dictation.state", state="warn", detail=detail)

    # -- what this module stores about the user --------------------------

    def training_stats(self) -> tuple[int, int]:
        """``(recordings, bytes)`` currently stored as training data.

        Written for every dictation while the option is on, with no limit and –
        until now – no way to see or remove them.  1 868 recordings / 1.4 GB is
        not a hypothetical number."""
        folder = self._training_dir()
        count = total = 0
        for root, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
                if name.lower().endswith(".wav"):
                    count += 1
        return count, total

    def clear_training_data(self) -> str:
        """Put every stored recording aside.  Returns the folder it was moved
        to, or "" if there was nothing (or the move failed).

        Moved, not deleted: renaming a folder is instant even for gigabytes,
        and it is what makes "Rückgängig" possible at all.  purge_training_
        data() is what finally removes it, once the undo offer has expired."""
        import datetime
        folder = self._training_dir()
        if not os.path.isdir(folder):
            return ""
        aside = f"{folder}.geloescht-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        try:
            os.rename(folder, aside)
        except OSError:
            _log.exception("could not move the recordings aside")
            return ""
        return aside

    def restore_training_data(self, aside: str) -> bool:
        """Move a set-aside recordings folder back."""
        folder = self._training_dir()
        if not aside or not os.path.isdir(aside) or os.path.exists(folder):
            return False
        try:
            os.rename(aside, folder)
            return True
        except OSError:
            _log.exception("could not move the recordings back")
            return False

    def purge_training_data(self, aside: str) -> None:
        """Finally remove a set-aside folder (the undo offer has expired)."""
        import shutil
        if aside and os.path.isdir(aside):
            shutil.rmtree(aside, ignore_errors=True)

    def history_count(self) -> int:
        return len(self._settings.get("history", []) or [])

    def clear_history(self) -> list:
        """Forget every stored dictation (they are plain text in the profile).

        Returns the removed entries so they can be put back – deleting first
        and offering to undo beats asking first (see widgets/undo_bar.py)."""
        old = list(self._settings.get("history", []) or [])
        self._settings["history"] = []
        self.on_settings_changed()
        if self._window is not None:
            try:
                self._window.clear_history_now()
            except Exception:
                pass
        return old

    def restore_history(self, entries: list) -> None:
        self._settings["history"] = list(entries)
        self.on_settings_changed()
        if self._window is not None:
            try:
                self._window.reload_history(list(entries))
            except Exception:
                pass

    def stored_api_keys(self) -> dict:
        """Every provider key currently on this device.

        The keys live in the app config (``dictation_api_keys``), written by
        set_api_key() – NOT in ``self._settings["api_key"]``, which nothing has
        written since the cloud fields were reworked.  Reading the wrong place
        made "Deine Daten" report "keiner gespeichert" while a key sat in
        app.json, and made its delete button a no-op."""
        cfg = app_config.load_app_config()
        return {p: k for p, k in (cfg.get("dictation_api_keys", {}) or {}).items()
                if (k or "").strip()}

    def has_api_key(self) -> bool:
        return bool(self.stored_api_keys())

    def clear_api_key(self) -> dict:
        """Remove every stored provider key.  Returns them so the caller can
        put them back (see the undo bar)."""
        old = self.stored_api_keys()
        cfg = app_config.load_app_config()
        cfg["dictation_api_keys"] = {}
        app_config.save_app_config(cfg)
        self._settings["api_key"] = ""       # retire the stale legacy field
        self.on_settings_changed()
        return old

    def restore_api_keys(self, keys: dict) -> None:
        if not keys:
            return
        cfg = app_config.load_app_config()
        cfg.setdefault("dictation_api_keys", {}).update(keys)
        app_config.save_app_config(cfg)
        self.on_settings_changed()

    def _training_dir(self) -> str:
        return os.path.join(str(app_config.CONFIG_DIR), "dictation_training")

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
            self._error(_t("err.no_audio_lib"), fixable=True)
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
        if self._settings.get("backend", "local") == "local":
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
        # The NAMES of the text blocks, so Whisper hears "Grußformel" instead
        # of the far more common word "Großformel".  Biasing the recogniser is
        # the real cure; the fuzzy match in lookup_snippet is only the net
        # underneath it.
        words += [name for name, _text in self._all_text_blocks()]
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
            raise ConfigError(_t("err.no_url"))
        if not api_key:
            raise ConfigError(_t("err.no_key"))

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
                raise ConfigError(_t("err.no_local"))
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

    def load_model_now(self, on_done) -> None:
        """Download/load the configured local model in the background.

        Switching the model in the dropdown only stores a name – the multi-
        hundred-megabyte file is fetched lazily on the next dictation, where
        the only feedback is the chip saying "Erkenne Text …" for minutes.
        This makes that step explicit and visible instead.
        ``on_done(ok, error)`` is called from the worker thread."""
        def run() -> None:
            try:
                self._ensure_model_loaded()
                on_done(True, "")
            except Exception as exc:
                _log.exception("model load failed")
                on_done(False, str(exc)[:200])

        threading.Thread(target=run, daemon=True).start()

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

    def _local_in_process(self) -> bool:
        """True when faster-whisper can be imported in THIS process (source
        build).  In the packaged .exe it cannot – there we transcribe through the
        out-of-process local runtime instead (see _transcribe_local_via_worker)."""
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None

    def _transcribe_local_via_worker(self, wav_bytes: bytes, *,
                                     live: bool = False) -> str:
        """Local transcription for the packaged .exe: run faster-whisper in the
        dedicated local runtime (localrt) via the isolated worker process,
        because the frozen interpreter has no faster-whisper of its own."""
        import local_runtime
        if not local_runtime.runtime_ready():
            raise ConfigError(_t("err.no_local"))
        model = (self._live_model_name() if live
                 else self._settings.get("local_model", "base"))
        threads = max(1, (os.cpu_count() or 4) // 2)
        language = self._local_language()
        prompt = (None if live
                  else (self._initial_prompt() if language == "de" else None))
        hotwords = self._hotwords() or None
        hall = self._hall_params(wav_bytes, live=live)
        text, low = self._whisper_proc.transcribe(
            wav_bytes, model=model, threads=threads, language=language,
            hotwords=hotwords, initial_prompt=prompt, live=live, hall=hall)
        self._last_low_words = low
        return self._postprocess_asr(text)

    def _hall_params(self, wav_bytes: bytes, *, live: bool = False) -> dict:
        """Hallucination-filter settings for THIS recording.

        See _effective_hall_level: a short command must not be judged by rules
        written for the tail of a long dictation."""
        level = _effective_hall_level(
            self._settings.get("hallucination_filter", "strong"),
            is_command=(self._active_mode == "command" and not live),
            seconds=_clip_seconds(wav_bytes))
        return _hallucination_params(level)

    def _transcribe_local(self, wav_bytes: bytes, *, live: bool = False) -> str:
        # Packaged .exe (no in-process faster-whisper): use the local runtime.
        if not self._local_in_process():
            return self._transcribe_local_via_worker(wav_bytes, live=live)
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
        params = self._hall_params(wav_bytes, live=live)
        # hallucination_silence_threshold skips silent gaps where Whisper invents
        # text; only used for the batch path (the live polish keeps its own).
        hall_sil = None if live else params.get("hall_sil")
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
                hallucination_silence_threshold=hall_sil,
                word_timestamps=True,   # per-word probs → confidence heatmap
            )
            seg_list = list(segments)   # iterating drives the actual inference
            # Segment-level filter: live keeps its own conservative rule; batch
            # uses the user-set hallucination filter (also on the last segment).
            kept = []
            for idx, seg in enumerate(seg_list):
                ns = getattr(seg, "no_speech_prob", 0.0)
                lp = getattr(seg, "avg_logprob", 0.0)
                is_last = idx == len(seg_list) - 1
                if live:
                    if ns > 0.6 and lp < -1.0:
                        continue
                elif _seg_is_hallucination(ns, lp, is_last, params):
                    continue
                kept.append(seg)
            # Word-level trailing trim (batch only): cut invented words tacked
            # onto the end of the last real segment.
            trimmed_last: str | None = None
            if not live and kept:
                words = getattr(kept[-1], "words", None) or []
                n = _trailing_trim_count(words, params)
                if words and n >= len(words):
                    kept = kept[:-1]
                elif n:
                    trimmed_last = "".join(
                        getattr(w, "word", "") for w in words[:len(words) - n]
                    ).strip()
            parts, low = [], []
            for i, seg in enumerate(kept):
                last = i == len(kept) - 1
                parts.append(trimmed_last if (last and trimmed_last is not None)
                             else seg.text.strip())
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

    def snippets_raw(self) -> list[dict]:
        """The saved text blocks as editable dicts (for the settings editor)."""
        return [dict(a) for a in self._settings.get("snippets", [])]

    def save_snippets(self, items: list[dict]) -> None:
        self._settings["snippets"] = [
            {"name": str(a.get("name", "")).strip(),
             "prompt": str(a.get("prompt", "")).strip()}
            for a in items
            if str(a.get("name", "")).strip() and str(a.get("prompt", "")).strip()]
        self.on_settings_changed()

    def macro_text_blocks(self) -> list[tuple[str, str]]:
        """Text macros from the macros module, asked for over the bus.

        A named piece of text is the same thing whether it is fired by a key in
        macro mode or spoken while dictating, so it should only have to be
        entered once.  The bus is synchronous: publish with an empty list, read
        the answer.  If the macros module is not running the list simply stays
        empty – no import, no dependency."""
        out: list[tuple[str, str]] = []
        try:
            bus.publish("macros.collect_text_blocks", out=out)
        except Exception:
            return []
        return out

    def _all_text_blocks(self) -> list[tuple[str, str]]:
        """Every named text: the macros first, then this module's own list."""
        items = list(self.macro_text_blocks())
        items += [(str(a.get("name", "")), str(a.get("prompt", "")))
                  for a in (self._settings.get("snippets", []) or [])]
        return [(n, t) for n, t in items if n and t]

    def lookup_snippet(self, spoken: str) -> tuple[str | None, list[str]]:
        """Find a text block by its spoken name.

        Looks in the MACROS first (that is where most people already have their
        greeting formula) and then in this module's own list, so the same name
        works in macro mode and while dictating.  Returns ``(text, known
        names)``.  Matched case-, space- and „ß"-insensitively because the name
        arrives from speech recognition, not from typing; a prefix match is
        accepted so "füge Gruß ein" also finds "Grußformel".
        """
        blocks = self._all_text_blocks()
        items = [{"name": n, "prompt": t} for n, t in blocks]
        names = [n for n, _t in blocks]

        def norm(x: str) -> str:
            # Fold everything speech recognition writes inconsistently, so the
            # NAME still matches however it came out: case, „ß“ vs „ss“, and
            # any separator at all.  A hyphen is the important one – a macro
            # called "E-Mail Proton" arrives from Whisper as "E-Mail Proton"
            # but reaches us as "e mail proton", because the command grammar
            # turns punctuation into spaces.  Comparing only letters and
            # digits makes both spellings the same name.
            x = str(x).lower().replace("ß", "ss")
            return "".join(ch for ch in x if ch.isalnum())

        want = norm(spoken)
        if not want:
            return None, names
        for a in items:
            if norm(a.get("name", "")) == want:
                return str(a.get("prompt", "")), names
        for a in items:
            n = norm(a.get("name", ""))
            if n.startswith(want) or want.startswith(n):
                return str(a.get("prompt", "")), names
        # Last resort: allow a couple of wrong letters.  "Grußformel" and
        # "Großformel" differ by ONE character and sound nearly identical, so
        # the recogniser gets it wrong often.  Only accepted when exactly one
        # name is that close – with two near-misses guessing would be worse
        # than saying so.
        from editor_actions import _levenshtein
        tol = 1 if len(want) <= 6 else 2
        close = [a for a in items
                 if _levenshtein(norm(a.get("name", "")), want) <= tol]
        if len(close) == 1:
            return str(close[0].get("prompt", "")), names
        return None, names

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

    _DIRECT_JOIN_MAX_AGE = 15 * 60      # seconds; after that, assume a new text

    def _foreground_hwnd(self) -> int:
        try:
            import ctypes
            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return 0

    def _join_direct(self, text: str) -> str:
        """Prepare `text` to continue what we last typed into THIS window.

        Direct mode cannot read the target application's text, so the previous
        utterance we inserted ourselves is the best available evidence.  Only
        used while the same window is still in front and the last insert was
        recent – otherwise the text is left exactly as spoken."""
        if not bool(self._settings.get("join_dictations", True)):
            return text
        if not self._last_direct_text:
            return text
        if self._foreground_hwnd() != self._last_direct_hwnd:
            return text                     # a different app: start fresh
        if time.monotonic() - self._last_direct_at > self._DIRECT_JOIN_MAX_AGE:
            return text
        from postprocess import join_dictation
        return join_dictation(self._last_direct_text, text)

    def _remember_direct(self, text: str) -> None:
        self._last_direct_text = text
        self._last_direct_hwnd = self._foreground_hwnd()
        self._last_direct_at = time.monotonic()

    def _insert_text(self, text: str) -> None:
        if not PYNPUT_AVAILABLE:
            return
        text = self._join_direct(text)
        method = self._settings.get("insert_method", "clipboard")
        keep = bool(self._settings.get("keep_in_clipboard", False))
        if method == "type":
            KeyController().type(text)
            if keep:
                self._set_clipboard(text)
            self._remember_direct(text)
            return
        self._paste_via_clipboard(text, keep=keep)
        self._remember_direct(text)

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
    def _copy_selection_from_target(cls) -> str:
        """Fetch whatever is selected in the foreground app, via Ctrl+C.

        Returns the selected text, or "" when nothing was selected.  The
        clipboard is put back exactly as it was: an unrelated copy the user
        made earlier must not be destroyed just because a dictation started.

        "Nothing was selected" is detected by the clipboard NOT changing –
        Ctrl+C on an empty selection leaves it alone in every app we care
        about.  Not perfect, but it never invents text.
        """
        get_text, set_text = cls._clipboard_funcs()
        before = get_text()
        # An unlikely marker, NOT a control character: some clipboard
        # consumers truncate at a NUL byte, which would make the probe
        # look like an empty clipboard every time.
        marker = "\u200bwithease-probe\u200b"
        # A marker first, so an unchanged clipboard really means "nothing was
        # copied" instead of "the same text was copied again".
        if not set_text(marker):
            return ""
        ctrl = KeyController()
        ctrl.press(PynputKey.ctrl)
        ctrl.press("c")
        ctrl.release("c")
        ctrl.release(PynputKey.ctrl)
        time.sleep(0.12)
        got = get_text()
        set_text(before if before is not None else "")
        if not got or got == marker:
            return ""
        return got

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
