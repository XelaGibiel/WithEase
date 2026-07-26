"""Tests for the self-learning error memory (examples/dictation)."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import correction as co  # noqa: E402


def test_default_learns_immediately():
    mem = co.ErrorMemory()                             # default threshold = 1
    assert mem.learn("Kaser", "Cursor") is True        # active after 1 correction
    assert mem.apply("Kaser vor Haus") == "Cursor vor Haus"


def test_higher_threshold_waits():
    mem = co.ErrorMemory(threshold=2)
    assert mem.learn("Kaser", "Cursor") is False       # 1st time: candidate
    assert mem.apply("Kaser vor Haus") == "Kaser vor Haus"
    assert mem.learn("Kaser", "Cursor") is True        # 2nd time: now active
    assert mem.apply("Kaser vor Haus") == "Cursor vor Haus"


def test_promotes_stored_candidate_on_load():
    # A correction captured earlier (candidate, count 1) becomes active once
    # loaded under the default threshold of 1.
    data = {"active": {}, "candidates": {"kaser": {"to": "Cursor", "count": 1}}}
    mem = co.ErrorMemory(data)
    assert mem.apply("Kaser") == "Cursor"
    assert mem.substitutions() == {"kaser": "Cursor"}


def test_case_is_matched():
    mem = co.ErrorMemory(threshold=1)
    mem.learn("kaser", "cursor")
    assert mem.apply("Kaser vor haus") == "Cursor vor haus"   # Titlecase kept
    assert mem.apply("KASER") == "CURSOR"                     # all caps kept
    assert mem.apply("kaser") == "cursor"


def test_ignores_noise_and_equal():
    mem = co.ErrorMemory(threshold=1)
    assert mem.learn("", "x") is False
    assert mem.learn("ab", "Cursor") is False          # too short
    assert mem.learn("Haus", "Haus") is False          # identical
    assert mem.learn("neue Zeile", "Absatz") is False  # phrases skipped (v1)
    assert mem.substitutions() == {}


def test_only_whole_words_replaced():
    mem = co.ErrorMemory(threshold=1)
    mem.learn("aus", "Haus")
    # "aus" must not corrupt "Pause" / "ausser" – whole-word match only
    assert mem.apply("Pause draußen") == "Pause draußen"
    assert mem.apply("aus dem Fenster") == "Haus dem Fenster"


def test_remove_forgets_active_and_candidate():
    mem = co.ErrorMemory(threshold=1)
    mem.learn("Kaser", "Cursor")
    assert mem.apply("Kaser") == "Cursor"
    mem.remove("kaser")                      # folded key
    assert mem.apply("Kaser") == "Kaser"
    assert mem.substitutions() == {}


def test_set_target_edits_substitution():
    mem = co.ErrorMemory()
    mem.learn("kaser", "Cursor")
    mem.set_target("kaser", "Cursor vor")
    assert mem.substitutions() == {"kaser": "Cursor vor"}
    assert mem.apply("kaser") == "Cursor vor"
    mem.set_target("kaser", "")          # empty ignored
    assert mem.substitutions() == {"kaser": "Cursor vor"}


def test_persistence_roundtrip():
    mem = co.ErrorMemory(threshold=2)
    mem.learn("Kaser", "Cursor")
    mem.learn("Kaser", "Cursor")       # active now
    data = mem.to_dict()
    restored = co.ErrorMemory(data)
    assert restored.apply("Kaser") == "Cursor"
    assert restored.substitutions() == {"kaser": "Cursor"}
