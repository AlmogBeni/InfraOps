from __future__ import annotations

import io
from xml.etree import ElementTree as ET

import pycdlib
import pytest

from app.services.windows_unattend import (
    UNATTEND_NS,
    WindowsUnattendSpec,
    build_autounattend_xml,
    build_unattend_iso,
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
    assert root.tag == f"{{{UNATTEND_NS}}}unattend"


def test_generated_iso_exposes_autounattend_at_the_joliet_root() -> None:
    xml = build_autounattend_xml(
        WindowsUnattendSpec("SERVER-001", "Administrator", "password")
    )
    iso_bytes = build_unattend_iso(xml)
    image = pycdlib.PyCdlib()
    image.open_fp(io.BytesIO(iso_bytes))
    output = io.BytesIO()
    image.get_file_from_iso_fp(output, joliet_path="/Autounattend.xml")
    image.close()
    assert output.getvalue() == xml


@pytest.mark.parametrize("username", ["DOMAIN\\user", "bad@name", "x" * 21])
def test_unattend_rejects_non_local_or_invalid_account_names(username: str) -> None:
    with pytest.raises(ValueError, match="valid local account"):
        build_autounattend_xml(WindowsUnattendSpec("SERVER-001", username, "password"))
