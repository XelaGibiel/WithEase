"""Putting the finished text into another program.

Three rules taken from OpenWhispr's paste helper, all three of which WithEase
got wrong:

* A held modifier is let go of first.  WithEase of all programs has to do
  this - its own Sticky Keys latch Shift or Ctrl until the next key, and a
  latched Shift turns the Ctrl+V of an insertion into Ctrl+Shift+V.
* A console window pastes with Ctrl+Shift+V.  Ctrl+V there does nothing at
  all, which looks exactly like the dictation having failed.
* The target window is brought to the front past Windows' foreground lock.
  A plain SetForegroundWindow from a background process is refused, and the
  text lands in whatever window happens to be in front.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import module as dic  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Keys:
    """Records what would be sent to the keyboard."""

    log: list = []

    def press(self, key):
        self.log.append(("press", getattr(key, "name", key)))

    def release(self, key):
        self.log.append(("release", getattr(key, "name", key)))

    def type(self, text):
        self.log.append(("type", text))


@pytest.fixture
def keys(monkeypatch):
    _Keys.log = []
    monkeypatch.setattr(dic, "KeyController", _Keys)
    return _Keys.log


@pytest.fixture
def modifiers(monkeypatch):
    """Pretend Ctrl is held down, and record every key press/release."""
    sent = []
    monkeypatch.setattr(dic, "_held_modifiers", lambda: [0xA2])
    monkeypatch.setattr(dic, "_send_key",
                        lambda vk, down: sent.append((vk, down)))
    return sent


@pytest.fixture
def clipboard(monkeypatch):
    """A clipboard that lives in a list instead of in Windows."""
    store = {"value": "etwas, das der Benutzer vorher kopiert hat"}

    def funcs():
        def get_text():
            return store["value"]

        def set_text(text):
            store["value"] = text
            return True
        return get_text, set_text

    monkeypatch.setattr(dic.DictationModule, "_clipboard_funcs",
                        staticmethod(funcs))
    return store


# -- letting go of held modifiers -------------------------------------------

def test_a_latched_modifier_is_released_and_pressed_again(modifiers):
    with dic.modifiers_released():
        assert modifiers == [(0xA2, False)], "released before anything is sent"
    assert modifiers == [(0xA2, False), (0xA2, True)]


def test_the_modifier_comes_back_even_when_the_action_fails(modifiers):
    with pytest.raises(RuntimeError):
        with dic.modifiers_released():
            raise RuntimeError("paste failed")
    assert modifiers[-1] == (0xA2, True), "the user's Shift must not stay down"


def test_nothing_is_touched_when_no_modifier_is_held(monkeypatch):
    sent = []
    monkeypatch.setattr(dic, "_held_modifiers", list)
    monkeypatch.setattr(dic, "_send_key", lambda vk, down: sent.append(vk))
    with dic.modifiers_released():
        pass
    assert sent == []


def test_pasting_happens_between_release_and_restore(keys, modifiers,
                                                     clipboard, monkeypatch):
    """The order is the whole point: let go, paste, put back."""
    order = []
    monkeypatch.setattr(dic, "_send_key",
                        lambda vk, down: order.append("modifier"))
    monkeypatch.setattr(_Keys, "press",
                        lambda self, key: order.append("key"))
    monkeypatch.setattr(_Keys, "release", lambda self, key: None)

    dic.DictationModule._paste_via_clipboard("Hallo", keep=True)

    assert order[0] == "modifier", "released before the shortcut"
    assert order[-1] == "modifier", "restored after it"
    assert "key" in order


# -- console windows ---------------------------------------------------------

@pytest.mark.parametrize("cls", [
    "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",
    "VirtualConsoleClass", "mintty", "PuTTY",
])
def test_console_windows_are_recognised(monkeypatch, cls):
    monkeypatch.setattr(dic, "window_class", lambda _h: cls)
    assert dic.is_terminal_window(1234)


@pytest.mark.parametrize("cls", ["Notepad", "Chrome_WidgetWin_1", "", "Qt5152"])
def test_ordinary_windows_are_not(monkeypatch, cls):
    monkeypatch.setattr(dic, "window_class", lambda _h: cls)
    assert not dic.is_terminal_window(1234)


def test_a_console_gets_ctrl_shift_v(monkeypatch, keys, clipboard):
    monkeypatch.setattr(dic, "window_class", lambda _h: "ConsoleWindowClass")
    dic.DictationModule._paste_via_clipboard("Hallo", keep=True, hwnd=7)
    assert keys == [("press", "ctrl"), ("press", "shift"), ("press", "v"),
                    ("release", "v"), ("release", "shift"), ("release", "ctrl")]


def test_everything_else_gets_ctrl_v(monkeypatch, keys, clipboard):
    monkeypatch.setattr(dic, "window_class", lambda _h: "Notepad")
    dic.DictationModule._paste_via_clipboard("Hallo", keep=True, hwnd=7)
    assert keys == [("press", "ctrl"), ("press", "v"),
                    ("release", "v"), ("release", "ctrl")]


# -- the clipboard is still put back -----------------------------------------

def test_the_previous_clipboard_comes_back(keys, clipboard):
    import time
    before = clipboard["value"]
    dic.DictationModule._paste_via_clipboard("Diktierter Text")
    assert clipboard["value"] == "Diktierter Text"
    time.sleep(0.6)
    assert clipboard["value"] == before, "an earlier copy must not be lost"


def test_keeping_the_text_leaves_it_on_the_clipboard(keys, clipboard):
    import time
    dic.DictationModule._paste_via_clipboard("Diktierter Text", keep=True)
    time.sleep(0.6)
    assert clipboard["value"] == "Diktierter Text"


# -- typing instead of pasting ----------------------------------------------

def test_typing_also_lets_go_of_the_modifiers(app, keys, modifiers,
                                              monkeypatch):
    """With Ctrl latched, typing "abc" would fire Ctrl+A, Ctrl+B, Ctrl+C."""
    m = dic.DictationModule()
    m._settings["insert_method"] = "type"
    monkeypatch.setattr(dic, "PYNPUT_AVAILABLE", True)
    monkeypatch.setattr(m, "_join_direct", lambda text: text)
    monkeypatch.setattr(m, "_remember_direct", lambda _text: None)

    m._insert_text("abc")

    assert ("type", "abc") in keys
    assert modifiers == [(0xA2, False), (0xA2, True)]
