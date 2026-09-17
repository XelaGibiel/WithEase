"""Regenerate ``requirements-build.txt`` – the pinned input for the release build.

Why this exists
---------------
The workflow that builds ``WithEase.exe`` used to install its dependencies
without version numbers.  What ended up inside the file people download was
therefore whatever PyPI happened to serve in that minute: two builds of the
same tag could contain different Qt versions, and a single compromised package
release would have walked straight into a program that reads every keystroke.

``requirements-build.txt`` fixes both halves: every package is pinned to an
exact version *and* to the SHA-256 of the exact wheel.  The build installs it
with ``--require-hashes``, so pip refuses anything whose bytes do not match.

Upgrading a dependency
----------------------
1. Install the new version in the development environment and test with it –
   the point of the pins is that the released .exe contains what was tested.
2. Change the version in ``DIRECT`` below.
3. Run this script:  ``.venv/Scripts/python.exe tools/pin_build_deps.py``
4. Commit the regenerated ``requirements-build.txt`` together with the change.

The resolution is done *for the build machine*, not for this one: Windows
x86-64 and the CPython version the workflow uses.  Both are named below and
must stay in step with ``.github/workflows/build-release.yml``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# The versions the release is built from.  These are deliberately exact and
# deliberately NOT the same statement as requirements.txt: that file describes
# what WithEase *runs on* (a range, for people installing from source), this
# one describes what one particular .exe was *built from*.
DIRECT = [
    "PySide6==6.11.1",
    "pynput==1.8.2",
    "pyinstaller==6.21.0",
    "sounddevice==0.5.5",
    "requests==2.34.2",
]

# Must match the workflow: python-version and runs-on.
PYTHON_VERSION = "3.12"
PLATFORM = "win_amd64"

OUTPUT = Path(__file__).resolve().parents[1] / "requirements-build.txt"

HEADER = f"""\
# Pinned build inputs for the release .exe - GENERATED, do not edit by hand.
#
# Regenerate with:  python tools/pin_build_deps.py
# (that script also explains why this file exists and how to upgrade)
#
# Resolved for CPython {PYTHON_VERSION} on {PLATFORM}, matching
# .github/workflows/build-release.yml.  Installed there with --require-hashes,
# so pip rejects any wheel whose bytes differ from the hash below.
#
# Direct dependencies (everything else is pulled in by these):
{chr(10).join('#   ' + d for d in DIRECT)}
"""


def resolve() -> list[tuple[str, str, str]]:
    """(name, version, sha256) for the full dependency closure."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
             "--ignore-installed", "--only-binary=:all:",
             "--python-version", PYTHON_VERSION, "--platform", PLATFORM,
             "--target", str(Path(tmp) / "unused"),
             "--report", str(report), *DIRECT],
            check=True)
        data = json.loads(report.read_text(encoding="utf-8"))

    packages = []
    for item in data["install"]:
        name = item["metadata"]["name"]
        version = item["metadata"]["version"]
        hashes = item["download_info"]["archive_info"]["hashes"]
        digest = hashes.get("sha256")
        if not digest:
            raise SystemExit(
                f"{name} {version} has no SHA-256 - refusing to write a file "
                f"that pretends to be verified")
        packages.append((name, version, digest))
    return sorted(packages, key=lambda p: p[0].lower())


def main() -> None:
    packages = resolve()
    lines = [HEADER]
    for name, version, digest in packages:
        # pip wants "name==version \" then an indented --hash line.
        lines.append(f"{name}=={version} \\\n    --hash=sha256:{digest}")
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{OUTPUT.name}: {len(packages)} packages pinned")


if __name__ == "__main__":
    main()
