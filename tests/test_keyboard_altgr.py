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
    return [(_LCTRL, scan, True), (_RALT, 0, True),
            (_RALT, 0, False), (_LCTRL, scan, False)]


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
