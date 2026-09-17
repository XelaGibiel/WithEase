"""The two guards on the path that puts code onto a user's machine.

WithEase reads every keystroke, so "where did this code come from" is not a
routine question here.  Two answers are pinned down:

* The self-update downloads from GitHub or not at all.  The release metadata
  already arrives over TLS, so a foreign address means something upstream is
  wrong - and this is the one code path that replaces the program's own files.

* The release build installs exactly the wheels named in
  requirements-build.txt, by SHA-256.  Before that file existed, the build
  ran a bare "pip install PySide6 pynput pyinstaller ...", so what ended up
  inside the downloaded .exe was whatever PyPI served in that minute.
"""
import re
from pathlib import Path

import pytest

from withease.core import updater

NL = chr(10)
ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "requirements-build.txt"
WORKFLOW = ROOT / ".github" / "workflows" / "build-release.yml"


# -- the update download must come from GitHub -------------------------------

@pytest.mark.parametrize("url", [
    "https://api.github.com/repos/XelaGibiel/WithEase/zipball/v0.7.0",
    "https://codeload.github.com/XelaGibiel/WithEase/zip/refs/tags/v0.7.0",
    "https://raw.githubusercontent.com/XelaGibiel/WithEase/main/store/modules.json",
    "https://objects.githubusercontent.com/some/redirect/target",
])
def test_github_urls_are_trusted(url):
    assert updater.is_trusted_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com.evil.example/XelaGibiel/WithEase/zipball/v1",  # look-alike
    "https://evil.example/github.com/zipball/v1",                     # path only
    "http://github.com/XelaGibiel/WithEase/zipball/v1",               # not TLS
    "https://raw.githubusercontent.com.evil.example/x",               # suffix trick
    "ftp://github.com/x",
    "",
])
def test_everything_else_is_refused(url):
    assert not updater.is_trusted_url(url)


def test_the_update_refuses_a_foreign_download(monkeypatch):
    """A rewritten zipball_url must abort BEFORE anything is downloaded."""
    opened = []
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: opened.append(a))

    info = updater.ReleaseInfo(
        version="9.9.9", notes="", html_url="https://example.invalid",
        zipball_url="https://evil.example/withease.zip")

    with pytest.raises(RuntimeError, match="non-GitHub"):
        updater._update_via_zipball(info)
    assert opened == [], "nothing may be fetched from an untrusted address"


# -- the build inputs must be pinned by hash ---------------------------------

_PIN_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)\s*\\$")
_HASH_LINE = re.compile(r"^\s+--hash=sha256:[0-9a-f]{64}$")


def _pinned_packages() -> dict[str, str]:
    """{name: version} from requirements-build.txt, verifying the shape."""
    lines = [ln.rstrip() for ln in
             PIN_FILE.read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln and not ln.lstrip().startswith("#")]
    assert lines, "the pin file must not be empty"
    assert len(lines) % 2 == 0, "every package needs exactly one hash line"

    packages = {}
    for name_line, hash_line in zip(lines[0::2], lines[1::2]):
        match = _PIN_LINE.match(name_line)
        assert match, f"not an exact pin: {name_line!r}"
        assert _HASH_LINE.match(hash_line), f"not a sha256 hash: {hash_line!r}"
        packages[match.group(1).lower()] = match.group(2)
    return packages


def test_every_build_input_is_pinned_with_a_hash():
    packages = _pinned_packages()
    # The five direct dependencies plus their whole closure.
    for required in ("pyside6", "pynput", "pyinstaller", "sounddevice",
                     "requests"):
        assert required in packages, f"{required} is not pinned"
    assert len(packages) > 10, "a bare five packages cannot be the full closure"


def test_the_pinned_versions_match_what_the_app_declares():
    """requirements.txt says what WithEase RUNS on, the pin file says what one
    .exe was BUILT from - the second must satisfy the first."""
    from packaging.requirements import Requirement
    from packaging.version import Version

    packages = _pinned_packages()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        pinned = packages.get(req.name.lower())
        assert pinned, f"{req.name} is in requirements.txt but not pinned"
        assert req.specifier.contains(Version(pinned)), (
            f"the build pins {req.name} {pinned}, which does not satisfy "
            f"{line!r}")


def _workflow_commands() -> str:
    """The workflow without its comment lines - a comment that mentions a
    command is not the same as running it."""
    return NL.join(
        line for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#"))


def test_the_build_actually_uses_the_pin_file():
    """A pin file the workflow ignores would be pure decoration."""
    commands = _workflow_commands()
    assert "--require-hashes" in commands
    assert "requirements-build.txt" in commands
    assert "pip install --upgrade pip" not in commands, (
        "upgrading pip from PyPI is another unpinned download into the build")


def test_the_pin_file_is_resolved_for_the_python_the_build_uses():
    generator = (ROOT / "tools" / "pin_build_deps.py").read_text(
        encoding="utf-8")
    match = re.search(r'PYTHON_VERSION = "([0-9.]+)"', generator)
    assert match, "the generator must name the interpreter it resolves for"
    assert f'python-version: "{match.group(1)}"' in WORKFLOW.read_text(encoding="utf-8"), (
        "the wheels are resolved for one interpreter - the workflow must "
        "install exactly that one")
