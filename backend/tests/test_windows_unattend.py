"""Windows Setup answer file and answer media.

The golden files pin every setting that keeps Setup from showing a page;
regenerate them deliberately with ``UPDATE_GOLDEN=1 pytest tests/test_windows_unattend.py``.
"""

from __future__ import annotations

import base64
import os
import pathlib
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest

from app.schemas.provisioning import FirmwareType
from app.services.guest.base import GuestCredentials
from app.services.vmware.base import TemporaryMediaRef, VmRef
from app.services.windows_unattend import (
    UNATTEND_NS,
    WindowsUnattendSpec,
    build_answer_iso,
    build_autounattend_xml,
    tools_install_command,
)
from app.workers.stages import stage_prepare_unattended_install
from tests.conftest import make_request

GOLDEN = pathlib.Path(__file__).parent / "golden"
NS = {"u": UNATTEND_NS}
SECTOR = 2048

EFI_SPEC = WindowsUnattendSpec(
    computer_name="SERVER-EFI-01",
    administrator_username="Administrator",
    administrator_password="Golden-Admin-Passw0rd!",
    image_index=4,
    locale="en-US",
    input_locale="0409:00000409",
    timezone="Israel Standard Time",
    firmware="EFI",
    product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
)
BIOS_SPEC = WindowsUnattendSpec(
    computer_name="SERVER-BIOS-01",
    administrator_username=r".\infraops-admin",
    administrator_password="Golden-Admin-Passw0rd!",
    image_index=2,
    locale="de-DE",
    input_locale="0407:00000407",
    timezone="W. Europe Standard Time",
    firmware="BIOS",
    builtin_administrator_password="Golden-Unused-Builtin-Passw0rd!",
)


def _pretty(xml: bytes) -> str:
    root = ET.fromstring(xml)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


@pytest.mark.parametrize(
    ("spec", "golden"),
    [(EFI_SPEC, "autounattend_efi.xml"), (BIOS_SPEC, "autounattend_bios.xml")],
)
def test_answer_file_matches_golden(spec: WindowsUnattendSpec, golden: str) -> None:
    rendered = _pretty(build_autounattend_xml(spec))
    path = GOLDEN / golden
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(rendered, encoding="utf-8", newline="\n")
    assert rendered == path.read_text(encoding="utf-8")


def _settings(root: ET.Element, pass_name: str) -> ET.Element:
    return root.find(f"u:settings[@pass='{pass_name}']", NS)


def _component(settings: ET.Element, name: str) -> ET.Element:
    return settings.find(f"u:component[@name='{name}']", NS)


def test_efi_answer_file_installs_without_any_page() -> None:
    root = ET.fromstring(build_autounattend_xml(EFI_SPEC))
    windows_pe = _settings(root, "windowsPE")
    winpe_intl = _component(windows_pe, "Microsoft-Windows-International-Core-WinPE")
    assert winpe_intl.findtext("u:SetupUILanguage/u:UILanguage", namespaces=NS) == "en-US"
    assert winpe_intl.findtext("u:InputLocale", namespaces=NS) == "0409:00000409"

    setup = _component(windows_pe, "Microsoft-Windows-Setup")
    assert setup.findtext("u:UserData/u:AcceptEula", namespaces=NS) == "true"
    assert setup.findtext("u:UserData/u:ProductKey/u:Key", namespaces=NS) == "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
    image = setup.find("u:ImageInstall/u:OSImage", NS)
    assert image.findtext("u:InstallFrom/u:MetaData/u:Key", namespaces=NS) == "/IMAGE/INDEX"
    assert image.findtext("u:InstallFrom/u:MetaData/u:Value", namespaces=NS) == "4"
    assert image.findtext("u:InstallTo/u:DiskID", namespaces=NS) == "0"
    assert image.findtext("u:InstallTo/u:PartitionID", namespaces=NS) == "3"

    disk = setup.find("u:DiskConfiguration/u:Disk", NS)
    assert disk.findtext("u:DiskID", namespaces=NS) == "0"
    assert disk.findtext("u:WillWipeDisk", namespaces=NS) == "true"
    types = [node.text for node in disk.findall("u:CreatePartitions/u:CreatePartition/u:Type", NS)]
    assert types == ["EFI", "MSR", "Primary"]

    specialize = _component(_settings(root, "specialize"), "Microsoft-Windows-Shell-Setup")
    assert specialize.findtext("u:ComputerName", namespaces=NS) == "SERVER-EFI-01"

    oobe = _settings(root, "oobeSystem")
    assert _component(oobe, "Microsoft-Windows-International-Core").findtext("u:UserLocale", namespaces=NS) == "en-US"
    shell = _component(oobe, "Microsoft-Windows-Shell-Setup")
    assert shell.findtext("u:OOBE/u:HideEULAPage", namespaces=NS) == "true"
    assert shell.findtext("u:OOBE/u:HideOnlineAccountScreens", namespaces=NS) == "true"
    assert shell.findtext("u:TimeZone", namespaces=NS) == "Israel Standard Time"
    assert (
        shell.findtext("u:UserAccounts/u:AdministratorPassword/u:Value", namespaces=NS)
        == "Golden-Admin-Passw0rd!"
    )
    assert shell.findtext("u:AutoLogon/u:LogonCount", namespaces=NS) == "1"


def test_bios_answer_file_uses_an_active_mbr_layout_and_a_local_administrator() -> None:
    root = ET.fromstring(build_autounattend_xml(BIOS_SPEC))
    setup = _component(_settings(root, "windowsPE"), "Microsoft-Windows-Setup")
    disk = setup.find("u:DiskConfiguration/u:Disk", NS)
    assert disk.findtext("u:WillWipeDisk", namespaces=NS) == "true"
    assert [node.text for node in disk.findall("u:CreatePartitions/u:CreatePartition/u:Type", NS)] == [
        "Primary",
        "Primary",
    ]
    assert disk.findtext("u:ModifyPartitions/u:ModifyPartition/u:Active", namespaces=NS) == "true"
    assert setup.findtext("u:ImageInstall/u:OSImage/u:InstallTo/u:PartitionID", namespaces=NS) == "2"
    assert setup.find("u:UserData/u:ProductKey", NS) is None  # none configured

    shell = _component(_settings(root, "oobeSystem"), "Microsoft-Windows-Shell-Setup")
    account = shell.find("u:UserAccounts/u:LocalAccounts/u:LocalAccount", NS)
    assert account.findtext("u:Name", namespaces=NS) == "infraops-admin"
    assert account.findtext("u:Group", namespaces=NS) == "Administrators"
    assert shell.findtext("u:AutoLogon/u:Username", namespaces=NS) == "infraops-admin"
    # Server OOBE would otherwise stop to ask for the built-in Administrator's password.
    assert (
        shell.findtext("u:UserAccounts/u:AdministratorPassword/u:Value", namespaces=NS)
        == "Golden-Unused-Builtin-Passw0rd!"
    )


def test_answer_file_escapes_secrets() -> None:
    password = 'A&<"strong>password'
    xml = build_autounattend_xml(WindowsUnattendSpec("SERVER-042", "Administrator", password))
    assert b'A&amp;&lt;"strong&gt;password' in xml
    assert password in [node.text for node in ET.fromstring(xml).iter()]


def test_first_logon_installs_tools_from_the_tools_iso_within_setup_limits() -> None:
    command = tools_install_command()
    script = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")

    assert len(command) <= 1024  # Windows Setup's FirstLogonCommands limit
    assert command.startswith("powershell.exe ")
    # Only the Tools ISO has setup64.exe at its root; Windows media has setup.exe.
    assert "setup64.exe" in script
    assert "setup.exe" not in script.replace("setup64.exe", "")
    # InstallShield passes everything after /v to msiexec as one argument.
    assert "'/s /v\"/qn REBOOT=R\"'" in script


@pytest.mark.parametrize("key", ["AAAAA-BBBBB", "aaaaa-bbbbb-ccccc-ddddd-eeee!", "not a key"])
def test_invalid_product_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="product key"):
        build_autounattend_xml(WindowsUnattendSpec("SERVER-001", "Administrator", "password", product_key=key))


@pytest.mark.parametrize("username", ["DOMAIN\\user", "bad@name", "x" * 21])
def test_unattend_rejects_non_local_or_invalid_account_names(username: str) -> None:
    with pytest.raises(ValueError, match="valid local account"):
        build_autounattend_xml(WindowsUnattendSpec("SERVER-001", username, "password"))


def _joliet_files(image: bytes) -> dict[str, bytes]:
    """Read the root directory of the Joliet tree, as Windows does."""
    descriptor = image[17 * SECTOR:18 * SECTOR]
    assert descriptor[0] == 2 and descriptor[1:6] == b"CD001" and descriptor[88:91] == b"%/E"
    root_extent = struct.unpack_from("<I", descriptor, 156 + 2)[0]
    directory = image[root_extent * SECTOR:(root_extent + 1) * SECTOR]
    files: dict[str, bytes] = {}
    offset = 0
    while offset < len(directory) and directory[offset]:
        length = directory[offset]
        extent = struct.unpack_from("<I", directory, offset + 2)[0]
        size = struct.unpack_from("<I", directory, offset + 10)[0]
        name_length = directory[offset + 32]
        name = directory[offset + 33:offset + 33 + name_length]
        if not directory[offset + 25] & 0x02:
            files[name.decode("utf-16-be")] = image[extent * SECTOR:extent * SECTOR + size]
        offset += length
    return files


def test_answer_iso_exposes_autounattend_at_the_root() -> None:
    xml = build_autounattend_xml(EFI_SPEC)
    image = build_answer_iso(xml)

    assert len(image) % SECTOR == 0
    primary = image[16 * SECTOR:17 * SECTOR]
    assert primary[0] == 1 and primary[1:6] == b"CD001"
    assert primary[40:72].decode("ascii").strip() == "INFRAOPS_ANSWER"
    assert image[18 * SECTOR:18 * SECTOR + 6] == b"\xffCD001"
    assert _joliet_files(image) == {"Autounattend.xml": xml}
    assert build_answer_iso(xml) == image  # deterministic


def _prepare_context(*, product_key_ref: str | None, firmware: FirmwareType):
    request = make_request()
    request.hardware.firmware = firmware
    request.hardware.secure_boot = firmware == FirmwareType.EFI
    request.guest.product_key_secret_ref = product_key_ref
    request.guest.domain_join = SimpleNamespace(
        domain="corp.example.com", ou=None, credential_secret_ref="domain-join"
    )
    secrets = {
        "windows-server-2025-key/password": "aaaaa-bbbbb-ccccc-ddddd-eeeee\n",
        "domain-join/username": "CORP\\joiner",
        "domain-join/password": "Domain-Join-Secret!",
    }
    attach = AsyncMock(return_value=TemporaryMediaRef(datastore_path="[ds] infraops-unattend/x.iso"))
    return SimpleNamespace(
        request=request,
        vm_ref=VmRef(id="vm-42", name=request.vm.name),
        steps_by_key={},
        job_id="job-42",
        target=object(),
        secrets=SimpleNamespace(get_secret=AsyncMock(side_effect=lambda ref: secrets[ref])),
        resolve_guest_credentials=AsyncMock(
            return_value=GuestCredentials(username="Administrator", password="Store-Admin-Passw0rd!")
        ),
        vmware=SimpleNamespace(attach_answer_media=attach),
    )


@pytest.mark.parametrize("firmware", [FirmwareType.EFI, FirmwareType.BIOS])
async def test_prepared_media_uses_the_credential_store_and_never_domain_credentials(firmware) -> None:
    ctx = _prepare_context(product_key_ref="windows-server-2025-key", firmware=firmware)

    outcome = await stage_prepare_unattended_install(ctx)

    kwargs = ctx.vmware.attach_answer_media.await_args.kwargs
    assert kwargs["file_name"] == "infraops-job-42.iso"
    xml = _joliet_files(kwargs["content"])["Autounattend.xml"]
    root = ET.fromstring(xml)
    shell = _component(_settings(root, "oobeSystem"), "Microsoft-Windows-Shell-Setup")
    assert (
        shell.findtext("u:UserAccounts/u:AdministratorPassword/u:Value", namespaces=NS)
        == "Store-Admin-Passw0rd!"
    )
    setup = _component(_settings(root, "windowsPE"), "Microsoft-Windows-Setup")
    assert setup.findtext("u:UserData/u:ProductKey/u:Key", namespaces=NS) == "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
    partitions = setup.findall("u:DiskConfiguration/u:Disk/u:CreatePartitions/u:CreatePartition/u:Type", NS)
    partition_types = [node.text for node in partitions]
    assert partition_types[0] == ("EFI" if firmware == FirmwareType.EFI else "Primary")
    # join_domain stays a guest-operations stage: nothing about the domain is in the media.
    for forbidden in (b"Domain-Join-Secret!", b"joiner", b"corp.example.com", b"UnattendedJoin"):
        assert forbidden not in xml
    assert outcome.artifacts == {"datastore_path": "[ds] infraops-unattend/x.iso"}
    assert "product key" in outcome.output


async def test_prepared_media_has_no_product_key_unless_one_is_selected() -> None:
    ctx = _prepare_context(product_key_ref=None, firmware=FirmwareType.EFI)

    await stage_prepare_unattended_install(ctx)

    xml = _joliet_files(ctx.vmware.attach_answer_media.await_args.kwargs["content"])["Autounattend.xml"]
    assert b"<ProductKey>" not in xml
    ctx.secrets.get_secret.assert_not_awaited()
