"""Platform-neutral keyboard-hook facade.

The rest of the app imports the shared keyboard hook and its helpers from here
instead of a platform-specific module.  On Windows this re-exports the
low-level WH_KEYBOARD_LL hook (unchanged); on every other platform it uses the
pynput-based POSIX backend.  Both expose the identical public API, so callers
never need a platform check.

See :mod:`win_keyboard_hook` and :mod:`posix_keyboard_hook` for the backends
(the POSIX one documents its Linux limitations – notably no key suppression).
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from withease.core.win_keyboard_hook import (  # noqa: F401
        MOD_VK,
        NUMPAD_VK,
        SPECIAL_VK,
        KeyCallback,
        current_combo_str,
        effective_modifiers,
        inject_modifier_release,
        is_altgr_fake_lctrl,
        release_all_modifiers,
        shared_keyboard_hook,
        vk_to_combo_str,
    )
else:
    from withease.core.posix_keyboard_hook import (  # noqa: F401
        MOD_VK,
        NUMPAD_VK,
        SPECIAL_VK,
        KeyCallback,
        current_combo_str,
        effective_modifiers,
        inject_modifier_release,
        is_altgr_fake_lctrl,
        release_all_modifiers,
        shared_keyboard_hook,
        vk_to_combo_str,
    )
