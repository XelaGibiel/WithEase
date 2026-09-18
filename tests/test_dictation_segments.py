"""Normal dictation with the microphone left on: every longer pause turns
what was said so far into text, the recording goes on.

No model and no microphone: a fake recogniser and generated audio."""
import math
import os
import struct
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import streaming as st  # noqa: E402


def _tone(seconds, amplitude=6000):
    n = int(seconds * st.RATE)
    return b"".join(struct.pack("<h", int(amplitude * math.sin(i / 5)))
                    for i in range(n))


def _silence(seconds, level=40):
    n = int(seconds * st.RATE)
    return b"".join(struct.pack("<h", level if i % 2 else -level)
                    for i in range(n))


def _feed(session, audio, block=1600):
    for i in range(0, len(audio), block):
        session.put(audio[i:i + block])


class _Win:
    def __init__(self):
        self.got = []

    def handle_transcript(self, text, mode="auto", low=None):
        self.got.append((text, mode))

    def set_state(self, *_a):
        pass


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def module(app, monkeypatch):
    import module as dic
    m = dic.DictationModule()
    m._settings.update({"backend": "local", "output_mode": "window"})
    m._window = _Win()
    m._active_mode = "auto"
    # the speech detector without Silero: generated tones are not speech
    monkeypatch.setattr(st, "make_gate", lambda *_a, **_k: st.EnergyGate())
    heard = []

    def transcribe(wav):
        heard.append(len(wav))
        return f"Teil {len(heard)}."
    monkeypatch.setattr(m, "transcribe", transcribe)
    monkeypatch.setattr(m, "_refine_transcript", lambda text, **_k: text)
    m.heard = heard
    return m


def test_on_by_default_for_the_window_only(module):
    assert module._segment_wanted()
    module._active_mode = "command"
    assert not module._segment_wanted()          # commands stay one piece
    module._active_mode = "auto"
    module._settings["output_mode"] = "direct"
    assert not module._segment_wanted()
    module._settings["output_mode"] = "window"
    module._settings["segment_on_pause"] = False
    assert not module._segment_wanted()


def test_a_pause_converts_while_the_microphone_stays_on(module):
    module._settings["segment_pause"] = 1.0
    session = module._make_segmenter()
    session.start()
    _feed(session, _silence(0.3) + _tone(1.0) + _silence(1.3))
    _feed(session, _tone(0.8) + _silence(0.2))
    import time
    deadline = time.monotonic() + 5
    while not module._window.got and time.monotonic() < deadline:
        time.sleep(0.02)
    # the first part is text already - the second one is still being spoken
    assert module._window.got == [("Teil 1.", "auto")]
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert module._window.got == [("Teil 1.", "auto"), ("Teil 2.", "auto")]
    assert module._state == "idle"


def test_a_thinking_pause_stays_in_the_same_part(module):
    module._settings["segment_pause"] = 1.5
    session = module._make_segmenter()
    session.start()
    _feed(session, _tone(0.8) + _silence(0.8) + _tone(0.8) + _silence(0.2))
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert module._window.got == [("Teil 1.", "auto")]
    assert len(module.heard) == 1


def test_throwing_the_recording_away_delivers_nothing_more(module):
    session = module._make_segmenter()
    session.start()
    _feed(session, _tone(1.0))
    module._segmenter = session
    module._state = "recording"
    module._abort_recording()
    session.stop(timeout=5)
    assert module._window.got == []
    assert module._segmenter is None and module._state == "idle"


def test_nothing_said_at_all_is_reported(module, monkeypatch):
    said = []
    monkeypatch.setattr(module, "_say_nothing_heard", said.append)
    session = module._make_segmenter()
    session.start()
    _feed(session, _silence(1.0))
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert said == ["empty"] and module.heard == []


def test_the_settings_offer_it_only_without_the_live_test(app, module):
    page = module.get_settings_widget()
    assert page._segment_cb.isVisibleTo(page)
    assert page._segment_pause.isVisibleTo(page)
    page._segment_cb.setChecked(False)
    assert not page._segment_pause.isVisibleTo(page)
    assert module._settings["segment_on_pause"] is False
    page._stream_cb.setChecked(True)
    assert not page._segment_cb.isVisibleTo(page)
    page.deleteLater()


# -- the countdown while you pause ---------------------------------------------

def test_a_pause_counts_down_until_the_part_is_converted(module):
    shown = []
    session = st.StreamSession(lambda pcm, final: "x", lambda *_: None,
                               lambda _t: None, step_s=1e9, pause_s=1.0,
                               detector=st.EnergyGate(),
                               on_silence=shown.append)
    session.start()
    import time
    _feed(session, _tone(0.8))
    time.sleep(0.2)
    for _ in range(12):                  # a second and more of quiet, live
        _feed(session, _silence(0.1))
        time.sleep(0.03)
    session.stop(timeout=5)
    numbers = [s for s in shown if s is not None]
    assert numbers and numbers[0] <= 0.7          # only after a short gap
    assert numbers == sorted(numbers, reverse=True)
    assert shown[-1] is None                       # gone once converted


def test_speaking_again_takes_the_countdown_away(module):
    shown = []
    session = st.StreamSession(lambda pcm, final: "x", lambda *_: None,
                               lambda _t: None, step_s=1e9, pause_s=2.0,
                               detector=st.EnergyGate(),
                               on_silence=shown.append)
    session.start()
    import time
    _feed(session, _tone(0.8))
    time.sleep(0.2)
    _feed(session, _silence(0.6))
    time.sleep(0.2)
    _feed(session, _tone(0.3))
    time.sleep(0.2)
    assert any(s is not None for s in shown) and shown[-1] is None
    session.stop(timeout=5)


def test_the_chip_shows_the_seconds_left(app):
    import module as dic
    chip = dic.DictationIndicator()
    chip._apply_state("recording", "")
    assert chip._subtitle() == ""
    chip._apply_pause(1.4, 2.0)
    assert "1,4" in chip._subtitle() or "1.4" in chip._subtitle()
    width = chip.width()
    chip._apply_pause(0.3, 2.0)
    assert chip.width() == width                   # the line does not twitch
    chip._apply_pause(-1.0, 2.0)
    assert chip._subtitle() == ""
    chip._apply_pause(1.0, 2.0)
    chip._apply_state("transcribing", "")
    assert chip._subtitle() == ""
    chip.deleteLater()
