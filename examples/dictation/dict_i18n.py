"""Translations for the dictation WINDOW and its dialogs.

Why a second table next to the one in module.py: that one belongs to the
settings page, and module.py imports the window – not the other way round.
Putting the window's strings here lets the window and its dialogs translate
themselves without either file importing the other.

The language and the fallback rule are the same as in module.py: whatever the
app is set to, falling back to English for anything missing, and following a
language change at runtime over the bus.
"""
from __future__ import annotations

from withease.core import config as app_config
from withease.core.event_bus import bus


class _Lang:
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
        table = STRINGS.get(self.code, STRINGS["en"])
        text = table.get(key) or STRINGS["en"].get(key) or key
        for placeholder, value in kwargs.items():
            text = text.replace(f"{{{placeholder}}}", value)
        return text


STRINGS: dict[str, dict[str, str]] = {
    "de": {
        # -- main window ------------------------------------------------
        "win.title": "WithEase – Diktieren",
        "win.placeholder": "Hier erscheint dein Diktat. Sprich Text oder Befehle wie „Cursor vor <Wort>“, „markiere <Wort>“ oder „lösche <Wort>“.",
        "win.history": "Verlauf (zum Laden anklicken)",
        "win.history.clear": "🗑 Verlauf löschen",
        "win.undo": "↶ Rückgängig",
        "win.redo": "↷ Wiederholen",
        "win.clear": "🧹 Leeren",
        "win.vocab": "＋ Wörterbuch",
        "win.target": "🎯 Ziel-App wählen",
        "win.commands": "❓ Befehle",
        "win.insert_close": "Einfügen && Schließen",
        "win.insert_keep": "Einfügen && weiter",
        "win.copy": "Kopieren",
        "win.copy_close": "Kopieren && Schließen",
        "win.close": "Schließen",
        "win.ai_toggle": "KI-Aktionen ein-/ausklappen",
        "win.history_toggle": "Verlauf ein-/ausklappen (merkt sich den Zustand für das nächste Mal)",
        "win.no_target": "⚠ Keine Ziel-App gewählt – „Einfügen“ landet nur in der Zwischenablage. Bitte „🎯 Ziel-App wählen“ drücken.",

        # -- status line ------------------------------------------------
        "msg.recognised": "erkannt: „{raw}“   →   {outcome}",
        "msg.as_text": "als Text eingefügt",
        "msg.nothing": "nichts erkannt",
        "msg.no_command": "Befehl nicht erkannt",
        "msg.copied": "Befehl: in die Zwischenablage kopiert",
        "msg.closing": "Befehl: Fenster schließen",
        "msg.cheatsheet": "Befehlsliste geöffnet",
        "msg.retarget": "Ziel-App wird neu gewählt …",
        "msg.spell_mode": "Buchstabiermodus: sprich die Buchstaben",
        "msg.correction_open": "Korrekturfenster geöffnet",
        "msg.to_correction": "→ Korrekturfenster",
        "msg.correction_cancelled": "Korrektur abgebrochen",
        "msg.paste_failed": "konnte nicht einfügen – Text liegt in der Zwischenablage (Strg+V)",
        "msg.pasted": "eingefügt – weiter diktieren",
        "msg.clipboard": "in die Zwischenablage kopiert",
        "msg.cleared": "geleert – im Verlauf gesichert, Strg+Z macht rückgängig",
        "msg.took_selection": "Markierten Text übernommen – weiter diktieren oder bearbeiten",
        "msg.ai_kept": "✓ KI-Ergebnis übernommen – Strg+Z macht rückgängig",
        "msg.ai_no_change": "KI: keine Änderung nötig",
        "msg.history_empty": "Verlauf ist leer",
        "msg.history_already_empty": "Verlauf ist schon leer",
        "msg.history_deleted": "Verlauf gelöscht",
        "msg.history_restored": "Verlauf wiederhergestellt",
        "msg.history_loaded": "aus dem Verlauf geladen",
        "msg.mark_word_first": "Erst ein Wort markieren, dann „＋ Wörterbuch“.",
        "msg.selection_cancelled": "Auswahl abgebrochen (Escape)",
        "msg.picked": "Treffer {n} von {total} gewählt",
        "msg.deleted_count": "{n} Diktate gelöscht.",

        # -- correction dialog -----------------------------------------
        "corr.title": "Korrektur",
        "corr.heard": "Bisher erkannt:",
        "corr.correct": "Richtige Version (tippen oder sprechen):",
        "corr.suggestions": "Vorschläge (anklicken oder „nimm N“ sagen):",
        "corr.help": "Tippe die Korrektur, oder drücke die Diktier-Taste und sprich die richtige Version. Dann „Übernehmen“.",
        "corr.cancel": "Abbrechen",
        "corr.apply": "Übernehmen",

        # -- command list ----------------------------------------------
        "cheat.intro": "Sprich einen dieser Befehle, während das Diktierfenster offen ist. Alles andere wird als Text eingefügt.",
        "cheat.search": "Befehl suchen …",
        "cheat.manual": "Ausführliche Anleitung …",
        "cheat.close": "Schließen",
        "cheat.count": "{n} Befehle",
        "cheat.mic": "Suchbegriff sprechen",
        "cheat.mic.stop": "Aufnahme beenden",

        # -- AI preview -------------------------------------------------
        "ai.highlight": "Änderungen hervorheben",
        "ai.highlight.hint": "Geänderte oder ergänzte Wörter grün markieren",
        "ai.apply": "Übernehmen",
        "ai.result.diff": "Ergebnis der KI-Aktion – grün = geändert oder ergänzt.",
        "ai.result": "Ergebnis der KI-Aktion.",

        # -- dialogs ----------------------------------------------------
        "learn.title": "Aus Text lernen",
        "learn.intro": "Füge einen Text ein (oder lade eine Datei) – WithEase schlägt daraus deine Fachbegriffe/Namen vor. Häkchen setzen und übernehmen; sie landen in „Eigene Wörter“.",
        "learn.placeholder": "Text hier einfügen …",
        "learn.cancel": "Abbrechen",
        "learn.accept": "Ausgewählte übernehmen",
        "learn.none": "Keine Begriffe gefunden.",
    },
    "en": {
        # -- main window ------------------------------------------------
        "win.title": "WithEase – Dictation",
        "win.placeholder": "Your dictation appears here. Speak text, or commands such as „Cursor vor <word>“, „markiere <word>“ or „lösche <word>“.",
        "win.history": "History (click an entry to load it)",
        "win.history.clear": "🗑 Clear history",
        "win.undo": "↶ Undo",
        "win.redo": "↷ Redo",
        "win.clear": "🧹 Empty",
        "win.vocab": "＋ Dictionary",
        "win.target": "🎯 Choose target app",
        "win.commands": "❓ Commands",
        "win.insert_close": "Insert && close",
        "win.insert_keep": "Insert && continue",
        "win.copy": "Copy",
        "win.copy_close": "Copy && close",
        "win.close": "Close",
        "win.ai_toggle": "Show/hide the AI actions",
        "win.history_toggle": "Show/hide the history (remembered for next time)",
        "win.no_target": "⚠ No target app chosen – „Insert“ will only reach the clipboard. Please press „🎯 Choose target app“.",

        # -- status line ------------------------------------------------
        "msg.recognised": "heard: „{raw}“   →   {outcome}",
        "msg.as_text": "inserted as text",
        "msg.nothing": "nothing recognised",
        "msg.no_command": "command not recognised",
        "msg.copied": "command: copied to the clipboard",
        "msg.closing": "command: close the window",
        "msg.cheatsheet": "command list opened",
        "msg.retarget": "choosing the target app …",
        "msg.spell_mode": "spelling mode: say the letters",
        "msg.correction_open": "correction window opened",
        "msg.to_correction": "→ correction window",
        "msg.correction_cancelled": "correction cancelled",
        "msg.paste_failed": "could not insert – the text is in the clipboard (Ctrl+V)",
        "msg.pasted": "inserted – carry on dictating",
        "msg.clipboard": "copied to the clipboard",
        "msg.cleared": "emptied – kept in the history, Ctrl+Z undoes it",
        "msg.took_selection": "selected text taken over – dictate on or edit it",
        "msg.ai_kept": "✓ AI result kept – Ctrl+Z undoes it",
        "msg.ai_no_change": "AI: no change needed",
        "msg.history_empty": "the history is empty",
        "msg.history_already_empty": "the history is already empty",
        "msg.history_deleted": "history deleted",
        "msg.history_restored": "history restored",
        "msg.history_loaded": "loaded from the history",
        "msg.mark_word_first": "Select a word first, then „＋ Dictionary“.",
        "msg.selection_cancelled": "selection cancelled (Escape)",
        "msg.picked": "match {n} of {total} chosen",
        "msg.deleted_count": "{n} dictations deleted.",

        # -- correction dialog -----------------------------------------
        "corr.title": "Correction",
        "corr.heard": "Heard so far:",
        "corr.correct": "Correct version (type or speak it):",
        "corr.suggestions": "Suggestions (click one, or say „nimm N“):",
        "corr.help": "Type the correction, or press the dictation key and speak the correct version. Then „Apply“.",
        "corr.cancel": "Cancel",
        "corr.apply": "Apply",

        # -- command list ----------------------------------------------
        "cheat.intro": "Say one of these commands while the dictation window is open. Anything else is inserted as text.",
        "cheat.search": "Search a command …",
        "cheat.manual": "Full manual …",
        "cheat.close": "Close",
        "cheat.count": "{n} commands",
        "cheat.mic": "Speak the search term",
        "cheat.mic.stop": "Stop recording",

        # -- AI preview -------------------------------------------------
        "ai.highlight": "Highlight changes",
        "ai.highlight.hint": "Mark changed or added words in green",
        "ai.apply": "Apply",
        "ai.result.diff": "Result of the AI action – green = changed or added.",
        "ai.result": "Result of the AI action.",

        # -- dialogs ----------------------------------------------------
        "learn.title": "Learn from a text",
        "learn.intro": "Paste a text (or load a file) – WithEase suggests your technical terms and names from it. Tick what you want and accept; they end up in „Eigene Wörter“.",
        "learn.placeholder": "Paste the text here …",
        "learn.cancel": "Cancel",
        "learn.accept": "Accept the selected",
        "learn.none": "No terms found.",
    },
}

_lang = _Lang()
t = _lang.t

__all__ = ["t", "STRINGS"]
