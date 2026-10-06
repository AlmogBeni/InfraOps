"""Recognize Windows installation media from its ISO 9660 volume label.

Unattended installation is supported for Windows Server only: client
editions add OOBE pages (account, privacy, network) and Windows 11 also needs
a vTPM. Microsoft labels every Windows Server installation ISO with an
``SSS_`` volume identifier (``SSS_X64FREE_EN-US_DV9``; 2012 R2 used
``IR3_SSS_...``), while client media use ``CCCOMA_``, ``CPBA_``, ``CENA_``,
``CCSA_``, ``CPRA_`` or ``ESD-ISO`` / ``ESD_ISO`` (Media Creation Tool).
"""

from __future__ import annotations

import enum
import re

# System area (16 sectors) + the primary volume descriptor that follows it.
_SECTOR = 2048
_PVD_OFFSET = 16 * _SECTOR
ISO_HEADER_BYTES = _PVD_OFFSET + _SECTOR

_SERVER_LABEL = re.compile(r"(?:^|_)SSS_")
_CLIENT_LABEL = re.compile(r"^(?:J_)?(?:CCCOMA|CCOMA|CPBA|CPRA|CENA|CCSA)_|^ESD[-_]ISO$")

PRODUCT_KEY = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}$")


class WindowsMediaKind(enum.StrEnum):
    SERVER = "server"
    CLIENT = "client"
    UNKNOWN = "unknown"


def iso_volume_label(header: bytes) -> str | None:
    """Volume identifier from the primary volume descriptor, if present."""
    descriptor = header[_PVD_OFFSET:_PVD_OFFSET + _SECTOR]
    if len(descriptor) < 72 or descriptor[0] != 1 or descriptor[1:6] != b"CD001":
        return None
    label = descriptor[40:72].decode("ascii", errors="replace").strip()
    return label or None


def classify_windows_media(label: str | None) -> WindowsMediaKind:
    normalized = (label or "").strip().upper()
    if _SERVER_LABEL.search(normalized):
        return WindowsMediaKind.SERVER
    if _CLIENT_LABEL.search(normalized):
        return WindowsMediaKind.CLIENT
    return WindowsMediaKind.UNKNOWN
