"""Tests for the self-learning error memory (examples/dictation)."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import correction as co  # noqa: E402


def _strong(mem, old, new):
    """Learn a correction enough times that it applies unconditionally."""
    mem.learn(old, new)
    mem.learn(old, new)


def test_fresh_correction_only_applies_when_uncertain():
    mem = co.ErrorMemory()
    assert mem.learn("Kaser", "Cursor") is True         # active after 1 correction
    # a clearly-spoken (confident) word is trusted → not over-corrected
    assert mem.apply("Kaser vor Haus") == "Kaser vor Haus"
    # but where Whisper was uncertain, the learned fix is applied
    assert mem.apply("Kaser vor Haus", uncertain=["Kaser"]) == "Cursor vor Haus"
    # and it is always available as a direct suggestion
    assert mem.direct("kaser") == "Cursor"


def test_repeated_correction_applies_always():
    mem = co.ErrorMemory()
    _strong(mem, "Kaser", "Cursor")
    assert mem.apply("Kaser vor Haus") == "Cursor vor Haus"   # no uncertainty needed


def test_self_correction_removes_over_correction():
    mem = co.ErrorMemory()
    _strong(mem, "kaus", "Haus")                # memory turns "kaus" → "Haus"
    assert mem.apply("kaus") == "Haus"
    mem.learn("Haus", "Maus")                   # user corrects our output away
    assert "kaus" not in mem.substitutions()    # the over-correction is forgotten


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
    assert mem.direct("Kaser") == "Cursor"
    assert mem.apply("Kaser", uncertain=["Kaser"]) == "Cursor"
    assert mem.substitutions() == {"kaser": "Cursor"}


def test_case_is_matched():
    mem = co.ErrorMemory()
    _strong(mem, "kaser", "cursor")
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
    mem = co.ErrorMemory()
    _strong(mem, "aus", "Haus")
    # "aus" must not corrupt "Pause" / "ausser" – whole-word match only
    assert mem.apply("Pause draußen") == "Pause draußen"
    assert mem.apply("aus dem Fenster") == "Haus dem Fenster"


def test_remove_forgets_active_and_candidate():
    mem = co.ErrorMemory()
    _strong(mem, "Kaser", "Cursor")
    assert mem.apply("Kaser") == "Cursor"
    mem.remove("kaser")                      # folded key
    assert mem.apply("Kaser") == "Kaser"
    assert mem.substitutions() == {}


def test_suggest_alternatives_ranks_by_similarity():
    pool = ["Haus", "Maus", "Kalender"]
    out = co.suggest_alternatives("Kaus", pool, limit=5)
    assert "Haus" in out and "Maus" in out       # edit-distance 1
    assert "Kalender" not in out                  # too far


def test_suggest_alternatives_offers_casing_fix():
    out = co.suggest_alternatives("haus", ["Haus", "Maus"], limit=5)
    assert out[0] == "Haus"                       # capitalisation fix ranks top
    assert "Maus" in out


def test_suggest_alternatives_excludes_exact_word():
    assert "haus" not in co.suggest_alternatives("haus", ["haus", "Haus"])


def test_set_target_edits_substitution():
    mem = co.ErrorMemory()
    _strong(mem, "kaser", "Cursor")
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
