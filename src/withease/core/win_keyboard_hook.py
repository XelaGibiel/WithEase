"""Low-level Windows keyboard hook (WH_KEYBOARD_LL).

Why this exists
---------------
pynput's keyboard ``Listener`` installs a low-level hook *and* translates every
key to a character via the keyboard layout.  On Windows this interferes with
AltGr / dead-key composition in the foreground application (e.g. AltGr+Q no
longer produces "@").  This hook does **not** translate anything – it only
reads the raw virtual-key code and scan code and forwards the event.  Because
it never touches ``ToUnicode``/the layout, it leaves AltGr composition intact,
exactly like AutoHotkey's pass-through hook.

The callback receives ``(vk, scan, extended, injected, is_press)`` and returns
``True`` to *suppress* the key (swallow it) or a falsy value to let it pass
through to the rest of the system.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

_WH_KEYBOARD_LL = 13
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
_WM_QUIT = 0x0012

_LLKHF_EXTENDED = 0x01
_LLKHF_INJECTED = 0x10

_HC_ACTION = 0

# LRESULT / LONG_PTR is pointer-sized – must be c_ssize_t for 64-bit correctness.
_LRESULT = ctypes.c_ssize_t


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


_HOOKPROC = ctypes.CFUNCTYPE(
    _LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


# Event callback: (vk, scan, extended, injected, is_press) -> suppress?
KeyCallback = Callable[[int, int, bool, bool, bool], bool]


# ---------------------------------------------------------------------------
# Shared virtual-key mapping.  Converts raw VK codes into the pynput-style
# combo strings that HotkeyEdit / _KeyRecorder store, so both the mouse and
# macro modules can match hotkeys without pynput's (AltGr-breaking) listener.
# ---------------------------------------------------------------------------

# name → VK for numpad keys (also used to build the reverse map)
NUMPAD_VK: dict[str, int] = {
    "num_0": 0x60, "num_1": 0x61, "num_2": 0x62, "num_3": 0x63,
    "num_4": 0x64, "num_5": 0x65, "num_6": 0x66, "num_7": 0x67,
    "num_8": 0x68, "num_9": 0x69,
    "num_add": 0x6B, "num_subtract": 0x6D,
    "num_multiply": 0x6A, "num_divide": 0x6F, "num_decimal": 0x6E,
}
VK_TO_NUM_STR: dict[int, str] = {vk: f"Key.{name}" for name, vk in NUMPAD_VK.items()}

# VK → canonical modifier name (left/right/generic variants)
MOD_VK: dict[int, str] = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl",  0xA2: "ctrl",  0xA3: "ctrl",
    0x12: "alt",   0xA4: "alt",   0xA5: "alt",
    0x5B: "win",   0x5C: "win",
}

# VK → non-printable special key strings (must match HotkeyEdit output)
SPECIAL_VK: dict[int, str] = {
    0x20: "Key.space", 0x0D: "Key.enter", 0x09: "Key.tab",
    0x08: "Key.backspace", 0x2E: "Key.delete", 0x2D: "Key.insert",
    0x24: "Key.home", 0x23: "Key.end",
    0x21: "Key.page_up", 0x22: "Key.page_down",
    0x26: "Key.up", 0x28: "Key.down", 0x25: "Key.left", 0x27: "Key.right",
    0x1B: "Key.esc", 0x14: "Key.caps_lock", 0x91: "Key.scroll_lock",
    0x13: "Key.pause", 0x2C: "Key.print_screen", 0x90: "Key.num_lock",
    **{0x70 + i: f"Key.f{i + 1}" for i in range(12)},  # F1..F12
}

# AltGr injects a synthetic left-ctrl with this scan code right before the real
# right-alt; it must be ignored so AltGr is never mistaken for a real Ctrl.
ALTGR_FAKE_LCTRL_VK = 0xA2
ALTGR_FAKE_LCTRL_SCAN = 0x21D


def vk_to_combo_str(vk: int) -> str | None:
    """Convert a raw virtual-key code to the combo string HotkeyEdit stores.

    Returns None for keys we don't represent (so callers can ignore them).
    """
    if vk in VK_TO_NUM_STR:            # numpad keys
        return VK_TO_NUM_STR[vk]
    if 0x41 <= vk <= 0x5A:             # A..Z → 'a'..'z'
        return f"'{chr(vk).lower()}'"
    if 0x30 <= vk <= 0x39:             # 0..9 → '0'..'9'
        return f"'{chr(vk)}'"
    if vk in SPECIAL_VK:               # F-keys, space, enter, arrows, …
        return SPECIAL_VK[vk]
    return None


def is_altgr_fake_lctrl(vk: int, scan: int) -> bool:
    """True for the synthetic left-ctrl that Windows injects for AltGr."""
    return vk == ALTGR_FAKE_LCTRL_VK and scan == ALTGR_FAKE_LCTRL_SCAN


# All physical VKs per modifier – releasing must cover BOTH sides, because
# e.g. a latch created with right-shift is not cleared by a left-shift key-up.
MOD_RELEASE_VKS: dict[str, list[int]] = {
    "shift": [0xA0, 0xA1],
    "ctrl":  [0xA2, 0xA3],
    "alt":   [0xA4],          # right-alt is AltGr and handled separately
    "win":   [0x5B, 0x5C],
    "altgr": [0xA5, 0xA2],    # right-alt + its synthetic left-ctrl
}

_KEYEVENTF_KEYUP = 0x02
_KEYEVENTF_EXTENDEDKEY = 0x01

# Right Ctrl, right Alt (AltGr) and the Windows keys are "extended" keys.  A
# key-up sent WITHOUT the extended flag targets the LEFT-hand key instead, so
# the physical right/Win key stays stuck at the OS level (right Ctrl / AltGr
# "hanging").  These VKs therefore must carry KEYEVENTF_EXTENDEDKEY on release.
_EXTENDED_RELEASE_VKS = frozenset({0xA3, 0xA5, 0x5B, 0x5C})


def inject_modifier_release(name: str) -> None:
    """Send key-up events for every physical key of the given modifier."""
    for vk in MOD_RELEASE_VKS.get(name, []):
        flags = _KEYEVENTF_KEYUP
        if vk in _EXTENDED_RELEASE_VKS:
            flags |= _KEYEVENTF_EXTENDEDKEY
        try:
            ctypes.windll.user32.keybd_event(vk, 0, flags, 0)
        except Exception:
            pass


def release_all_modifiers() -> None:
    """Force-release every modifier (both sides) – panic-button cleanup."""
    for name in ("shift", "ctrl", "alt", "altgr", "win"):
        inject_modifier_release(name)


def effective_modifiers() -> frozenset[str]:
    """Modifiers Windows currently considers held (via GetAsyncKeyState).

    Unlike tracking press/release events, this includes modifiers held open
    by Sticky Keys (whose physical releases are suppressed) – the OS state is
    what actually applies to the next key.
    """
    state = ctypes.windll.user32.GetAsyncKeyState
    mods = set()
    if state(0x10) & 0x8000:
        mods.add("shift")
    if state(0x11) & 0x8000:
        mods.add("ctrl")
    if state(0x12) & 0x8000:
        mods.add("alt")
    if (state(0x5B) | state(0x5C)) & 0x8000:
        mods.add("win")
    return frozenset(mods)


def current_combo_str(vk: int) -> str | None:
    """Full hotkey string for a key press including currently-held modifiers.

    Matches the storage format of HotkeyEdit/_KeyRecorder: modifiers sorted
    alphabetically, e.g. "alt+shift+'m'" – or just "'m'" without modifiers.
    Returns None for keys we don't represent.
    """
    key_str = vk_to_combo_str(vk)
    if key_str is None:
        return None
    mods = sorted(effective_modifiers())
    return "+".join(mods + [key_str]) if mods else key_str


class _SharedKeyboardHook:
    """Process-wide single keyboard hook shared by all modules.

    Installing more than one low-level keyboard hook starves other low-level
    hooks (notably pynput's mouse hook) because Windows serialises them and
    times slow ones out.  So every module subscribes here instead of creating
    its own hook; exactly one OS hook is installed while anyone is subscribed.

    An event is suppressed if *any* subscriber returns True.
    """

    def __init__(self) -> None:
        self._callbacks: list[KeyCallback] = []
        self._hook: LowLevelKeyboardHook | None = None
        self._lock = threading.Lock()

    def subscribe(self, callback: KeyCallback) -> None:
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
            if self._hook is None:
                self._hook = LowLevelKeyboardHook(self._dispatch)
                self._hook.start()

    def unsubscribe(self, callback: KeyCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
            if not self._callbacks and self._hook is not None:
                self._hook.stop()
                self._hook = None

    def _dispatch(self, vk: int, scan: int, extended: bool,
                  injected: bool, is_press: bool) -> bool:
        suppress = False
        for callback in list(self._callbacks):
            try:
                if callback(vk, scan, extended, injected, is_press):
                    suppress = True
            except Exception:
                pass
        return suppress


# The one hook instance every module should use.
shared_keyboard_hook = _SharedKeyboardHook()


class LowLevelKeyboardHook:
    """A raw WH_KEYBOARD_LL hook running its own message loop in a thread."""

    def __init__(self, callback: KeyCallback) -> None:
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook = None
        self._proc = None  # keep a reference so the CFUNCTYPE isn't GC'd
        self._running = False

    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread_id is not None:
            # Wake the message loop so it can exit.
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, _WM_QUIT, 0, 0)
            self._thread_id = None

    # ------------------------------------------------------------------

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
        user32.CallNextHookEx.restype = _LRESULT
        user32.CallNextHookEx.argtypes = (
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        self._thread_id = kernel32.GetCurrentThreadId()

        def proc(nCode: int, wParam: int, lParam: int) -> int:
            if nCode == _HC_ACTION:
                try:
                    kb = ctypes.cast(
                        lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    extended = bool(kb.flags & _LLKHF_EXTENDED)
                    injected = bool(kb.flags & _LLKHF_INJECTED)
                    is_press = wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
                    is_release = wParam in (_WM_KEYUP, _WM_SYSKEYUP)
                    if is_press or is_release:
                        if self._callback(
                                kb.vkCode, kb.scanCode, extended,
                                injected, is_press):
                            return 1  # suppress
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = _HOOKPROC(proc)
        self._hook = user32.SetWindowsHookExW(
            _WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            self._running = False
            return

        # Message loop – required for a low-level hook to receive events.
        msg = wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):  # WM_QUIT or error
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._proc = None
