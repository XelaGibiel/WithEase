"""whisper.cpp as the second local engine.

faster-whisper only uses a graphics card when it is an NVIDIA one; everywhere
else it falls back to the processor, where a large model takes minutes.
whisper.cpp covers AMD, Intel and Apple hardware as well - and nobody knows
what is inside the next machine WithEase is installed on.  It stays the second
choice: it has no word biasing and returns no per-word confidence, so two
features are switched off with it rather than quietly producing worse
results.

Nothing here needs the whisper.cpp program: the server is faked.
"""
import hashlib
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import module as dic  # noqa: E402
import whispercpp  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A WithEase data directory of our own."""
    monkeypatch.setattr(whispercpp, "data_dir", lambda: str(tmp_path))
    return tmp_path


# -- the published model list ------------------------------------------------

def test_every_model_carries_its_checksum():
    for name, (size, sha) in whispercpp.MODELS.items():
        assert len(sha) == 40 and int(sha, 16) >= 0, name
        assert size and any(unit in size for unit in ("MB", "GB")), name


def test_the_quantised_models_are_offered_first():
    """They are the reason this engine is interesting on a weak machine."""
    assert next(iter(whispercpp.MODELS)).endswith("q5_0")
    assert whispercpp.DEFAULT_MODEL in whispercpp.MODELS


# -- finding the program -----------------------------------------------------

def test_a_configured_path_wins(home, tmp_path):
    binary = tmp_path / "anywhere" / "whisper-server.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"")
    assert whispercpp.find_binary(str(binary)) == str(binary)


def test_one_next_to_withease_is_found(home):
    binary = home / "bin" / "whisper-server.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"")
    assert whispercpp.find_binary() == str(binary)


def test_no_program_is_reported_honestly(home, monkeypatch):
    monkeypatch.setattr(whispercpp.shutil if hasattr(whispercpp, "shutil")
                        else whispercpp, "which", lambda _n: None,
                        raising=False)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert whispercpp.find_binary() == ""
    assert not whispercpp.available()


# -- downloading a model -----------------------------------------------------

class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=0):
        yield self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _serve(monkeypatch, payload: bytes):
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda *_a, **_k: _Response(payload))


def test_a_model_is_verified_before_it_counts(home, monkeypatch):
    payload = b"pretend model"
    monkeypatch.setitem(whispercpp.MODELS, "tiny",
                        ("75 MB", hashlib.sha1(payload).hexdigest()))
    _serve(monkeypatch, payload)

    seen = []
    path = whispercpp.download_model("tiny", progress=seen.append)

    assert os.path.isfile(path)
    assert whispercpp.model_installed("tiny")
    assert seen and seen[-1] == 100


def test_a_wrong_checksum_is_refused_and_leaves_nothing_behind(home,
                                                               monkeypatch):
    """A tampered or half-finished file must never look like a model."""
    monkeypatch.setitem(whispercpp.MODELS, "tiny", ("75 MB", "00" * 20))
    _serve(monkeypatch, b"something else")

    with pytest.raises(RuntimeError, match="checksum"):
        whispercpp.download_model("tiny")

    assert not whispercpp.model_installed("tiny")
    assert os.listdir(whispercpp.model_dir()) == []


def test_an_unknown_model_is_not_downloaded(home):
    with pytest.raises(ValueError):
        whispercpp.download_model("gibberish")


def test_a_model_can_be_removed_again(home, monkeypatch):
    payload = b"pretend model"
    monkeypatch.setitem(whispercpp.MODELS, "tiny",
                        ("75 MB", hashlib.sha1(payload).hexdigest()))
    _serve(monkeypatch, payload)
    whispercpp.download_model("tiny")

    aside = whispercpp.delete_model("tiny")
    assert aside and os.path.isfile(aside), "moved aside, not erased"
    assert not whispercpp.model_installed("tiny")

    assert whispercpp.restore_model(aside) is True
    assert whispercpp.model_installed("tiny")

    whispercpp.purge_model(whispercpp.delete_model("tiny"))
    assert not whispercpp.model_installed("tiny")


# -- talking to the server ---------------------------------------------------

@pytest.mark.parametrize("payload, want", [
    ({"text": " Straße 3 "}, "Straße 3"),
    ({"text": "", "segments": [{"text": "Hallo"}, {"text": " Welt"}]},
     "Hallo Welt"),
    ({"transcription": [{"text": "Nur"}, {"text": "Segmente"}]},
     "Nur Segmente"),
    ("Straße 3", "Straße 3"),
    ({}, ""),
    (None, ""),
])
def test_the_answer_is_read_in_every_shape(payload, want):
    assert whispercpp._text_from(payload) == want


def test_a_recording_is_sent_with_language_and_prompt(monkeypatch):
    sent = {}

    class _Posted:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "Hallo"}

    def post(url, files=None, data=None, timeout=None):
        sent.update(url=url, files=files, data=data)
        return _Posted()

    import requests
    monkeypatch.setattr(requests, "post", post)

    server = whispercpp.Server()
    server._proc = type("P", (), {"poll": lambda self: None})()
    server._port = 1234

    assert server.transcribe(b"RIFFwav", language="de",
                             prompt="Diktat") == "Hallo"
    assert "/inference" in sent["url"]
    assert sent["data"]["language"] == "de"
    assert sent["data"]["prompt"] == "Diktat"
    assert sent["files"]["file"][1] == b"RIFFwav"


def test_a_server_that_is_not_running_answers_nothing():
    assert whispercpp.Server().transcribe(b"RIFFwav") == ""


# -- when the module uses it -------------------------------------------------

def _module(app, monkeypatch, engine="auto", faster=True, cpp=True):
    m = dic.DictationModule()
    m._settings["local_engine"] = engine
    monkeypatch.setattr(m, "_faster_whisper_usable", lambda: faster)
    monkeypatch.setattr(whispercpp, "available", lambda *_a, **_k: cpp)
    return m


def test_automatic_keeps_faster_whisper_when_it_works(app, monkeypatch):
    m = _module(app, monkeypatch, "auto", faster=True, cpp=True)
    assert not m._use_whispercpp(), "the engine must not change by itself"


def test_automatic_falls_back_when_faster_whisper_cannot_run(app, monkeypatch):
    m = _module(app, monkeypatch, "auto", faster=False, cpp=True)
    assert m._use_whispercpp()


def test_automatic_stays_put_when_neither_is_ready(app, monkeypatch):
    m = _module(app, monkeypatch, "auto", faster=False, cpp=False)
    assert not m._use_whispercpp()


def test_the_choice_is_honoured_both_ways(app, monkeypatch):
    assert _module(app, monkeypatch, "whispercpp", faster=True)._use_whispercpp()
    assert not _module(app, monkeypatch, "faster-whisper",
                       faster=False)._use_whispercpp()


def test_a_missing_program_says_so_instead_of_failing_silently(app,
                                                               monkeypatch):
    m = _module(app, monkeypatch, "whispercpp")
    monkeypatch.setattr(whispercpp, "find_binary", lambda _c="": "")
    with pytest.raises(dic.ConfigError):
        m._transcribe_via_whispercpp(b"RIFF")


def test_a_missing_model_says_so_too(app, monkeypatch, home):
    m = _module(app, monkeypatch, "whispercpp")
    monkeypatch.setattr(whispercpp, "find_binary", lambda _c="": "whisper.exe")
    monkeypatch.setattr(whispercpp, "model_installed", lambda _n: False)
    with pytest.raises(dic.ConfigError):
        m._transcribe_via_whispercpp(b"RIFF")


def test_the_uncertainty_marks_are_switched_off_with_this_engine(app,
                                                                 monkeypatch):
    """No per-word probabilities means no honest yellow marks."""
    m = _module(app, monkeypatch, "whispercpp")
    m._last_low_words = ["Bericht"]

    class _Server:
        def alive(self):
            return True

        def model(self):
            return whispercpp.model_path("base")

        def transcribe(self, *_a, **_k):
            return "Der Bericht ist da."

        def stop(self):
            return None

    monkeypatch.setattr(whispercpp, "find_binary", lambda _c="": "whisper.exe")
    monkeypatch.setattr(whispercpp, "model_installed", lambda _n: True)
    m._settings["whispercpp_model"] = "base"
    m._whispercpp = _Server()

    text = m._transcribe_via_whispercpp(b"RIFF")

    assert "Bericht" in text
    assert m._last_low_words == []


# -- the settings page -------------------------------------------------------

def test_the_whispercpp_rows_only_appear_when_they_matter(app, monkeypatch):
    """Nobody on a working NVIDIA machine needs to read about a second
    engine, so the rows stay out of the way until they are chosen."""
    m = dic.DictationModule()
    monkeypatch.setattr(m, "_faster_whisper_usable", lambda: True)
    page = m.get_settings_widget()
    assert not page._cpp_note.isVisibleTo(page)

    page._engine.setCurrentIndex(page._engine.findData("whispercpp"))
    assert page._cpp_note.isVisibleTo(page)
    assert m._settings["local_engine"] == "whispercpp"
    page.deleteLater()


def test_they_appear_by_themselves_when_faster_whisper_cannot_run(app,
                                                                  monkeypatch):
    m = dic.DictationModule()
    monkeypatch.setattr(m, "_faster_whisper_usable", lambda: False)
    page = m.get_settings_widget()
    assert page._cpp_note.isVisibleTo(page), "this machine has nothing else"
    page.deleteLater()


def test_the_model_list_shows_sizes_and_what_is_already_there(app, home,
                                                              monkeypatch):
    monkeypatch.setattr(whispercpp, "model_installed",
                        lambda name: name == "base")
    m = dic.DictationModule()
    page = m.get_settings_widget()
    box = page._cpp_model
    labels = [box.itemText(i) for i in range(box.count())]
    assert any("q5_0" in text and "MB" in text for text in labels)

    # What is already downloaded carries a dot, the rest carries none.
    here = next(i for i in range(box.count()) if box.itemData(i) == "base")
    missing = next(i for i in range(box.count()) if box.itemData(i) == "medium")
    assert not box.itemIcon(here).isNull()
    assert box.itemIcon(missing).isNull()
    page.deleteLater()


def test_the_download_button_is_off_for_a_model_that_is_there(app, home,
                                                              monkeypatch):
    monkeypatch.setattr(whispercpp, "model_installed", lambda _n: True)
    m = dic.DictationModule()
    m._settings["local_engine"] = "whispercpp"
    page = m.get_settings_widget()
    assert not page._cpp_dl.isEnabled()
    page.deleteLater()


def test_the_program_row_says_whether_it_was_found(app, home, monkeypatch):
    monkeypatch.setattr(whispercpp, "find_binary", lambda _c="": "")
    m = dic.DictationModule()
    m._settings["local_engine"] = "whispercpp"
    page = m.get_settings_widget()
    assert page._cpp_path_status.text(), "silence would be the worst answer"
    page.deleteLater()
