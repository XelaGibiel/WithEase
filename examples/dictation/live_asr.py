"""Streaming speech recognition via Vosk (offline, word-by-word).

Isolated + guarded so the rest of the module doesn't depend on Vosk being
installed.  ``VoskStreamer.accept`` returns ``(is_final, text)`` per audio chunk:
a growing *partial* while speaking, and the finalised text when a segment ends.
"""
from __future__ import annotations

import json
import os


class VoskUnavailable(Exception):
    """Vosk isn't installed, or no model was found."""


def models_dir(config_dir: str) -> str:
    return os.path.join(str(config_dir), "vosk_models")


def find_model(config_dir: str) -> str:
    """Return the first Vosk model folder under <config>/vosk_models, or ""."""
    base = models_dir(config_dir)
    try:
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            # a Vosk model folder contains an "am" or "conf" subfolder
            if os.path.isdir(path) and (
                    os.path.isdir(os.path.join(path, "am"))
                    or os.path.isdir(os.path.join(path, "conf"))):
                return path
    except OSError:
        pass
    return ""


class VoskStreamer:
    def __init__(self, model_path: str, sample_rate: int = 16000) -> None:
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError as exc:
            raise VoskUnavailable(
                "Vosk ist nicht installiert (pip install vosk)") from exc
        try:
            SetLogLevel(-1)     # silence Vosk's verbose model-loading LOG lines
        except Exception:
            pass
        if not model_path or not os.path.isdir(model_path):
            raise VoskUnavailable("Kein Vosk-Sprachmodell gefunden")
        self.model_path = model_path
        self._sample_rate = sample_rate
        self._model = Model(model_path)         # expensive – load once, reuse
        self._KaldiRecognizer = KaldiRecognizer
        self.reset()

    def reset(self) -> None:
        """Start a fresh recognition session without reloading the model
        (cheap), so repeated dictations don't reload ~GB from disk each time."""
        self._rec = self._KaldiRecognizer(self._model, self._sample_rate)
        self._rec.SetWords(False)

    def accept(self, pcm_bytes: bytes) -> tuple[bool, str]:
        """Feed one chunk of 16-bit mono PCM. Returns (is_final, text)."""
        if self._rec.AcceptWaveform(pcm_bytes):
            return True, json.loads(self._rec.Result()).get("text", "")
        return False, json.loads(self._rec.PartialResult()).get("partial", "")

    def final(self) -> str:
        """Flush and return any remaining finalised text."""
        return json.loads(self._rec.FinalResult()).get("text", "")
