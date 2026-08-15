"""Provision and locate a dedicated Python runtime for local dictation.

Why this exists
---------------
The packaged ``WithEase.exe`` (PyInstaller) embeds a Python interpreter, but that
interpreter has **no pip** and cannot run a ``.py`` file as a subprocess.  Local
speech recognition needs both: it must *install* ``faster-whisper`` and it must
*run* ``whisper_worker.py`` in a separate process.  With the frozen interpreter
alone that is impossible – which is why local recognition used to be offered only
in the source-code build.

The fix is a small, self-contained Python environment that lives *next to* the
app under ``%APPDATA%/WithEase/localrt``.  On first use the app bootstraps it with
`uv <https://github.com/astral-sh/uv>`_ (a single ~15 MB binary that downloads a
standalone CPython and installs packages), puts ``faster-whisper`` – plus the
NVIDIA CUDA wheels when a GPU is present – into it, and then runs *both* the
installer and the Whisper worker through *that* interpreter.

In the source-code build nothing here is needed: the current interpreter already
has pip and runs the worker, so :func:`worker_python` returns ``sys.executable``
and :func:`runtime_ready` simply reflects whether ``faster-whisper`` is importable
here.  All the frozen-only machinery stays dormant.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

# ``uv`` publishes one zip per platform on its GitHub "latest" release.
_UV_URL = ("https://github.com/astral-sh/uv/releases/latest/download/"
           "uv-x86_64-pc-windows-msvc.zip")
_UV_EXE = "uv.exe"
# The managed CPython uv fetches for the local environment.
_PYTHON_VERSION = "3.11"

# Quiet subprocess windows on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ProgressCb = Callable[[str], None]


def is_frozen() -> bool:
    """True when running inside the packaged .exe (no pip, no script subprocess)."""
    return bool(getattr(sys, "frozen", False))


def withease_data_dir() -> str:
    """The ``%APPDATA%/WithEase`` directory that holds modules, profiles, etc.

    Derived from this file's location (``…/WithEase/modules/dictation``) so it
    stays correct even if the app is installed to a non-default APPDATA, with a
    plain ``%APPDATA%/WithEase`` fallback.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.dirname(os.path.dirname(here))  # …/WithEase
    if os.path.basename(candidate).lower() == "withease":
        return candidate
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "WithEase")
    return candidate


def runtime_dir() -> str:
    """Directory holding the dedicated local-recognition virtual environment."""
    return os.path.join(withease_data_dir(), "localrt")


def runtime_python() -> str | None:
    """Path to the local runtime's ``python.exe``, or ``None`` if not set up."""
    exe = os.path.join(runtime_dir(), "Scripts", "python.exe")
    return exe if os.path.isfile(exe) else None


def _runtime_site_packages() -> str:
    return os.path.join(runtime_dir(), "Lib", "site-packages")


def worker_python() -> str | None:
    """Interpreter that should run pip and the Whisper worker.

    Source build: the current interpreter (it has pip and runs scripts).
    Frozen build: the provisioned local runtime, or ``None`` when not yet set up.
    """
    if not is_frozen():
        return sys.executable
    return runtime_python()


def clean_child_env() -> dict:
    """Environment for child processes (the Whisper worker, uv) that must NOT
    inherit the PyInstaller bundle.

    A frozen app prepends its own directory – which contains its *own* bundled
    ``python3xx.dll`` and support DLLs – to ``PATH`` and can export
    ``PYTHONHOME`` / ``PYTHONPATH``.  If the dedicated runtime's ``python.exe``
    inherited those, the OS loader could pick up the app's bundled DLLs or the
    wrong standard library instead of the runtime's own, and the worker would
    fail to start.  Strip them so the child is fully self-contained.  In the
    source build nothing is stripped (the environment is already correct)."""
    env = dict(os.environ)
    if not is_frozen():
        return env
    for var in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
                "__PYVENV_LAUNCHER__", "PYTHONNOUSERSITE"):
        env.pop(var, None)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and env.get("PATH"):
        mein = os.path.normcase(os.path.abspath(meipass))
        keep = [p for p in env["PATH"].split(os.pathsep)
                if p and os.path.normcase(os.path.abspath(p)) != mein]
        env["PATH"] = os.pathsep.join(keep)
    return env


def runtime_ready() -> bool:
    """True when local recognition can actually run right now."""
    if not is_frozen():
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    return (runtime_python() is not None
            and os.path.isdir(os.path.join(_runtime_site_packages(),
                                           "faster_whisper")))


# ---------------------------------------------------------------------------
# Bootstrapping (frozen build only)
# ---------------------------------------------------------------------------

def _bin_dir() -> str:
    return os.path.join(withease_data_dir(), "bin")


def uv_path() -> str | None:
    """Locate a usable ``uv`` binary: one we downloaded, or one already on PATH."""
    local = os.path.join(_bin_dir(), _UV_EXE)
    if os.path.isfile(local):
        return local
    import shutil
    found = shutil.which("uv")
    return found


def ensure_uv(progress: ProgressCb | None = None) -> str:
    """Return a path to ``uv``, downloading it into ``…/WithEase/bin`` if needed."""
    existing = uv_path()
    if existing:
        return existing
    if progress:
        progress("uv")
    import io
    import urllib.request
    import zipfile
    os.makedirs(_bin_dir(), exist_ok=True)
    with urllib.request.urlopen(_UV_URL, timeout=120) as resp:  # nosec B310
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if os.path.basename(name).lower() == _UV_EXE:
                with zf.open(name) as src, \
                        open(os.path.join(_bin_dir(), _UV_EXE), "wb") as dst:
                    dst.write(src.read())
                break
        else:  # pragma: no cover - upstream layout change
            raise RuntimeError("uv.exe not found in the downloaded archive")
    path = os.path.join(_bin_dir(), _UV_EXE)
    if not os.path.isfile(path):
        raise RuntimeError("uv download failed")
    return path


def _run(cmd: list[str], *, timeout: int) -> None:
    """Run a bootstrap command, raising a trimmed error on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout, creationflags=_NO_WINDOW,
                            env=clean_child_env())
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(tail[-400:] or f"command failed: {cmd[0]}")


def provision(progress: ProgressCb | None = None, *, with_gpu: bool = False,
              timeout: int = 3600) -> None:
    """Create the local runtime and install faster-whisper (+ CUDA when asked).

    Idempotent: an existing environment is reused and ``uv pip install`` skips
    what is already satisfied.  Raises ``RuntimeError`` with a short message when
    a step fails, so the caller can show it to the user.
    """
    uv = ensure_uv(progress)
    rt = runtime_dir()

    if runtime_python() is None:
        if progress:
            progress("python")
        # ``--python-preference managed`` makes uv fetch its own standalone
        # CPython, so we never depend on a system Python being installed.
        _run([uv, "venv", rt, "--python", _PYTHON_VERSION,
              "--python-preference", "managed"], timeout=timeout)

    py = runtime_python()
    if py is None:  # pragma: no cover - venv creation somehow produced no python
        raise RuntimeError("could not create the local Python environment")

    if progress:
        progress("packages")
    pkgs = ["faster-whisper"]
    if with_gpu:
        # CTranslate2 bundles cuDNN but not cuBLAS / the CUDA runtime.
        pkgs += ["nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12"]
    _run([uv, "pip", "install", "--python", py, *pkgs], timeout=timeout)

    if with_gpu:
        # Copy the CUDA DLLs into CTranslate2's folder inside the new runtime, so
        # GPU inference works on the first worker start.  Importing the worker
        # runs its _ensure_cuda_libs() in the runtime interpreter (it only copies
        # files – it never imports ctranslate2/faster-whisper).
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "whisper_worker.py")
        try:
            _run([py, "-c",
                  "import runpy, os, sys; "
                  "sys.path.insert(0, os.path.dirname(r'%s')); "
                  "import whisper_worker" % worker],
                 timeout=300)
        except Exception:
            pass  # best effort; the worker also does this on every start
