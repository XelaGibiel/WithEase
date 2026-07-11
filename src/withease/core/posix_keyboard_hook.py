"""POSIX (Linux/X11) keyboard backend – mirror of :mod:`win_keyboard_hook`.

This provides the *same public API* as the Windows low-level hook so the rest
of the app stays platform-agnostic.  It uses pynput's global ``Listener`` to
observe key events and translates every pynput key into the **same Windows
virtual-key codes** the Windows backend emits, so all downstream logic
(``vk_to_combo_str``, ``MOD_VK`` lookups, sticky keys, macros …) works
unchanged.

Known limitations versus the Windows WH_KEYBOARD_LL hook
--------------------------------------------------------
* **No selective suppression.**  pynput can only suppress *all* keys globally
  (which would make the keyboard unusable), so a callback returning ``True`` is
  honoured *best-effort only*: the key is still delivered to the focused app.
  Features that rely on swallowing a key – Sticky Keys holding a modifier open,
  key-delay debouncing, keyboard-as-mouse-click – therefore also pass the
  original key through on Linux.
* **X11 only.**  On Wayland, pynput cannot observe global input; the hook then
  simply reports nothing.  Run the X11/Xorg session for full functionality.

The static VK maps below intentionally mirror :mod:`win_keyboard_hook` so a
callback receives identical ``vk`` values on both platforms.  Keep them in sync.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

try:  # pynput is a hard dependency, but never crash the app if it is missing
    from pynput import keyboard as _pynput_kb
except Exception as exc:  # pragma: no cover - only on a broken install
    _pynput_kb = None
    log.warning("pynput keyboard unavailable: %s", exc)


# Event callback: (vk, scan, extended, injected, is_press) -> suppress?
KeyCallback = Callable[[int, int, bool, bool, bool], bool]


# ---------------------------------------------------------------------------
# Static maps – MUST mirror win_keyboard_hook so the emitted ``vk`` values and
# the combo strings match across platforms.  (Duplicated on purpose so the
# Windows module can stay byte-for-byte untouched and free of any import of
# this file.)
# ---------------------------------------------------------------------------

NUMPAD_VK: dict[str, int] = {
    "num_0": 0x60, "num_1": 0x61, "num_2": 0x62, "num_3": 0x63,
    "num_4": 0x64, "num_5": 0x65, "num_6": 0x66, "num_7": 0x67,
    "num_8": 0x68, "num_9": 0x69,
    "num_add": 0x6B, "num_subtract": 0x6D,
    "num_multiply": 0x6A, "num_divide": 0x6F, "num_decimal": 0x6E,
}
VK_TO_NUM_STR: dict[int, str] = {vk: f"Key.{name}" for name, vk in NUMPAD_VK.items()}

MOD_VK: dict[int, str] = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl",  0xA2: "ctrl",  0xA3: "ctrl",
    0x12: "alt",   0xA4: "alt",   0xA5: "alt",
    0x5B: "win",   0x5C: "win",
}

SPECIAL_VK: dict[int, str] = {
    0x20: "Key.space", 0x0D: "Key.enter", 0x09: "Key.tab",
    0x08: "Key.backspace", 0x2E: "Key.delete", 0x2D: "Key.insert",
    0x24: "Key.home", 0x23: "Key.end",
    0x21: "Key.page_up", 0x22: "Key.page_down",
    0x26: "Key.up", 0x28: "Key.down", 0x25: "Key.left", 0x27: "Key.right",
    0x1B: "Key.esc", 0x14: "Key.caps_lock", 0x91: "Key.scroll_lock",
    0x13: "Key.pause", 0x2C: "Key.print_screen", 0x90: "Key.num_lock",
    **{0x70 + i: f"Key.f{i + 1}" for i in range(12)},
}

MOD_RELEASE_VKS: dict[str, list[int]] = {
    "shift": [0xA0, 0xA1],
    "ctrl":  [0xA2, 0xA3],
    "alt":   [0xA4],
    "win":   [0x5B, 0x5C],
    "altgr": [0xA5, 0xA2],
}


def vk_to_combo_str(vk: int) -> str | None:
    """Convert a virtual-key code to the combo string HotkeyEdit stores."""
    if vk in VK_TO_NUM_STR:
        return VK_TO_NUM_STR[vk]
    if 0x41 <= vk <= 0x5A:
        return f"'{chr(vk).lower()}'"
    if 0x30 <= vk <= 0x39:
        return f"'{chr(vk)}'"
    if vk in SPECIAL_VK:
        return SPECIAL_VK[vk]
    return None


def is_altgr_fake_lctrl(vk: int, scan: int) -> bool:
    """No synthetic AltGr left-ctrl on X11 – always False here."""
    return False


# ---------------------------------------------------------------------------
# pynput key  ->  Windows virtual-key code
# ---------------------------------------------------------------------------

# pynput Key attribute name -> Windows VK code.  Left/right modifier variants
# map to the physical VKs that MOD_VK / MOD_RELEASE_VKS know about.
_KEY_NAME_TO_VK: dict[str, int] = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "backspace": 0x08,
    "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "esc": 0x1B, "caps_lock": 0x14, "scroll_lock": 0x91, "pause": 0x13,
    "print_screen": 0x2C, "num_lock": 0x90,
    "shift": 0xA0, "shift_l": 0xA0, "shift_r": 0xA1,
    "ctrl": 0xA2, "ctrl_l": 0xA2, "ctrl_r": 0xA3,
    "alt": 0xA4, "alt_l": 0xA4, "alt_r": 0xA5, "alt_gr": 0xA5,
    "cmd": 0x5B, "cmd_l": 0x5B, "cmd_r": 0x5C,
    **{f"f{i}": 0x70 + (i - 1) for i in range(1, 13)},
}


def _build_key_to_vk() -> dict:
    if _pynput_kb is None:
        return {}
    key_enum = _pynput_kb.Key
    m: dict = {}
    for name, vk in _KEY_NAME_TO_VK.items():
        key = getattr(key_enum, name, None)
        if key is not None:
            m[key] = vk
    return m


_KEY_TO_VK = _build_key_to_vk()


def _key_to_vk(key) -> int | None:
    """Best-effort pynput key -> Windows VK code."""
    if key is None:
        return None
    vk = _KEY_TO_VK.get(key)
    if vk is not None:
        return vk
    # Character keys (letters/digits) arrive as KeyCode with a .char.
    char = getattr(key, "char", None)
    if char:
        c = char.lower()
        if "a" <= c <= "z":
            return 0x41 + (ord(c) - ord("a"))
        if "0" <= c <= "9":
            return 0x30 + (ord(c) - ord("0"))
    return None


# ---------------------------------------------------------------------------
# Modifier state – tracked from observed press/release (no OS query on X11).
# ---------------------------------------------------------------------------

_held_mods: set[str] = set()
_held_lock = threading.Lock()


def effective_modifiers() -> frozenset[str]:
    """Modifiers currently held, tracked from observed events."""
    with _held_lock:
        return frozenset(_held_mods)


def current_combo_str(vk: int) -> str | None:
    """Full hotkey string for a key press including held modifiers."""
    key_str = vk_to_combo_str(vk)
    if key_str is None:
        return None
    mods = sorted(effective_modifiers())
    return "+".join(mods + [key_str]) if mods else key_str


def inject_modifier_release(name: str) -> None:
    """Release a modifier via pynput (best effort)."""
    if _pynput_kb is None:
        return
    K = _pynput_kb.Key
    keys = {
        "shift": [K.shift], "ctrl": [K.ctrl], "alt": [K.alt],
        "win": [getattr(K, "cmd", None)],
        "altgr": [getattr(K, "alt_gr", None), getattr(K, "alt_r", None)],
    }.get(name, [])
    try:
        controller = _pynput_kb.Controller()
        for k in keys:
            if k is not None:
                controller.release(k)
    except Exception:
        pass


def release_all_modifiers() -> None:
    """Force-release every modifier – panic-button cleanup."""
    with _held_lock:
        _held_mods.clear()
    for name in ("shift", "ctrl", "alt", "altgr", "win"):
        inject_modifier_release(name)


# ---------------------------------------------------------------------------
# Shared hook – same subscribe/unsubscribe surface as the Windows backend.
# ---------------------------------------------------------------------------

class _SharedKeyboardHook:
    """Process-wide keyboard listener shared by all modules (pynput-based)."""

    def __init__(self) -> None:
        self._callbacks: list[KeyCallback] = []
        self._listener = None
        self._lock = threading.Lock()

    def subscribe(self, callback: KeyCallback) -> None:
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
            if self._listener is None and _pynput_kb is not None:
                self._start_listener()

    def unsubscribe(self, callback: KeyCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
            if not self._callbacks and self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None

    def _start_listener(self) -> None:
        try:
            self._listener = _pynput_kb.Listener(
                on_press=lambda k: self._on_event(k, True),
                on_release=lambda k: self._on_event(k, False),
            )
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:  # pragma: no cover
            log.warning("could not start keyboard listener: %s", exc)
            self._listener = None

    def _on_event(self, key, is_press: bool) -> None:
        vk = _key_to_vk(key)
        if vk is None:
            return
        mod = MOD_VK.get(vk)
        if mod:
            with _held_lock:
                if is_press:
                    _held_mods.add(mod)
                else:
                    _held_mods.discard(mod)
        # pynput cannot suppress selectively, so the return value is ignored.
        for callback in list(self._callbacks):
            try:
                callback(vk, 0, False, False, is_press)
            except Exception:
                pass


shared_keyboard_hook = _SharedKeyboardHook()
