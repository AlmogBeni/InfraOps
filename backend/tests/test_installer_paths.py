"""Installer path validation and approved-root matching."""

from __future__ import annotations

import pytest

from app.models.applications import InstallerType
from app.services.applications.paths import (
    path_within_roots,
    validate_installer_path,
    validate_installer_root,
)

ROOTS = [r"\\srv\apps"]


@pytest.mark.parametrize(
    "path",
    [r"\\srv\apps\7zip\7z.msi", r"\\SRV\Apps\tools\setup.exe", r"\\srv\apps\x\install.ps1"],
)
def test_paths_inside_root_are_approved(path: str) -> None:
    assert path_within_roots(path, ROOTS)


@pytest.mark.parametrize(
    "path",
    [
        r"\\srv\apps-evil\x.msi",  # prefix match without a segment boundary
        r"\\srv\apps\..\..\other\x.exe",  # traversal
        r"\\srv\apps",  # the root itself is not a file inside it
        r"\\srv\other\x.msi",
    ],
)
def test_paths_outside_root_are_rejected(path: str) -> None:
    assert not path_within_roots(path, ROOTS)


@pytest.mark.parametrize(
    "path",
    [
        r'\\srv\apps\x".msi',
        r"\\srv\apps\a&b|c.msi",
        r"\\srv\apps\..\x.msi",
        r"\\srv\apps\x*.msi",
        "//srv/apps/x.msi",
        r"relative\x.msi",
        "\\\\srv\\apps\\x\n.msi",
    ],
)
def test_invalid_installer_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_installer_path(path)


def test_extension_must_match_installer_type() -> None:
    assert validate_installer_path(r"\\srv\apps\a.msi", InstallerType.MSI)
    with pytest.raises(ValueError):
        validate_installer_path(r"\\srv\apps\a.exe", InstallerType.MSI)


def test_roots_are_normalised() -> None:
    assert validate_installer_root("\\\\srv\\apps\\") == r"\\srv\apps"
    with pytest.raises(ValueError):
        validate_installer_root(r"\\srv\apps\..")
