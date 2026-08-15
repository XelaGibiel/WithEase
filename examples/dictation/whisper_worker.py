"""Out-of-process Whisper transcription worker.

faster-whisper (CTranslate2 + PyAV/ffmpeg + numpy) is run in *this* separate
process so it never shares a process – and thus native runtimes / OpenMP – with
Vosk in the main app.  Running both engines in one process crashed the whole
app on Windows; isolating Whisper here removes that entire class of crashes.

Protocol (line-based JSON over stdin/stdout):
  * on start the worker prints ``{"ready": true}`` (or ``{"ready": false, ...}``)
  * each request is one JSON object per line: ``{"wav": "<path>", "language":
    "de", "hotwords": "...", "initial_prompt": null, "live": true}``
  * each response is one JSON object per line: ``{"text": "...", "low": [...]}``
  * ``{"cmd": "quit"}`` ends the worker.
Anything the ML libraries print goes to stderr; stdout carries only JSON.
"""
from __future__ import annotations

import json
import os
import sys


def _ctranslate2_dir() -> str | None:
    """Locate CTranslate2's package folder without importing it."""
    import importlib.util
    try:
        spec = importlib.util.find_spec("ctranslate2")
    except Exception:
        return None
    for loc in getattr(spec, "submodule_search_locations", None) or []:
        return loc
    return None


def _find_nvidia_bin_dirs() -> list[str]:
    """All ``nvidia/*/bin`` directories under any known packages root.

    Robust on purpose: ``importlib.util.find_spec('nvidia.*')`` turned out to be
    environment-sensitive (it silently found nothing under some launchers), so
    we also scan every sys.path / site-packages root directly.
    """
    import glob
    import site
    roots: list[str] = list(sys.path)
    for getter in (getattr(site, "getsitepackages", None),
                   getattr(site, "getusersitepackages", None)):
        try:
            got = getter() if getter else None
        except Exception:
            got = None
        if isinstance(got, str):
            roots.append(got)
        elif got:
            roots.extend(got)
    dirs: list[str] = []
    seen: set[str] = set()
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        for d in glob.glob(os.path.join(r, "nvidia", "*", "bin")):
            key = os.path.normcase(d)
            if key not in seen:
                seen.add(key)
                dirs.append(d)
    return dirs


def _ensure_cuda_libs() -> None:
    """Make CTranslate2 find CUDA cuBLAS on Windows – permanently.

    CTranslate2 bundles cuDNN but NOT cuBLAS / the CUDA runtime, so GPU
    inference fails with "cublas64_12.dll is not found or cannot be loaded"
    until those are available. When they come from the ``nvidia-*-cu12`` pip
    wheels they live in ``site-packages/nvidia/*/bin`` — which CTranslate2 does
    not search, and pointing the DLL search path at them proved unreliable
    across launch environments. The robust fix: copy the DLLs into CTranslate2's
    own package folder, which its ``__init__`` always loads (exactly how its
    bundled cuDNN works). Idempotent – only copies what is missing.
    """
    if sys.platform != "win32":
        return
    ctdir = _ctranslate2_dir()
    if not ctdir or not os.path.isdir(ctdir):
        return
    wanted = ("cudart64_12.dll", "cublasLt64_12.dll", "cublas64_12.dll")
    if all(os.path.isfile(os.path.join(ctdir, n)) for n in wanted):
        return  # already set up – fast path on every start
    import shutil
    src_dirs = _find_nvidia_bin_dirs()
    for name in wanted:
        dst = os.path.join(ctdir, name)
        if os.path.isfile(dst):
            continue
        for d in src_dirs:
            src = os.path.join(d, name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass
                break


_ensure_cuda_libs()


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _pick_device(device: str) -> tuple[str, str]:
    if device and device != "auto":
        return device, ("float16" if device == "cuda" else "int8")
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _transcribe(model, wav_path: str, req: dict) -> tuple[str, list]:
    language = req.get("language") or None
    prompt = req.get("initial_prompt") or None
    hotwords = req.get("hotwords") or None
    live = bool(req.get("live"))
    # A temperature *list* enables faster-whisper's fallback re-decode, which
    # drops repetitive / low-confidence hallucinations (e.g. from background
    # noise on a far-field mic). hallucination_silence_threshold skips silent
    # gaps where Whisper otherwise invents text (needs word_timestamps=True).
    # The live polish uses the list; the batch path (live=False) uses a scalar
    # 0.0 to match the in-process batch behaviour (repetitions are cleaned up
    # afterwards by the post-processing step instead).
    temperature = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0] if live else 0.0
    segments, _info = model.transcribe(
        wav_path,
        language=language,
        initial_prompt=prompt,
        hotwords=hotwords,
        beam_size=5,
        temperature=temperature,
        condition_on_previous_text=False,
        vad_filter=True,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        hallucination_silence_threshold=2.0,
        word_timestamps=True,
    )
    parts, low = [], []
    for seg in segments:            # iterating drives the actual inference
        if live and getattr(seg, "no_speech_prob", 0.0) > 0.6 \
                and getattr(seg, "avg_logprob", 0.0) < -1.0:
            continue                # Whisper itself flags this as a hallucination
        parts.append(seg.text.strip())
        for w in (getattr(seg, "words", None) or []):
            if getattr(w, "probability", 1.0) < 0.55:
                token = (w.word or "").strip().strip(" .,;:!?…\"'„“”")
                if token:
                    low.append(token)
    return " ".join(parts).strip(), low


def main() -> None:
    model_name = sys.argv[1] if len(sys.argv) > 1 else "small"
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    device_arg = sys.argv[3] if len(sys.argv) > 3 else "auto"
    try:
        from faster_whisper import WhisperModel
        device, compute = _pick_device(device_arg)
        model = WhisperModel(model_name, device=device,
                             compute_type=compute, cpu_threads=threads)
    except Exception as exc:        # pragma: no cover - environment specific
        _emit({"ready": False, "error": str(exc)[:300]})
        return
    _emit({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("cmd") == "quit":
            break
        wav = req.get("wav")
        if not wav:
            _emit({"text": "", "low": []})
            continue
        try:
            text, low = _transcribe(model, wav, req)
            _emit({"text": text, "low": low})
        except Exception as exc:    # never let one bad clip kill the worker
            _emit({"text": "", "low": [], "error": str(exc)[:300]})


if __name__ == "__main__":
    main()
