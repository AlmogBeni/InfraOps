"""Installer path validation and approved-root matching.

Administrators define installer locations; these rules still make sure a path
is a plain absolute UNC or drive path (no traversal, quotes, wildcards or
control characters) and that the approved-root check respects path-segment
boundaries (``\\\\srv\\apps`` never approves ``\\\\srv\\apps-evil``).
"""

from __future__ import annotations

import re

from app.models.applications import InstallerType

# One path segment: anything Windows allows in a file name, minus separators.
_SEGMENT = r"[^\\/:*?\"<>|\x00-\x1f]+"
UNC_PATH_PATTERN = re.compile(
    r"^\\\\[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?\\" + _SEGMENT + r"(?:\\" + _SEGMENT + r")*$"
)
DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:\\" + _SEGMENT + r"(?:\\" + _SEGMENT + r")*$")

_EXPECTED_EXTENSIONS: dict[InstallerType, tuple[str, ...]] = {
    InstallerType.MSI: (".msi",),
    InstallerType.EXE: (".exe",),
    InstallerType.POWERSHELL: (".ps1",),
}


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("\\") if segment]


def validate_windows_path(value: str, *, label: str = "Path") -> str:
    """Return a normalised absolute UNC or drive path, or raise ``ValueError``."""
    path = (value or "").strip()
    if not path:
        raise ValueError(f"{label} is required.")
    if "/" in path:
        raise ValueError(f"{label} must use backslashes (\\) as separators.")
    if not (UNC_PATH_PATTERN.fullmatch(path) or DRIVE_PATH_PATTERN.fullmatch(path)):
        raise ValueError(
            f"{label} must be an absolute UNC path (\\\\server\\share\\...) or drive path "
            "(C:\\...) without quotes, wildcards or control characters."
        )
    if any(segment.strip() in {".", ".."} for segment in _segments(path)):
        raise ValueError(f"{label} must not contain '.' or '..' segments.")
    if any(segment != segment.rstrip(" .") for segment in _segments(path)):
        raise ValueError(f"{label} segments must not end with a space or a dot.")
    return path


def validate_installer_path(value: str, installer_type: InstallerType | None = None) -> str:
    path = validate_windows_path(value, label="Installer path")
    if installer_type is not None:
        extensions = _EXPECTED_EXTENSIONS.get(installer_type, ())
        if extensions and not path.lower().endswith(extensions):
            raise ValueError(
                f"{installer_type.value} installers must point to a {' or '.join(extensions)} file."
            )
    return path


def validate_installer_root(value: str) -> str:
    return validate_windows_path((value or "").strip().rstrip("\\"), label="Approved installer root")


def _normalise(path: str) -> str:
    return path.strip().rstrip("\\").casefold()


def path_within_roots(path: str, roots: list[str]) -> bool:
    """True when ``path`` is strictly inside one of ``roots`` (segment boundary)."""
    try:
        candidate = validate_windows_path(path)
    except ValueError:
        return False
    normalised = _normalise(candidate)
    for root in roots:
        try:
            clean_root = _normalise(validate_installer_root(str(root)))
        except ValueError:
            continue
        if normalised.startswith(clean_root + "\\"):
            return True
    return False
