"""A long wait must say what it is waiting for.

Loading a speech model is the slowest thing the dictation module does: on
large-v3 that is a one-off download of about three gigabytes, and roughly the
same amount pushed into the graphics card every time the model is loaded.
Both of those showed the identical words the app uses for a two-second
transcription - "Erkenne Text ..." - with no counter and no progress.  The
only available conclusion was that the program had hung.

So: name the model, say which of the two waits this is, and keep a number
moving while it lasts.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.core.event_bus import bus  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _german():
    """These tests read user-visible German text, so pin the language."""
    import dict_i18n
    import module as dic
    before = dic._lang.code
    bus.publish("i18n.language_changed", lang="de")
    assert dict_i18n._lang.code == "de"
    yield
    bus.publish("i18n.language_changed", lang=before)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point EVERY Hugging Face cache path at a folder of our own.

    Overriding only HF_HUB_CACHE was not enough: the lookup also falls back to
    the real ~/.cache/huggingface, so these tests read whatever models the
    person running them happens to have downloaded - and started failing the
    day one more model appeared there."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))


@pytest.fixture
def states():
    """Every dictation.state the module publishes during a test."""
    seen = []

    def listen(state="", detail="", **_):
        seen.append((state, detail))

    bus.subscribe("dictation.state", listen)
    yield seen
    try:
        bus.unsubscribe("dictation.state", listen)
    except Exception:
        pass


# -- is the model already here, or is this a download? -----------------------

def _fake_cache(tmp_path, model, complete=True):
    """Build the directory layout Hugging Face leaves behind."""
    repo = tmp_path / f"models--Systran--faster-whisper-{model}"
    (repo / "blobs").mkdir(parents=True)
    if complete:
        snap = repo / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"not really a model")
    else:
        # What an interrupted download actually looks like: bytes in blobs,
        # no snapshot yet.
        (repo / "blobs" / "deadbeef.incomplete").write_bytes(b"half of it")
    return repo


def test_a_cached_model_is_recognised(tmp_path, monkeypatch):
    import module as dic
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _fake_cache(tmp_path, "large-v3")
    assert dic.model_is_downloaded("large-v3")


def test_an_unfinished_download_does_not_count_as_present(tmp_path, monkeypatch):
    import module as dic
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _fake_cache(tmp_path, "medium", complete=False)
    assert not dic.model_is_downloaded("medium")


def test_a_missing_model_is_a_download():
    import module as dic
    assert not dic.model_is_downloaded("small")


def test_a_model_given_as_a_folder_needs_no_download(tmp_path):
    import module as dic
    assert dic.model_is_downloaded(str(tmp_path))


# -- how big is that download ------------------------------------------------

def test_the_size_is_written_the_way_the_language_writes_it():
    import module as dic
    assert dic._model_size_text("large-v3") == "3,1 GB"      # German comma
    assert dic._model_size_text("tiny") == "75 MB"
    bus.publish("i18n.language_changed", lang="en")
    assert dic._model_size_text("large-v3") == "3.1 GB"
    bus.publish("i18n.language_changed", lang="de")


def test_an_unknown_model_claims_no_size():
    import module as dic
    assert dic._model_size_text("something-new") == ""


# -- what the user is told ---------------------------------------------------

def test_a_download_says_so_and_says_how_big(app, states, monkeypatch):
    import module as dic
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: False)

    m._state = "transcribing"
    with m._model_phase("large-v3"):
        state, detail = states[-1]
        assert state == "loading"
        assert "large-v3" in detail
        assert "3,1 GB" in detail, "the size is the whole point of warning"
        assert "heruntergeladen" in detail

    assert states[-1][0] == "transcribing", "the wait must hand the state back"


def test_loading_into_the_graphics_card_is_named_as_that(app, states,
                                                         monkeypatch):
    import module as dic
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: True)
    monkeypatch.setattr(m, "_whisper_device", lambda: ("cuda", "float16"))

    m._state = "transcribing"
    with m._model_phase("large-v3"):
        state, detail = states[-1]
    assert state == "loading"
    assert "Grafikspeicher" in detail
    assert "3,1 GB" not in detail, "nothing is downloaded here - do not say so"


def test_without_a_gpu_it_says_memory_instead(app, states, monkeypatch):
    import module as dic
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: True)
    monkeypatch.setattr(m, "_whisper_device", lambda: ("cpu", "int8"))

    m._state = "transcribing"
    with m._model_phase("small"):
        assert "Arbeitsspeicher" in states[-1][1]


def test_the_state_is_handed_back_even_when_loading_fails(app, states,
                                                          monkeypatch):
    """A failed load must not leave the chip stuck on "loading" forever."""
    import module as dic
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: True)
    monkeypatch.setattr(m, "_whisper_device", lambda: ("cpu", "int8"))

    m._state = "transcribing"
    with pytest.raises(RuntimeError):
        with m._model_phase("small"):
            raise RuntimeError("no disk space")
    assert states[-1][0] == "transcribing"


def test_an_already_loaded_model_says_nothing(app, states, monkeypatch):
    """No message when there is no wait - otherwise it becomes noise."""
    import module as dic
    m = dic.DictationModule()
    m._local_model = object()
    m._local_model_name = "small"
    monkeypatch.setattr(m, "_load_model", lambda name: m._local_model)

    m._ensure_model_loaded("small", announce=True)
    assert not [s for s in states if s[0] == "loading"]


# -- the chip and the window -------------------------------------------------

def test_the_chip_shows_the_message_and_a_running_counter(app):
    import time

    import module as dic
    chip = dic.DictationIndicator()
    chip._apply_state("loading", "Sprachmodell large-v3 wird geladen")

    label = chip._label()
    assert "Sprachmodell large-v3 wird geladen" in label
    assert label.rstrip().endswith("s"), "a wait needs a number that moves"

    # Wind the clock back and confirm the number actually follows it.
    chip._phase_started = time.monotonic() - 42
    assert "42 s" in chip._label()
    chip.close()


def test_the_window_shows_the_whole_message(app):
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda _t: None, on_copy=lambda _t: None)
    win._apply_state("loading",
                     "Sprachmodell large-v3 wird in den Grafikspeicher geladen")
    assert ("Sprachmodell large-v3 wird in den Grafikspeicher geladen"
            in win._status.text())
    assert win._rec_timer.isActive(), "the seconds counter has to run"
    win.close()


# -- the packaged .exe takes the other road ----------------------------------

def test_the_worker_start_is_announced_too(app):
    """In the .exe the model is loaded by a subprocess - the same wait, and
    for a long time the same silence."""
    import module as dic
    proc = dic.WhisperProc()
    announced = []

    import contextlib

    @contextlib.contextmanager
    def phase(model):
        announced.append(model)
        yield

    proc.on_phase = phase
    proc.start = lambda model, threads: True

    assert proc._start_announced("medium", 4) is True
    assert announced == ["medium"]


def test_a_warm_up_nobody_is_waiting_for_stays_quiet(app):
    import module as dic
    proc = dic.WhisperProc()
    started = []
    proc.start = lambda model, threads: started.append(model) or True
    # on_phase is None during the background warm-up.
    assert proc._start_announced("medium", 4)
    assert started == ["medium"]


def test_a_model_that_has_to_be_loaded_announces_itself(app, states,
                                                        monkeypatch):
    import module as dic
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: True)
    monkeypatch.setattr(m, "_whisper_device", lambda: ("cpu", "int8"))
    loaded = []

    def load(name):
        # The state must already say "loading" while this runs - that is the
        # whole point; announcing it afterwards would help nobody.
        loaded.append((name, states[-1][0]))
        return object()

    monkeypatch.setattr(m, "_load_model", load)
    m._local_model = None
    m._ensure_model_loaded("medium", announce=True)

    assert loaded == [("medium", "loading")]


def test_the_real_dictation_path_switches_the_announcement_on(app, monkeypatch):
    """Without this, every message above would be dead code."""
    import module as dic
    m = dic.DictationModule()
    calls = {}

    monkeypatch.setattr(m, "_local_in_process", lambda: True)
    monkeypatch.setattr(m, "_ensure_model_loaded",
                        lambda name=None, **kw: calls.update(kw) or object())
    monkeypatch.setattr(m, "_local_language", lambda: "de")
    # Stop before the actual transcription; the model step is what is tested.
    monkeypatch.setattr(m, "_hotwords", lambda: (_ for _ in ()).throw(
        RuntimeError("far enough")))

    with pytest.raises(RuntimeError, match="far enough"):
        m._transcribe_local(b"\x00" * 1000)

    assert calls.get("announce") is True


def test_a_load_during_a_recording_does_not_restart_the_clock(app):
    """Live mode loads the model on the first chunk, mid-recording.  Coming
    back from that must not tell the user they have been talking for 0 s."""
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda _t: None, on_copy=lambda _t: None)

    win._apply_state("recording", "Diktat")
    for _ in range(12):
        win._tick_recording()
    assert "12 s" in win._status.text()

    win._apply_state("loading", "Sprachmodell wird geladen")
    assert "0 s" in win._status.text()          # the load has its own clock

    win._apply_state("recording", "Diktat")
    assert "12 s" in win._status.text(), "the recording clock resumes"
    win.close()
