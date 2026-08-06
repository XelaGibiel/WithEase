"""P1b: the key-repeat delay must not swallow keystrokes inside WithEase's own
windows, so dictated text stays reliably editable in the dictation window."""
from withease.modules import keyboard as kb

_VK_A = 0x41
_VK_B = 0x42


def _press(mod: kb.KeyboardModule, vk: int) -> bool:
    # (vk, scan, extended, injected, is_press)
    return mod._on_key_event(vk, 0, False, False, True)


def test_key_delay_swallows_repeats_in_foreign_window(monkeypatch):
    monkeypatch.setattr(kb, "foreground_is_own_process", lambda: False)
    m = kb.KeyboardModule()
    m.load_settings({"delay_enabled": True, "delay_ms": 500})
    assert _press(m, _VK_A) is False        # first press passes through
    assert _press(m, _VK_A) is True         # rapid repeat is swallowed


def test_key_delay_bypassed_in_own_window(monkeypatch):
    monkeypatch.setattr(kb, "foreground_is_own_process", lambda: True)
    m = kb.KeyboardModule()
    m.load_settings({"delay_enabled": True, "delay_ms": 500})
    assert _press(m, _VK_B) is False        # first press passes through
    # In our own window the debounce is skipped, so the repeat is NOT swallowed
    # (Backspace/arrow auto-repeat keeps working while editing dictated text).
    assert _press(m, _VK_B) is False
