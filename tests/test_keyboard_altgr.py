"""Regression tests for AltGr handling in the keyboard module.

AltGr on a German layout arrives as a synthetic left-Ctrl followed by a
right-Alt.  If the synthetic Ctrl is mistaken for a real Ctrl tap, Sticky Keys
used to latch a stuck Ctrl+Alt that only cleared when the app was closed.  The
detection must be robust even when the synthetic Ctrl's scan code differs from
the canonical 0x21D (it varies by keyboard/driver).
"""
from unittest.mock import patch

import withease.modules.keyboard as kbmod

_LCTRL = 0xA2          # left Ctrl (AltGr's synthetic partner)
_RALT = 0xA5           # right Alt = AltGr
_Q = 0x51
_GOOD_SCAN = 0x21D     # canonical AltGr fake-ctrl scan code
_OTHER_SCAN = 0x1D     # a driver that reports a different scan code


def _feed(settings, events):
    """Run an event sequence through the module; return (module, suppresses)."""
    module = kbmod.KeyboardModule()
    module._settings = settings
    suppresses = []
    with patch.object(kbmod, "inject_modifier_release", lambda name: None):
        for vk, scan, is_press in events:
            suppresses.append(bool(
                module._on_key_event(vk, scan, False, False, is_press)))
    return module, suppresses


def _latched(module):
    return {k for k, v in module._sticky_state.items() if v}


_STICKY = {"sticky_enabled": True, "sticky_auto_release": True,
           "sticky_ctrl": True, "sticky_alt": True}


def _altgr_tap(scan):
    # Release order: right-Alt up THEN the synthetic left-Ctrl up.
    return [(_LCTRL, scan, True), (_RALT, 0, True),
            (_RALT, 0, False), (_LCTRL, scan, False)]


def _altgr_tap_real(scan):
    # The order observed on real hardware: the synthetic left-Ctrl is released
    # BEFORE the right-Alt.  This is what broke sticky AltGr.
    return [(_LCTRL, scan, True), (_RALT, 0, True),
            (_LCTRL, scan, False), (_RALT, 0, False)]


def test_altgr_tap_does_not_latch_with_canonical_scancode():
    module, _ = _feed(_STICKY, _altgr_tap(_GOOD_SCAN))
    assert _latched(module) == set()


def test_altgr_tap_does_not_latch_with_other_scancode():
    # The regression: a non-0x21D scan code must NOT leave Ctrl+Alt stuck.
    module, _ = _feed(_STICKY, _altgr_tap(_OTHER_SCAN))
    assert _latched(module) == set()


def test_altgr_plus_key_passes_through_and_does_not_latch():
    events = [(_LCTRL, _OTHER_SCAN, True), (_RALT, 0, True), (_Q, 0, True),
              (_Q, 0, False), (_RALT, 0, False), (_LCTRL, _OTHER_SCAN, False)]
    module, suppresses = _feed(_STICKY, events)
    assert _latched(module) == set()
    # The '@' key itself must never be swallowed.
    assert suppresses[2] is False


def test_real_ctrl_tap_still_latches():
    # A genuine left-Ctrl tap (no right-Alt after it) must still latch.
    module, _ = _feed(_STICKY, [(_LCTRL, _OTHER_SCAN, True),
                                (_LCTRL, _OTHER_SCAN, False)])
    assert "ctrl" in _latched(module)


def test_real_ctrl_tap_after_altgr_still_latches():
    # AltGr must not leave a stale "ctrl used" flag that blocks a later real
    # Ctrl tap from latching.
    events = _altgr_tap(_GOOD_SCAN) + [(_LCTRL, _OTHER_SCAN, True),
                                       (_LCTRL, _OTHER_SCAN, False)]
    module, _ = _feed({**_STICKY, "sticky_alt": False}, events)
    assert "ctrl" in _latched(module)


# --- Sticky AltGr must actually work (tap AltGr, then the key gets AltGr) -----
_STICKY_ALTGR = {"sticky_enabled": True, "sticky_auto_release": True,
                 "sticky_altgr": True}


def test_sticky_altgr_holds_ctrl_while_latched_other_scancode():
    # Tapping AltGr latches it and must KEEP its synthetic Ctrl held (release
    # suppressed) so the next key still produces '@', even when the scan code
    # is not the canonical 0x21D.
    module, suppresses = _feed(_STICKY_ALTGR, _altgr_tap(_OTHER_SCAN))
    assert "altgr" in _latched(module)
    assert suppresses[-1] is True   # the left-ctrl release is swallowed (held)


def test_sticky_altgr_releases_after_next_key():
    events = _altgr_tap(_OTHER_SCAN) + [(_Q, 0, True), (_Q, 0, False)]
    module = kbmod.KeyboardModule()
    module._settings = _STICKY_ALTGR
    released = []
    with patch.object(kbmod, "inject_modifier_release", released.append):
        for vk, scan, is_press in events:
            module._on_key_event(vk, scan, False, False, is_press)
    assert "altgr" in released          # AltGr released after the key
    assert _latched(module) == set()    # nothing left latched


def test_sticky_altgr_holds_ctrl_with_real_release_order():
    # Real hardware releases the synthetic Ctrl BEFORE the right-Alt – i.e.
    # before AltGr has latched.  The Ctrl release must still be held so the
    # latched AltGr keeps its Ctrl half (otherwise the next key is not '@').
    module, suppresses = _feed(_STICKY_ALTGR, _altgr_tap_real(_GOOD_SCAN))
    assert "altgr" in _latched(module)
    # events: LCtrl↓, RAlt↓, LCtrl↑, RAlt↑ → the LCtrl↑ (index 2) must be held
    assert suppresses[2] is True


def test_sticky_altgr_real_order_then_key_releases():
    events = _altgr_tap_real(_GOOD_SCAN) + [(_Q, 0, True), (_Q, 0, False)]
    module = kbmod.KeyboardModule()
    module._settings = _STICKY_ALTGR
    released = []
    with patch.object(kbmod, "inject_modifier_release", released.append):
        for vk, scan, is_press in events:
            module._on_key_event(vk, scan, False, False, is_press)
    assert "altgr" in released
    assert _latched(module) == set()


def test_hold_altgr_key_real_order_does_not_latch_or_stick():
    # Holding AltGr and using a key (real release order) must NOT latch and must
    # not leave the ctrl half stuck.
    events = [(_LCTRL, _GOOD_SCAN, True), (_RALT, 0, True), (_Q, 0, True),
              (_Q, 0, False), (_LCTRL, _GOOD_SCAN, False), (_RALT, 0, False)]
    module, suppresses = _feed(_STICKY_ALTGR, events)
    assert _latched(module) == set()
    assert suppresses[4] is False   # the LCtrl↑ is released normally
