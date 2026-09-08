from __future__ import annotations

import struct
from xml.etree import ElementTree as ET

import pytest

from app.services.windows_unattend import (
    UNATTEND_NS,
    WindowsUnattendSpec,
    build_autounattend_xml,
    build_unattend_floppy,
)


def test_answer_file_contains_requested_setup_values_and_escapes_secrets() -> None:
    password = 'A&<"strong>password'
    xml = build_autounattend_xml(
        WindowsUnattendSpec(
            computer_name="SERVER-042",
            administrator_username=r".\Administrator",
            administrator_password=password,
            image_index=4,
            locale="en-US",
            input_locale="0409:00000409",
            timezone="Israel Standard Time",
        )
    )
    root = ET.fromstring(xml)
    values = [node.text for node in root.iter()]
    assert "SERVER-042" in values
    assert "4" in values
    assert "Israel Standard Time" in values
    assert password in values
    assert b'A&amp;&lt;"strong&gt;password' in xml
    assert b"sources\\boot.wim" in xml
    assert root.tag == f"{{{UNATTEND_NS}}}unattend"


def test_generated_floppy_exposes_autounattend_at_the_fat_root() -> None:
    xml = build_autounattend_xml(
        WindowsUnattendSpec("SERVER-001", "Administrator", "password")
    )
    image = build_unattend_floppy(xml)

    assert len(image) == 1_474_560
    assert image[510:512] == b"\x55\xaa"
    root_start = 19 * 512
    entries = [image[root_start + offset:root_start + offset + 32] for offset in (0, 32, 64)]
    long_entries = sorted(entries[:2], key=lambda entry: entry[0] & 0x1F)
    units = []
    for entry in long_entries:
        for offset in (1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30):
            value = struct.unpack_from("<H", entry, offset)[0]
            if value == 0:
                break
            if value != 0xFFFF:
                units.append(value)
    name = b"".join(struct.pack("<H", value) for value in units).decode("utf-16le")
    assert name == "Autounattend.xml"

    short_entry = entries[2]
    assert short_entry[0:11] == b"AUTOUN~1XML"
    assert struct.unpack_from("<H", short_entry, 26)[0] == 2
    size = struct.unpack_from("<I", short_entry, 28)[0]
    assert image[33 * 512:33 * 512 + size] == xml


@pytest.mark.parametrize("username", ["DOMAIN\\user", "bad@name", "x" * 21])
def test_unattend_rejects_non_local_or_invalid_account_names(username: str) -> None:
    with pytest.raises(ValueError, match="valid local account"):
        build_autounattend_xml(WindowsUnattendSpec("SERVER-001", username, "password"))
