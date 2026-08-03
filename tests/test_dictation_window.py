"""Tests for the dictation window's transcript routing (examples/dictation)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import dictation_window as dw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make(app, history=None, hist_sink=None):
    inserted, copied = [], []
    win = dw.DictationWindow(
        on_insert=inserted.append, on_copy=copied.append,
        on_history_changed=(hist_sink if hist_sink is not None else None),
        history=history)
    return win, inserted, copied


def feed(app, win, text, mode="auto"):
    win.handle_transcript(text, mode)
    app.processEvents()


def test_dictation_then_command_then_correction(app):
    win, inserted, _ = make(app)
    feed(app, win, "Hallo Welt")
    feed(app, win, "neue Zeile")
    feed(app, win, "Zeile zwei")
    assert win.text() == "Hallo Welt\nZeile zwei"
    # correction: select a word and re-speak
    feed(app, win, "markiere Welt")
    feed(app, win, "Erde")
    assert win.text() == "Hallo Erde\nZeile zwei"


def test_insert_command_inserts_then_closes_and_clears(app):
    win, inserted, _ = make(app)
    win.show()
    feed(app, win, "Guten Tag")
    feed(app, win, "einfügen")
    assert inserted == ["Guten Tag"]
    assert win.text() == ""              # buffer cleared
    assert not win.isVisible()           # window closed
    assert win._history.count() == 1     # text kept in the history
    assert win._history.item(0).data(dw.Qt.ItemDataRole.UserRole) == "Guten Tag"


def test_copy_command_calls_callback(app):
    win, _, copied = make(app)
    feed(app, win, "Notiz")
    feed(app, win, "kopieren")
    assert copied == ["Notiz"]


def test_spell_inline(app):
    win, _, _ = make(app)
    feed(app, win, "buchstabiere Ludwig Emil Ida")   # L E I
    assert win.text() == "Lei"


def test_ambiguous_marks_candidates(app):
    win, _, _ = make(app)
    feed(app, win, "Haus und Haus")
    feed(app, win, "markiere Haus")
    assert len(win._edit.extraSelections()) == 2      # both matches highlighted
    assert len(win._badges._badges) == 2              # numbered badges painted
    assert "Treffer" in win._hint.text()
    feed(app, win, "nimm zwei")
    assert win._edit.extraSelections() == []          # cleared after pick
    assert win._badges._badges == []


def test_command_mode_does_not_insert_unknown(app):
    win, _, _ = make(app)
    feed(app, win, "irgendein Satz", mode="command")
    assert win.text() == ""                           # not dumped as text
    assert "nicht erkannt" in win._hint.text()
    feed(app, win, "neue Zeile", mode="command")      # a real command still runs
    assert win.text() == "\n"


def test_text_mode_never_runs_commands(app):
    win, _, _ = make(app)
    feed(app, win, "markiere Haus", mode="text")      # looks like a command
    assert win.text() == "markiere Haus"              # inserted verbatim


def test_history_persists_and_caps_fifo(app):
    saved = []
    win, _, _ = make(app, hist_sink=lambda items: saved.__setitem__(
        slice(None), items))
    for i in range(25):
        feed(app, win, f"Eintrag {i}")
        feed(app, win, "schließen")
    assert win._history.count() == 20                 # capped
    assert len(saved) == 20                            # persisted list capped
    assert saved[0] == "Eintrag 24"                    # newest first
    assert "Eintrag 4" not in saved                    # oldest dropped


def test_history_restored_from_storage(app):
    win, _, _ = make(app, history=["Zweiter", "Erster"])   # newest first
    assert win._history.count() == 2
    win._load_history(win._history.item(0))
    app.processEvents()
    assert win.text() == "Zweiter"


def test_spell_mode(app):
    win, _, _ = make(app)
    feed(app, win, "buchstabieren")
    feed(app, win, "Heinrich Anton Ulrich Samuel")   # H A U S
    assert win.text() == "Haus"


def test_close_clears_buffer_and_archives(app):
    win, _, _ = make(app)
    feed(app, win, "Hallo Welt")
    feed(app, win, "schließen")
    assert win.text() == ""                       # buffer cleared on close
    assert win._history.count() == 1              # text moved to history
    assert win._history.item(0).data(dw.Qt.ItemDataRole.UserRole) == "Hallo Welt"


def test_copy_and_close(app):
    win, _, copied = make(app)
    feed(app, win, "Meine Notiz")
    win._do_copy_and_close()
    app.processEvents()
    assert copied == ["Meine Notiz"]
    assert win.text() == ""


def test_history_reload(app):
    win, _, _ = make(app)
    feed(app, win, "Erster Text")
    feed(app, win, "schließen")
    assert win.text() == ""
    win._load_history(win._history.item(0))
    app.processEvents()
    assert win.text() == "Erster Text"


def test_correction_window_opens_speak_and_confirm(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("korrigiere Welt", "command")
    app.processEvents()
    assert win._correction_dialog is not None            # correction window open
    assert win._correction_dialog.result_text() == "Welt"  # prefilled with target
    win.handle_transcript("Erde", "text")                # spoken → fills the field
    app.processEvents()
    assert win._correction_dialog.result_text() == "Erde"
    win.handle_transcript("übernehmen", "command")       # confirm hands-free
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Hallo Erde"
    assert learned == [("Welt", "Erde")]


def test_correction_window_spoken_word_has_no_trailing_period(app):
    win, _, _ = make(app)
    win.handle_transcript("Ich sehe ein Haus", "text")
    win.handle_transcript("korrigiere Haus", "command")
    app.processEvents()
    win.handle_transcript("Auto.", "text")          # Whisper appended a period
    app.processEvents()
    assert win._correction_dialog.result_text() == "Auto"   # no trailing period
    win.handle_transcript("übernehmen", "command")
    app.processEvents()
    assert win.text() == "Ich sehe ein Auto"


def test_correction_window_typed_self_input(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Ich sehe ein Haus", "text")
    win.handle_transcript("korrigiere Haus", "command")
    app.processEvents()
    dlg = win._correction_dialog
    assert dlg is not None
    dlg._field.setText("Auto")       # user types the correction (self-input)
    dlg._apply()                     # clicks "Übernehmen"
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Ich sehe ein Auto"
    assert learned == [("Haus", "Auto")]


def test_correction_window_shows_suggestions_and_pick(app):
    # module-side suggestion (e.g. learned/glossary) plus buffer/casing
    win = dw.DictationWindow(on_suggest=lambda wrong: ["Maus"])
    win.handle_transcript("Ich sehe ein Haus", "text")
    win.handle_transcript("korrigiere Haus", "command")
    app.processEvents()
    dlg = win._correction_dialog
    assert dlg is not None
    assert "Maus" in dlg._suggestions
    win.handle_transcript("nimm eins", "command")   # pick suggestion 1
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Ich sehe ein Maus"


def test_dictation_key_keeps_manual_selection_and_overwrites(app):
    win, _, _ = make(app)
    win.handle_transcript("Ein schönes Haus", "text")
    app.processEvents()
    cur = win._edit.textCursor()
    cur.setPosition(4)
    cur.setPosition(11, dw.QTextCursor.MoveMode.KeepAnchor)   # "schönes"
    win._edit.setTextCursor(cur)
    win.open_for_dictation()                    # pressing the dictation key
    app.processEvents()
    assert win._edit.textCursor().selectedText() == "schönes"   # kept
    win.handle_transcript("kleines", "text")    # overwrites the selection
    app.processEvents()
    assert win.text() == "Ein kleines Haus"


def test_dictation_inserts_at_cursor_when_already_open(app):
    win, _, _ = make(app)
    win.show()                                   # session already in progress
    win.handle_transcript("Anfang Ende", "text")
    app.processEvents()
    cur = win._edit.textCursor()
    cur.setPosition(6)                           # between "Anfang" and "Ende"
    win._edit.setTextCursor(cur)
    win.open_for_dictation()                     # pressing the key again
    win.handle_transcript("Mitte", "text")
    app.processEvents()
    assert win.text() == "Anfang Mitte Ende"     # inserted at cursor, not end


def test_redo_last_dictation_replaces_without_learning(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("nochmal", "command")       # misspoke → redo
    app.processEvents()
    assert win._edit.textCursor().selectedText() == "Hallo Welt"
    win.handle_transcript("Hallo Erde", "text")        # re-record replaces it
    app.processEvents()
    assert win.text() == "Hallo Erde"
    assert learned == []                                # a slip is not learned


def test_correct_das_uses_manual_selection(app):
    win, _, _ = make(app)
    win.handle_transcript("Ein schönes Haus", "text")
    app.processEvents()
    # simulate a manual (mouse) selection of just "schönes"
    cur = win._edit.textCursor()
    cur.setPosition(4)
    cur.setPosition(11, dw.QTextCursor.MoveMode.KeepAnchor)
    win._edit.setTextCursor(cur)
    assert cur.selectedText() == "schönes"
    win.handle_transcript("korrigiere das", "command")
    app.processEvents()
    assert win._correction_dialog is not None
    assert win._correction_dialog.result_text() == "schönes"   # only that word
    win._correction_dialog._field.setText("kleines")
    win._correction_dialog._apply()
    app.processEvents()
    assert win.text() == "Ein kleines Haus"


def test_correction_window_closed_via_x_resumes_dictation(app):
    win, _, _ = make(app)
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("korrigiere Welt", "command")
    app.processEvents()
    assert win._correction_dialog is not None
    win._correction_dialog.reject()       # like closing via the window X
    app.processEvents()
    assert win._correction_dialog is None  # routing lock released
    win.handle_transcript("neuer Text", "text")   # must land in the buffer
    app.processEvents()
    assert "neuer Text" in win.text()


def test_correction_window_cancel_keeps_text(app):
    win, _, _ = make(app)
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("korrigiere Welt", "command")
    app.processEvents()
    win.handle_transcript("abbrechen", "command")
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Hallo Welt"                    # unchanged


def test_marking_then_respeak_does_not_learn(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("markiere Welt", "command")     # just a quick edit
    win.handle_transcript("Erde", "text")
    app.processEvents()
    assert win.text() == "Hallo Erde"
    assert learned == []                                   # must NOT learn


def test_replace_command_forwards_correction(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Ich mag Katzen", "text")
    win.handle_transcript("ersetze Katzen durch Hunde", "command")
    app.processEvents()
    assert win.text() == "Ich mag Hunde"
    assert learned == [("Katzen", "Hunde")]


def test_insert_fallback_keeps_window_open(app):
    win = dw.DictationWindow(on_insert=lambda _t: False)   # paste failed
    win.show()
    win.handle_transcript("Hallo", "text")
    win.handle_transcript("einfügen", "command")
    app.processEvents()
    assert win.isVisible()                    # stayed open
    assert win.text() == "Hallo"              # text kept
    assert "Zwischenablage" in win._hint.text()


def test_insert_success_closes(app):
    win = dw.DictationWindow(on_insert=lambda _t: True)
    win.show()
    win.handle_transcript("Hallo", "text")
    win.handle_transcript("einfügen", "command")
    app.processEvents()
    assert not win.isVisible()
    assert win.text() == ""


def test_target_label_updates(app):
    win, _, _ = make(app)
    win.set_target("Dokument1 – Word")
    app.processEvents()
    assert "Word" in win._target_label.text()
    win.set_target("")
    app.processEvents()
    assert "keine" in win._target_label.text().lower()


# --- live dictation: auto-space between sentences ---------------------------

def test_needs_sep_after_sentence_punctuation():
    ns = dw.DictationWindow._needs_sep
    assert ns("a") and ns("1")
    assert ns(".") and ns("!") and ns("?") and ns("…")
    assert ns(",") and ns(":") and ns(";")
    assert not ns(" ")


def test_live_second_sentence_gets_leading_space(app):
    win, _, _ = make(app)
    win.live_final("Das ist Satz eins.")
    app.processEvents()
    win.live_polish("Das ist Satz eins.")
    app.processEvents()
    # a new sentence must not stick to the previous period
    win.live_final("Das ist Satz zwei.")
    app.processEvents()
    assert win.text() == "Das ist Satz eins. Das ist Satz zwei."


def test_live_inserts_at_cursor_not_end(app):
    win, _, _ = make(app)
    win.live_final("Ich mag Haus.")
    app.processEvents()
    win.live_polish("Ich mag Haus.")
    app.processEvents()
    # move the cursor before "Haus" (as „Cursor vor Haus" would) and dictate
    idx = win.text().index("Haus")
    cur = win._edit.textCursor()
    cur.setPosition(idx)
    win._edit.setTextCursor(cur)
    win.live_final("das")
    app.processEvents()
    assert win.text() == "Ich mag das Haus."


def test_live_at_cursor_adds_leading_and_trailing_space(app):
    win, _, _ = make(app)
    win.live_final("ABCD")
    app.processEvents()
    win.live_polish("ABCD")
    app.processEvents()
    cur = win._edit.textCursor()
    cur.setPosition(2)                 # between B and C: "AB|CD"
    win._edit.setTextCursor(cur)
    win.live_final("x")
    app.processEvents()
    assert win.text() == "AB x CD"


# --- live sentence-accumulation polish --------------------------------------

def test_live_polish_keeps_sentence_open_until_committed(app):
    win, _, _ = make(app)
    # first pause: mid-sentence, Whisper text has no end punctuation → open
    win.live_final("das ist")
    app.processEvents()
    win.live_polish("Das ist", commit=False)
    app.processEvents()
    assert win.text() == "Das ist"
    # second pause: sentence completes → whole sentence re-polished + committed
    win.live_final("ein satz zeichen")
    app.processEvents()
    win.live_polish("Das ist ein Satzzeichen.", commit=True)
    app.processEvents()
    assert win.text() == "Das ist ein Satzzeichen."
    # next sentence starts fresh and is appended with a separating space
    win.live_final("und noch was")
    app.processEvents()
    win.live_polish("Und noch was.", commit=True)
    app.processEvents()
    assert win.text() == "Das ist ein Satzzeichen. Und noch was."


def test_whisper_only_polish_without_vosk_run(app):
    # Whisper-only mode: no live_final (no Vosk) precedes the polish – the
    # polished text is inserted fresh and kept open until it commits.
    win, _, _ = make(app)
    win.live_polish("Das ist ein", commit=False)
    app.processEvents()
    assert win.text() == "Das ist ein"
    win.live_polish("Das ist ein Test.", commit=True)      # whole sentence
    app.processEvents()
    assert win.text() == "Das ist ein Test."
    win.live_polish("Und noch was.", commit=True)          # next sentence
    app.processEvents()
    assert win.text() == "Das ist ein Test. Und noch was."


# --- live noise gate --------------------------------------------------------

def test_chunk_rms_gate():
    import numpy as np

    import module as mod
    rms = mod.DictationModule._chunk_rms
    silence = np.zeros(2000, dtype=np.int16).tobytes()
    loud = (np.ones(2000, dtype=np.int16) * 4000).tobytes()
    assert rms(b"") == 0.0
    assert rms(silence) == 0.0
    assert rms(loud) > 3000            # loud speech is well above a ~250 gate


def test_reselect_command_calls_callback(app):
    called = []
    win = dw.DictationWindow(on_reselect_target=lambda: called.append(1))
    win.handle_transcript("Ziel wählen", "command")
    app.processEvents()
    assert called == [1]


def test_geometry_saved_on_close(app):
    saved = []
    win = dw.DictationWindow(on_geometry_changed=lambda g: saved.append(g))
    win.show()
    win.handle_transcript("Hi", "text")
    win.handle_transcript("schließen", "command")
    app.processEvents()
    assert saved and len(saved[-1]) == 4


def test_low_confidence_words_highlighted(app):
    win, _, _ = make(app)
    win.handle_transcript("Ich sehe ein Haus", "text", ["Haus"])
    app.processEvents()
    assert len(win._edit.extraSelections()) == 1


def test_cheatsheet_constructs(app):
    dlg = dw.CommandCheatSheet()
    assert "Sprachbefehle" in dlg.windowTitle()


def test_add_selection_to_vocab(app, monkeypatch):
    added = []
    win = dw.DictationWindow(on_add_vocab=lambda s, w: added.append((s, w)))
    win.handle_transcript("Ich sehe WithEase", "text")
    app.processEvents()
    cur = win._edit.textCursor()
    cur.setPosition(9)
    cur.setPosition(17, dw.QTextCursor.MoveMode.KeepAnchor)   # "WithEase"
    win._edit.setTextCursor(cur)
    monkeypatch.setattr(dw.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("with ease", True)))
    win._add_selection_to_vocab()
    assert added == [("with ease", "WithEase")]


def test_add_selection_to_vocab_needs_selection(app):
    added = []
    win = dw.DictationWindow(on_add_vocab=lambda s, w: added.append((s, w)))
    win.handle_transcript("Kein markiertes Wort", "text")
    app.processEvents()
    win._add_selection_to_vocab()          # nothing selected
    assert added == []
    assert "markieren" in win._hint.text()


def test_accepted_low_words_are_confirmed(app):
    confirmed = []
    win = dw.DictationWindow(
        on_insert=lambda _t: True,
        on_confirm_words=lambda words: confirmed.extend(words))
    win.show()
    win.handle_transcript("Ich sehe ein Haus", "text", ["Haus"])
    win.handle_transcript("einfügen", "command")
    app.processEvents()
    assert "Haus" in confirmed        # flagged but accepted unchanged → learned


def test_corrected_low_word_is_not_confirmed(app):
    confirmed = []
    win = dw.DictationWindow(
        on_insert=lambda _t: True,
        on_confirm_words=lambda words: confirmed.extend(words))
    win.show()
    win.handle_transcript("Ich sehe ein Haus", "text", ["Haus"])
    # replace the flagged word before inserting
    win.handle_transcript("ersetze Haus durch Auto", "command")
    win.handle_transcript("einfügen", "command")
    app.processEvents()
    assert "Haus" not in confirmed    # it was changed, so not confirmed


def test_live_partial_then_final_then_polish(app):
    win, _, _ = make(app)
    win.show()
    # word-by-word provisional updates
    win.live_partial("hallo")
    app.processEvents()
    assert win.text() == "hallo"
    win.live_partial("hallo welt")
    app.processEvents()
    assert win.text() == "hallo welt"
    # segment finalises
    win.live_final("hallo welt")
    app.processEvents()
    assert win.text() == "hallo welt"
    # Whisper polish replaces the finalised segment (punctuation/casing)
    win.live_polish("Hallo Welt.")
    app.processEvents()
    assert win.text() == "Hallo Welt."


def test_live_second_segment_appends_with_space(app):
    win, _, _ = make(app)
    win.show()
    win.live_final("erster Satz")
    win.live_partial("zweiter")
    app.processEvents()
    assert win.text() == "erster Satz zweiter"
    win.live_final("zweiter Satz")
    app.processEvents()
    assert win.text() == "erster Satz zweiter Satz"


def test_live_polish_skipped_if_user_edited(app):
    win, _, _ = make(app)
    win.show()
    win.live_final("test")
    app.processEvents()
    win._edit.setPlainText("etwas ganz anderes")   # user edits
    win.live_polish("Test.")
    app.processEvents()
    assert win.text() == "etwas ganz anderes"       # polish left it alone


def test_state_shows_mode(app):
    win, _, _ = make(app)
    win.set_state("recording", "Befehl")
    app.processEvents()
    assert "Aufnahme" in win._status.text()
    assert "Befehl" in win._status.text()
    win.set_state("transcribing", "Diktat")
    app.processEvents()
    assert "Diktat" in win._status.text()


def test_state_indicator_updates(app):
    win, _, _ = make(app)
    win.set_state("recording")
    app.processEvents()
    assert "Aufnahme" in win._status.text()
    win.set_state("transcribing")
    app.processEvents()
    assert "erkannt" in win._status.text()
    win.set_state("idle")
    app.processEvents()
    assert "Bereit" in win._status.text()
