"""P5: 'hold = one keystroke' – auto-repeat of a held key is swallowed unless
the key is on the exception list."""
from withease.modules import keyboard as kb

_VK_A = 0x41   # 'a'


def _press(mod, vk):
    return mod._on_key_event(vk, 0, False, False, True)


def _release(mod, vk):
    return mod._on_key_event(vk, 0, False, False, False)


def test_autorepeat_of_held_key_is_swallowed():
    m = kb.KeyboardModule()
    m.load_settings({"no_repeat_enabled": True})
    assert _press(m, _VK_A) is False    # first press passes
    assert _press(m, _VK_A) is True     # auto-repeat (no key-up) swallowed
    assert _press(m, _VK_A) is True     # still held → still swallowed
    _release(m, _VK_A)
    assert _press(m, _VK_A) is False    # pressed again after release → passes


def test_exception_key_keeps_repeating():
    m = kb.KeyboardModule()
    m.load_settings({"no_repeat_enabled": True, "no_repeat_exceptions": ["'a'"]})
    assert _press(m, _VK_A) is False
    assert _press(m, _VK_A) is False    # exception key repeats freely


def test_disabled_lets_repeats_through():
    m = kb.KeyboardModule()
    m.load_settings({"no_repeat_enabled": False})
    assert _press(m, _VK_A) is False
    assert _press(m, _VK_A) is False
