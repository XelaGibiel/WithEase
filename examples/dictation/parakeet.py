"""Talks to the Parakeet worker (see parakeet_worker.py).

Parakeet runs in a separate test environment, so WithEase has to find it.
It looks, in this order, at:

  1. the environment variable ``WITHEASE_PARAKEET_DIR``,
  2. a one-line file ``%APPDATA%/WithEase/parakeet-test.txt`` naming the
     folder (so the ~4 GB can live on another drive),
  3. ``%APPDATA%/WithEase/parakeet-test``.

That folder holds ``venv`` (Python with onnx-asr) and ``model``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import threading

_log = logging.getLogger(__name__)

_START_TIMEOUT = 600          # a first start may still download the model


def _withease_dir() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "WithEase") if appdata else ""


def env_dir() -> str:
    """The Parakeet test folder, or "" when there is none."""
    candidates = [os.environ.get("WITHEASE_PARAKEET_DIR", "")]
    pointer = os.path.join(_withease_dir(), "parakeet-test.txt")
    try:
        with open(pointer, encoding="utf-8") as fh:
            candidates.append(fh.read().strip())
    except OSError:
        pass
    candidates.append(os.path.join(_withease_dir(), "parakeet-test"))
    for folder in candidates:
        if folder and os.path.isfile(python_in(folder)):
            return folder
    return ""


def python_in(folder: str) -> str:
    return os.path.join(folder, "venv", "Scripts", "python.exe")


def available() -> bool:
    return bool(env_dir())


CANARY_MODEL = "nemo-canary-1b-v2"
CANARY_DIR = "canary-model"


def canary_available() -> bool:
    """Canary sits in the same test folder, in ``canary-model``."""
    folder = env_dir()
    return bool(folder) and os.path.isfile(
        os.path.join(folder, CANARY_DIR, "encoder-model.onnx"))


def canary_engine() -> "ParakeetEngine":
    """NVIDIA's sister model: slower than Parakeet, but it can be told the
    language, so a single German word is never read as English."""
    return ParakeetEngine(model_dir=CANARY_DIR, model_name=CANARY_MODEL)


class ParakeetEngine:
    """The worker process, started once and kept warm."""

    def __init__(self, folder: str = "", model_dir: str = "model",
                 model_name: str = "") -> None:
        self._folder = folder or env_dir()
        self._model_dir = model_dir
        self._model_name = model_name
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        # start() may be called by the start-up preload and by a dictation
        # at the same moment; only one worker may come of it.
        self._start_lock = threading.Lock()
        self._ready = False
        self._next_id = 0
        self.device = ""

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ready(self) -> bool:
        """Started AND the model is loaded."""
        return self._ready and self.alive()

    def start(self) -> None:
        """Start the worker and wait for the model.  Raises on failure.
        A second caller waits for the first instead of starting another."""
        with self._start_lock:
            if self.ready():
                return
            self._start()

    def _start(self) -> None:
        self._ready = False
        if not self._folder:
            raise RuntimeError("Parakeet-Testumgebung nicht gefunden")
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "parakeet_worker.py")
        name = self._model_name or "parakeet"
        log_path = os.path.join(self._folder, f"worker-{name}.log")
        self._log_file = open(log_path, "w", encoding="utf-8")
        env = dict(os.environ)
        if self._model_name:
            env["WITHEASE_ASR_MODEL"] = self._model_name
        self._proc = subprocess.Popen(
            [python_in(self._folder), worker,
             os.path.join(self._folder, self._model_dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._log_file, text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ready = self._read_line(_START_TIMEOUT)
        if not ready.get("ready"):
            self.stop()
            raise RuntimeError("Parakeet startet nicht: "
                               + str(ready.get("error", "keine Antwort")))
        self.device = str(ready.get("device", ""))
        self._ready = True
        _log.info("%s worker ready on %s", name, self.device)

    def _read_line(self, timeout: float) -> dict:
        box: list[str] = []

        def read() -> None:
            try:
                box.append(self._proc.stdout.readline())
            except Exception:
                box.append("")
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout)
        if not box or not box[0]:
            return {}
        try:
            return json.loads(box[0])
        except ValueError:
            return {}

    def transcribe(self, pcm16: bytes, final: bool = False,
                   language: str = "") -> str:
        with self._lock:
            if not self.ready():
                self.start()
            self._next_id += 1
            # Half a second of silence either side: without it a short word
            # comes back as its English look-alike ("Had" for "hat", "Nine"
            # for "nein") or as nothing at all.
            pad = b"\x00\x00" * 8000
            request = {"id": self._next_id,
                       "pcm": base64.b64encode(pad + pcm16 + pad).decode(
                           "ascii")}
            if language:
                request["language"] = language
            self._proc.stdin.write(json.dumps(request) + "\n")
            self._proc.stdin.flush()
            answer = self._read_line(60)
            if "error" in answer:
                raise RuntimeError(answer["error"])
            return str(answer.get("text", ""))

    def stop(self) -> None:
        self._ready = False
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        log_file = getattr(self, "_log_file", None)
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
