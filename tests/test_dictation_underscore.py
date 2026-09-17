"""Hyphen and underscore dictated between numbers and words.

Reported from real use: "2026-09-16_" came out as "2026 Binderstrich 09
Binderstrich 16 Unterstrich".  "Unterstrich" was not known at all, and Whisper
wrote the spoken "Bindestrich" as "Binderstrich".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import commands_de as cde  # noqa: E402


@pytest.mark.parametrize("spoken, want", [
    ("2026 Binderstrich 09 Binderstrich 16 Unterstrich", "2026-09-16_"),
    ("2026 Bindestrich 09 Bindestrich 16 Unterstrich.", "2026-09-16_"),
    ("Bericht Unterstrich final", "Bericht_final"),
    ("Bericht Tiefstrich final", "Bericht_final"),
    ("Dateien. Schrägstrich. Bilder", "Dateien/Bilder"),
])
def test_tight_symbols_inside_the_text(spoken, want):
    assert cde.apply_inline_punctuation(spoken) == want


@pytest.mark.parametrize("spoken, char", [
    ("Unterstrich", "_"), ("Tiefstrich", "_"), ("Binderstrich", "-"),
])
def test_spoken_on_their_own(spoken, char):
    cmd = cde.parse(spoken)
    assert cmd is not None and cmd.kind == "punct"
    assert cmd.data["char"] == char


def test_the_command_reference_names_them():
    entries = [cmd for _group, items in cde.CHEAT_SHEET for cmd, _d in items]
    assert any("Unterstrich" in e for e in entries)
