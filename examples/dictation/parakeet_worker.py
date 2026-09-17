"""Parakeet (NVIDIA) as a recogniser for the live dictation test.

Runs in its OWN Python environment, not WithEase's: onnx-asr and the ONNX
runtime with its CUDA libraries are a trial, and nothing of the working
installation may change because of a trial.  WithEase starts this script
with that environment's interpreter and talks to it over stdin/stdout, one
JSON object per line:

    -> {"id": 1, "pcm": "<base64 of 16 kHz mono int16>"}
    <- {"id": 1, "text": "…", "ms": 123}

The first line it writes is {"ready": true, "device": "cuda"|"cpu"} once the
model is loaded, or {"ready": false, "error": "…"}.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time

MODEL = "nemo-parakeet-tdt-0.6b-v3"


# The answers get a channel of their own.  Libraries write to stdout while
# they load (ONNX Runtime's DLL loader prints a line per missing library),
# and a single stray line there would be read as an answer.  So the real
# stdout is kept for the protocol and everything else goes to stderr.
_PROTOCOL = None


def _claim_stdout() -> None:
    global _PROTOCOL
    _PROTOCOL = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr


def _say(obj: dict) -> None:
    _PROTOCOL.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _PROTOCOL.flush()


def _load(model_dir: str, device: str = "auto", quantization: str = ""):
    import onnx_asr
    import onnxruntime as ort
    # "auto": NVIDIA if it works, else DirectML (AMD/Intel graphics on
    # Windows), else the processor.  "cpu" forces the processor.
    wanted = {"cpu": ["CPUExecutionProvider"],
              "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
              "dml": ["DmlExecutionProvider", "CPUExecutionProvider"]}.get(
        device, ["CUDAExecutionProvider", "DmlExecutionProvider",
                 "CPUExecutionProvider"])
    providers = [p for p in wanted if p in ort.get_available_providers()]
    try:
        # The CUDA libraries come as pip packages; make them findable.
        ort.preload_dlls()
    except Exception:
        pass
    model = onnx_asr.load_model(MODEL, path=model_dir, providers=providers,
                                quantization=quantization or None)
    used = providers[0] if providers else "CPUExecutionProvider"
    device = {"CUDAExecutionProvider": "cuda",
              "DmlExecutionProvider": "dml"}.get(used, "cpu")
    return model, device


def main() -> int:
    _claim_stdout()
    model_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "parakeet-model")
    try:
        import numpy as np
        model, device = _load(
            model_dir, os.environ.get("WITHEASE_PARAKEET_DEVICE", "auto"),
            os.environ.get("WITHEASE_PARAKEET_QUANT", ""))
        model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000)
    except Exception as exc:                      # report, never hang
        _say({"ready": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
        return 1
    _say({"ready": True, "device": device})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            pcm = base64.b64decode(request.get("pcm", ""))
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            audio /= 32768.0
            started = time.perf_counter()
            text = model.recognize(audio, sample_rate=16000) if len(audio) \
                else ""
            _say({"id": request.get("id"), "text": str(text or "").strip(),
                  "ms": round((time.perf_counter() - started) * 1000)})
        except Exception as exc:
            _say({"id": None, "text": "", "error": str(exc)[:300]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
