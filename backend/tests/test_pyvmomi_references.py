"""Every pyVmomi name the backend uses must exist in the installed pyVmomi.

The vSphere adapters only run against a real vCenter, and the unit tests
replace most of pyVmomi with fakes, so a misspelt type such as
``vim.vm.guest.FileAttributes`` passed every test and failed the first real
guest file upload.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pyVmomi = pytest.importorskip("pyVmomi")

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_REFERENCE = re.compile(r"\b(vim|vmodl)((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")


def _references() -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        source = path.relative_to(APP.parent).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in _REFERENCE.finditer(line):
                found.append((f"{source}:{number}", match.group(1), match.group(2)[1:]))
    return found


def test_the_backend_uses_pyvmomi() -> None:
    # Guards the scan itself: an empty result would make the next test pass vacuously.
    assert len(_references()) > 50


def test_every_pyvmomi_name_used_by_the_backend_exists() -> None:
    roots = {"vim": pyVmomi.vim, "vmodl": pyVmomi.vmodl}
    missing = []
    for where, root, dotted in _references():
        current = roots[root]
        for part in dotted.split("."):
            if not hasattr(current, part):
                missing.append(f"{where}: {root}.{dotted}")
                break
            current = getattr(current, part)

    assert missing == []
