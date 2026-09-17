"""whisper.cpp as a second local engine.

Why a second one at all: CTranslate2 - what faster-whisper runs on - does its
GPU work on NVIDIA cards only.  On an AMD or an Intel graphics chip WithEase
falls back to the processor, and a large model on a processor means minutes
instead of seconds.  whisper.cpp brings Vulkan, ROCm, Metal and OpenVINO
along, so it covers exactly the machines we cannot serve today - and nobody
knows what is inside the next computer WithEase is installed on.

faster-whisper stays the default wherever it works.  Two things this engine
cannot do, and the module switches them off rather than pretending:

  * **No "hotwords".**  Your own words, learned corrections and macro names
    reach faster-whisper as a direct bias; whisper.cpp only has the prompt.
  * **No per-word probabilities** in the server's answer, so the yellow
    "unsure" marks and the trailing-word trim stay off with this engine.

It talks to ``whisper-server``, the binary that ships with whisper.cpp, over
HTTP on the loopback interface: started once, the model stays loaded between
dictations.  A process per dictation would reload the model every time, which
for a large model is a quarter of a minute per sentence.

Nothing here downloads a program.  whisper.cpp publishes no binaries with its
releases, so the path to one is a setting; the models are downloaded and
verified against the SHA-1 checksums whisper.cpp publishes for them.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import subprocess
import sys
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)

# The models whisper.cpp offers, with the checksums from its models/README.md.
# Quantised variants first: they are the reason this engine is interesting on
# a weaker machine - large-v3 in q5_0 is 1.1 GiB instead of 2.9.
MODELS: dict[str, tuple[str, str]] = {
    # name:                 (size for humans, SHA-1)
    "large-v3-turbo-q5_0": ("547 MB", "e050f7970618a659205450ad97eb95a18d69c9ee"),
    "large-v3-q5_0":       ("1,1 GB", "e6e2ed78495d403bef4b7cff42ef4aaadcfea8de"),
    "large-v3-turbo":      ("1,5 GB", "4af2b29d7ec73d781377bfd1758ca957a807e941"),
    "large-v3":            ("2,9 GB", "ad82bf6a9043ceed055076d0fd39f5f186ff8062"),
    "medium":              ("1,5 GB", "fd9727b6e1217c2f614f9b698455c4ffd82463b4"),
    "small":               ("466 MB", "55356645c2b361a969dfd0ef2c5a50d530afd8d5"),
    "base":                ("142 MB", "465707469ff3a37a2b9b8d8f89f2f99de7299dac"),
    "tiny":                ("75 MB", "bd577a113a864445d4c299885e0cb97d4ba92b5f"),
}
DEFAULT_MODEL = "large-v3-turbo-q5_0"

_MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
              "ggml-{name}.bin")
_BINARY_NAMES = ("whisper-server.exe", "whisper-server")
# Loading a large model can take a while before the server answers at all.
_START_TIMEOUT = 300
_REQUEST_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Where the program and the models live
# ---------------------------------------------------------------------------

def data_dir() -> str:
    """``%APPDATA%/WithEase`` - the same place the rest of the module uses."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.dirname(os.path.dirname(here))       # …/WithEase
    if os.path.basename(candidate).lower() == "withease":
        return candidate
    appdata = os.environ.get("APPDATA")
    return os.path.join(appdata, "WithEase") if appdata else candidate


def model_dir() -> str:
    return os.path.join(data_dir(), "whispercpp")


def model_path(name: str) -> str:
    return os.path.join(model_dir(), f"ggml-{name}.bin")


def model_installed(name: str) -> bool:
    return os.path.isfile(model_path(name))


def installed_models() -> list[str]:
    return [name for name in MODELS if model_installed(name)]


def find_binary(configured: str = "") -> str:
    """The whisper-server to use: the configured one, one we keep beside the
    program, or one on PATH.  "" when there is none."""
    if configured and os.path.isfile(configured):
        return configured
    for name in _BINARY_NAMES:
        beside = os.path.join(data_dir(), "bin", name)
        if os.path.isfile(beside):
            return beside
    import shutil
    for name in _BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return ""


def available(configured_binary: str = "", model: str = "") -> bool:
    """True when this engine could actually run right now."""
    return bool(find_binary(configured_binary)) and model_installed(
        model or DEFAULT_MODEL)


# ---------------------------------------------------------------------------
# Getting a model
# ---------------------------------------------------------------------------

def download_model(name: str, progress: Callable[[int], None] | None = None,
                   url: str = "") -> str:
    """Download a ggml model and verify it.  Returns the path it was saved to.

    Verified against the SHA-1 whisper.cpp publishes for that file, and only
    moved into place once it matches - a half-finished or tampered download
    must never end up looking like a working model.
    """
    if name not in MODELS:
        raise ValueError(f"unknown model: {name}")
    expected = MODELS[name][1]
    os.makedirs(model_dir(), exist_ok=True)
    target = model_path(name)
    partial = target + ".part"

    import requests
    with requests.get(url or _MODEL_URL.format(name=name), stream=True,
                      timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        digest = hashlib.sha1()
        done = 0
        with open(partial, "wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(99, int(done * 100 / total)))

    if digest.hexdigest() != expected:
        os.unlink(partial)
        raise RuntimeError(
            f"the downloaded model does not match its checksum ({name})")
    os.replace(partial, target)
    if progress:
        progress(100)
    return target


def delete_model(name: str) -> str:
    """Put a downloaded model aside.  Returns the path it was moved to, "".

    Moved rather than deleted, so "Rückgängig" can bring a gigabyte back
    without downloading it again - the same rule as everywhere else in
    WithEase."""
    import datetime
    path = model_path(name)
    if not os.path.isfile(path):
        return ""
    aside = f"{path}.geloescht-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    try:
        os.rename(path, aside)
    except OSError:
        _log.exception("could not move the model aside: %r", path)
        return ""
    return aside


def restore_model(aside: str) -> bool:
    """Move a set-aside model back."""
    target = str(aside).split(".geloescht-")[0]
    if aside and os.path.isfile(aside) and not os.path.exists(target):
        try:
            os.rename(aside, target)
            return True
        except OSError:
            _log.exception("could not bring the model back: %r", aside)
    return False


def purge_model(aside: str) -> None:
    """Finally remove a set-aside model, once undo has expired."""
    try:
        if aside and os.path.isfile(aside):
            os.unlink(aside)
    except OSError:
        _log.exception("could not remove %r", aside)


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """A port nothing else is on - whisper-server's own default (8080) is a
    popular one, and taking it would collide with whatever is already there."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    """A whisper-server process, started once and kept warm."""

    def __init__(self) -> None:
        self._proc: Any = None
        self._port = 0
        self._model = ""

    # -- lifecycle ------------------------------------------------------

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def model(self) -> str:
        return self._model

    def start(self, binary: str, model_file: str, threads: int = 4,
              use_gpu: bool = True) -> bool:
        """Start the server and wait until it answers.  False on any failure."""
        if not binary or not os.path.isfile(model_file):
            return False
        port = _free_port()
        command = [binary, "--model", model_file, "--host", "127.0.0.1",
                   "--port", str(port), "--threads", str(max(1, threads))]
        if not use_gpu:
            command.append("--no-gpu")
        try:
            self._proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            _log.exception("could not start whisper-server")
            self._proc = None
            return False
        self._port = port
        if not self._wait_until_ready():
            self.stop()
            return False
        self._model = model_file
        return True

    def _wait_until_ready(self, timeout: int = _START_TIMEOUT) -> bool:
        """whisper-server loads the model BEFORE it listens, so a connection
        that succeeds means the model is in memory."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive():
                return False
            try:
                with socket.create_connection(("127.0.0.1", self._port), 0.5):
                    return True
            except OSError:
                time.sleep(0.25)
        return False

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self._model = ""
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # -- using it -------------------------------------------------------

    def transcribe(self, wav_bytes: bytes, language: str = "",
                   prompt: str = "") -> str:
        """Send one recording and return the recognised text."""
        if not self.alive():
            return ""
        import requests
        data = {"response_format": "json", "temperature": "0.0"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        try:
            response = requests.post(
                f"http://127.0.0.1:{self._port}/inference",
                files={"file": ("dictation.wav", wav_bytes, "audio/wav")},
                data=data, timeout=_REQUEST_TIMEOUT)
            response.raise_for_status()
            return _text_from(response.json())
        except Exception:
            _log.exception("whisper-server request failed")
            return ""


def _text_from(payload: Any) -> str:
    """The text out of whatever shape the answer has.

    The server speaks the OpenAI shape ({"text": …}); older builds and the
    verbose format put the words in segments instead.  Both are read rather
    than insisting on one.
    """
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    segments = payload.get("segments") or payload.get("transcription") or []
    parts = []
    for segment in segments if isinstance(segments, list) else []:
        if isinstance(segment, dict):
            piece = segment.get("text") or segment.get("sentence") or ""
            if isinstance(piece, str):
                parts.append(piece.strip())
        elif isinstance(segment, str):
            parts.append(segment.strip())
    return " ".join(part for part in parts if part).strip()


def describe_platform() -> str:
    """One line for the settings page: what this engine would run on."""
    if sys.platform == "darwin":
        return "Metal"
    return "Vulkan / CUDA / ROCm"
