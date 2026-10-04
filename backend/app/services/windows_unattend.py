"""Build temporary Windows Setup answer media without persisting plaintext secrets.

The answer file is delivered as ``Autounattend.xml`` at the root of a small
ISO 9660 image attached as a CD drive. Windows Setup searches removable
read-only media for that name at the start of the windowsPE pass under both
BIOS and EFI firmware, caches it in ``%WINDIR%\\Panther`` and applies the
later passes from the cache.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
import struct
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from app.services.windows_media import PRODUCT_KEY

UNATTEND_NS = "urn:schemas-microsoft-com:unattend"
WCM_NS = "http://schemas.microsoft.com/WMIConfig/2002/State"
ET.register_namespace("", UNATTEND_NS)
ET.register_namespace("wcm", WCM_NS)

ANSWER_FILE_NAME = "Autounattend.xml"


@dataclass(frozen=True)
class WindowsUnattendSpec:
    computer_name: str
    administrator_username: str
    administrator_password: str
    image_index: int = 1
    locale: str = "en-US"
    input_locale: str = "0409:00000409"
    timezone: str = "UTC"
    firmware: str = "EFI"
    # Only for media that asks for a key (retail / MAK); volume-license and
    # evaluation media install without one.
    product_key: str | None = None
    # Windows Server OOBE asks for the built-in Administrator's password unless
    # the answer file sets it. When the provisioning account is another local
    # account, callers pass a random value nobody keeps.
    builtin_administrator_password: str | None = None


def _component(settings: ET.Element, name: str) -> ET.Element:
    component = ET.SubElement(settings, f"{{{UNATTEND_NS}}}component")
    component.set("name", name)
    component.set("processorArchitecture", "amd64")
    component.set("publicKeyToken", "31bf3856ad364e35")
    component.set("language", "neutral")
    component.set("versionScope", "nonSxS")
    return component


def _text(parent: ET.Element, name: str, value: str) -> ET.Element:
    node = ET.SubElement(parent, f"{{{UNATTEND_NS}}}{name}")
    node.text = value
    return node


def _action_child(parent: ET.Element, name: str) -> ET.Element:
    node = ET.SubElement(parent, f"{{{UNATTEND_NS}}}{name}")
    node.set(f"{{{WCM_NS}}}action", "add")
    return node


def _add_disk_configuration(setup: ET.Element, firmware: str) -> int:
    """Wipe disk 0 (the only disk attached during Setup) and lay it out for the firmware."""
    disk_configuration = ET.SubElement(setup, f"{{{UNATTEND_NS}}}DiskConfiguration")
    disk = _action_child(disk_configuration, "Disk")
    _text(disk, "DiskID", "0")
    _text(disk, "WillWipeDisk", "true")
    create = ET.SubElement(disk, f"{{{UNATTEND_NS}}}CreatePartitions")
    modify = ET.SubElement(disk, f"{{{UNATTEND_NS}}}ModifyPartitions")

    if firmware.upper() == "EFI":
        # GPT: EFI system partition, Microsoft reserved, Windows.
        layouts = (
            (1, "EFI", "100", False, "FAT32", "System"),
            (2, "MSR", "16", False, None, None),
            (3, "Primary", None, True, "NTFS", "Windows"),
        )
        windows_partition = 3
    else:
        # MBR: active system partition, Windows.
        layouts = (
            (1, "Primary", "550", False, "NTFS", "System"),
            (2, "Primary", None, True, "NTFS", "Windows"),
        )
        windows_partition = 2

    for order, type_name, size, extend, filesystem, label in layouts:
        partition = _action_child(create, "CreatePartition")
        _text(partition, "Order", str(order))
        _text(partition, "Type", type_name)
        if size:
            _text(partition, "Size", size)
        if extend:
            _text(partition, "Extend", "true")
        if filesystem:
            changed = _action_child(modify, "ModifyPartition")
            _text(changed, "Order", str(order))
            _text(changed, "PartitionID", str(order))
            _text(changed, "Format", filesystem)
            if label:
                _text(changed, "Label", label)
            if firmware.upper() != "EFI" and order == 1:
                _text(changed, "Active", "true")
    _text(disk_configuration, "WillShowUI", "OnError")
    return windows_partition


def _local_account_name(username: str) -> str:
    raw_username = username.strip()
    qualifier, separator, local_username = raw_username.rpartition("\\")
    if not separator:
        local_username = raw_username
    if (
        (separator and qualifier != ".")
        or not local_username
        or len(local_username) > 20
        or local_username.endswith(".")
        or re.search(r'["/\\\[\]:;|=,+*?<>@\x00-\x1f]', local_username)
    ):
        raise ValueError(
            "The Windows provisioning credential username must be a valid local account name."
        )
    return local_username


def _add_user_accounts(
    shell: ET.Element, local_username: str, password: str, builtin_password: str | None
) -> None:
    accounts = ET.SubElement(shell, f"{{{UNATTEND_NS}}}UserAccounts")
    is_builtin = local_username.casefold() == "administrator"
    if is_builtin or builtin_password:
        admin_password = ET.SubElement(accounts, f"{{{UNATTEND_NS}}}AdministratorPassword")
        _text(admin_password, "Value", password if is_builtin else builtin_password)
        _text(admin_password, "PlainText", "true")
    if is_builtin:
        return
    local_accounts = ET.SubElement(accounts, f"{{{UNATTEND_NS}}}LocalAccounts")
    account = ET.SubElement(local_accounts, f"{{{UNATTEND_NS}}}LocalAccount")
    account.set(f"{{{WCM_NS}}}action", "add")
    account_password = ET.SubElement(account, f"{{{UNATTEND_NS}}}Password")
    _text(account_password, "Value", password)
    _text(account_password, "PlainText", "true")
    _text(account, "DisplayName", local_username)
    _text(account, "Group", "Administrators")
    _text(account, "Name", local_username)


def _add_international_core(settings: ET.Element, component_name: str, spec: WindowsUnattendSpec) -> None:
    intl = _component(settings, component_name)
    if component_name.endswith("-WinPE"):
        setup_ui = ET.SubElement(intl, f"{{{UNATTEND_NS}}}SetupUILanguage")
        _text(setup_ui, "UILanguage", spec.locale)
    for key, value in (
        ("InputLocale", spec.input_locale),
        ("SystemLocale", spec.locale),
        ("UILanguage", spec.locale),
        ("UserLocale", spec.locale),
    ):
        _text(intl, key, value)


# Installs VMware Tools at first logon from the host's Tools ISO, which
# InfraOps attaches as a second CD drive. Only that ISO has setup64.exe at its
# root (Windows media has setup.exe only). The installer is retried for up to
# two hours in case the drive is still being enumerated.
# Compact so the encoded command stays within the length limit below.
_TOOLS_INSTALL_STATEMENTS = (
    "$d=(Get-Date).AddHours(2)",
    "do{$i=Get-PSDrive -PSProvider FileSystem|%{Join-Path $_.Root setup64.exe}|?{Test-Path $_}|select -f 1",
    "if($i){exit (Start-Process $i '/s /v\"/qn REBOOT=R\"' -Wait -PassThru).ExitCode}",
    "sleep 15}while((Get-Date)-lt $d)",
    "exit 1",
)
# Windows Setup limits a FirstLogonCommands command line to 1024 characters.
MAX_COMMAND_LINE = 1024


def tools_install_command() -> str:
    """First-logon command line; -EncodedCommand avoids any nested quoting."""
    script = "; ".join(_TOOLS_INSTALL_STATEMENTS)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    command = (
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}"
    )
    if len(command) > MAX_COMMAND_LINE:  # pragma: no cover - guarded by a unit test
        raise ValueError("The VMware Tools first-logon command exceeds Windows Setup's limit.")
    return command


def build_autounattend_xml(spec: WindowsUnattendSpec) -> bytes:
    """Return an amd64 answer file that installs Windows without showing a page.

    windowsPE: Setup language, disk 0 wiped and partitioned for the firmware,
    the image index, the EULA and (when configured) the product key.
    specialize: computer name and time zone. oobeSystem: locale, every OOBE
    page hidden, the provisioning administrator, one automatic logon that
    installs VMware Tools. Domain credentials are never part of the file.
    ElementTree performs XML escaping.
    """
    local_username = _local_account_name(spec.administrator_username)
    product_key = (spec.product_key or "").strip().upper() or None
    if product_key is not None and not PRODUCT_KEY.fullmatch(product_key):
        raise ValueError("The Windows product key must have the form XXXXX-XXXXX-XXXXX-XXXXX-XXXXX.")
    root = ET.Element(f"{{{UNATTEND_NS}}}unattend")

    windows_pe = ET.SubElement(root, f"{{{UNATTEND_NS}}}settings", {"pass": "windowsPE"})
    _add_international_core(windows_pe, "Microsoft-Windows-International-Core-WinPE", spec)
    setup = _component(windows_pe, "Microsoft-Windows-Setup")
    windows_partition = _add_disk_configuration(setup, spec.firmware)
    install = ET.SubElement(setup, f"{{{UNATTEND_NS}}}ImageInstall")
    os_image = ET.SubElement(install, f"{{{UNATTEND_NS}}}OSImage")
    install_from = ET.SubElement(os_image, f"{{{UNATTEND_NS}}}InstallFrom")
    metadata = ET.SubElement(install_from, f"{{{UNATTEND_NS}}}MetaData")
    metadata.set(f"{{{WCM_NS}}}action", "add")
    _text(metadata, "Key", "/IMAGE/INDEX")
    _text(metadata, "Value", str(spec.image_index))
    install_to = ET.SubElement(os_image, f"{{{UNATTEND_NS}}}InstallTo")
    _text(install_to, "DiskID", "0")
    _text(install_to, "PartitionID", str(windows_partition))
    _text(os_image, "WillShowUI", "OnError")
    user_data = ET.SubElement(setup, f"{{{UNATTEND_NS}}}UserData")
    _text(user_data, "AcceptEula", "true")
    if product_key is not None:
        key = ET.SubElement(user_data, f"{{{UNATTEND_NS}}}ProductKey")
        _text(key, "Key", product_key)
        _text(key, "WillShowUI", "OnError")

    specialize = ET.SubElement(root, f"{{{UNATTEND_NS}}}settings", {"pass": "specialize"})
    shell_specialize = _component(specialize, "Microsoft-Windows-Shell-Setup")
    _text(shell_specialize, "ComputerName", spec.computer_name)
    _text(shell_specialize, "TimeZone", spec.timezone)

    oobe = ET.SubElement(root, f"{{{UNATTEND_NS}}}settings", {"pass": "oobeSystem"})
    # Answers the region, app language and keyboard page.
    _add_international_core(oobe, "Microsoft-Windows-International-Core", spec)
    shell = _component(oobe, "Microsoft-Windows-Shell-Setup")

    # A single automatic logon runs the VMware Tools installer; the cleanup
    # stage removes the Winlogon values it leaves behind.
    autologon = ET.SubElement(shell, f"{{{UNATTEND_NS}}}AutoLogon")
    logon_password = ET.SubElement(autologon, f"{{{UNATTEND_NS}}}Password")
    _text(logon_password, "Value", spec.administrator_password)
    _text(logon_password, "PlainText", "true")
    _text(autologon, "Domain", ".")
    _text(autologon, "Enabled", "true")
    _text(autologon, "LogonCount", "1")
    _text(autologon, "Username", local_username)

    commands = ET.SubElement(shell, f"{{{UNATTEND_NS}}}FirstLogonCommands")
    command = ET.SubElement(commands, f"{{{UNATTEND_NS}}}SynchronousCommand")
    command.set(f"{{{WCM_NS}}}action", "add")
    _text(command, "CommandLine", tools_install_command())
    _text(command, "Description", "Install VMware Tools")
    _text(command, "Order", "1")

    oobe_settings = ET.SubElement(shell, f"{{{UNATTEND_NS}}}OOBE")
    for key, value in (
        ("HideEULAPage", "true"),
        ("HideLocalAccountScreen", "true"),
        ("HideOEMRegistrationScreen", "true"),
        ("HideOnlineAccountScreens", "true"),
        ("HideWirelessSetupInOOBE", "true"),
        ("ProtectYourPC", "3"),
    ):
        _text(oobe_settings, key, value)
    _text(shell, "TimeZone", spec.timezone)
    _add_user_accounts(
        shell, local_username, spec.administrator_password, spec.builtin_administrator_password
    )

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ── ISO 9660 + Joliet answer media ───────────────────────────────────────────

_SECTOR = 2048
_VOLUME_ID = "INFRAOPS_ANSWER"
# Fixed so the same answer file always yields the same image.
_RECORDED_AT = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _directory_date() -> bytes:
    moment = _RECORDED_AT
    return bytes((moment.year - 1900, moment.month, moment.day, moment.hour, moment.minute, moment.second, 0))


def _volume_date() -> bytes:
    return _RECORDED_AT.strftime("%Y%m%d%H%M%S00").encode("ascii") + b"\x00"


def _directory_record(identifier: bytes, extent: int, length: int, *, directory: bool) -> bytes:
    record = bytearray(33)
    record[2:10] = _both32(extent)
    record[10:18] = _both32(length)
    record[18:25] = _directory_date()
    record[25] = 0x02 if directory else 0x00
    record[28:32] = _both16(1)
    record[32] = len(identifier)
    record += identifier
    if len(record) % 2:
        record += b"\x00"
    record[0] = len(record)
    return bytes(record)


def _path_table(root_extent: int, *, big_endian: bool) -> bytes:
    location = struct.pack(">I" if big_endian else "<I", root_extent)
    parent = struct.pack(">H" if big_endian else "<H", 1)
    return bytes((1, 0)) + location + parent + b"\x00\x00"


def _volume_descriptor(
    *,
    joliet: bool,
    total_sectors: int,
    path_table_size: int,
    l_table: int,
    m_table: int,
    root_extent: int,
) -> bytes:
    descriptor = bytearray(_SECTOR)
    descriptor[0] = 2 if joliet else 1
    descriptor[1:6] = b"CD001"
    descriptor[6] = 1

    def identifier(text: str, size: int) -> bytes:
        if joliet:
            encoded = text.encode("utf-16-be")
            return (encoded + " ".encode("utf-16-be") * size)[:size]
        return text.encode("ascii").ljust(size, b" ")

    descriptor[8:40] = identifier("", 32)
    descriptor[40:72] = identifier(_VOLUME_ID, 32)
    descriptor[80:88] = _both32(total_sectors)
    if joliet:
        descriptor[88:91] = b"%/E"  # UCS-2 level 3
    descriptor[120:124] = _both16(1)
    descriptor[124:128] = _both16(1)
    descriptor[128:132] = _both16(_SECTOR)
    descriptor[132:140] = _both32(path_table_size)
    descriptor[140:144] = struct.pack("<I", l_table)
    descriptor[148:152] = struct.pack(">I", m_table)
    descriptor[156:190] = _directory_record(b"\x00", root_extent, _SECTOR, directory=True)
    for start, size in ((190, 128), (318, 128), (446, 128), (574, 128), (702, 37), (739, 37), (776, 37)):
        descriptor[start:start + size] = identifier("", size)
    descriptor[813:830] = _volume_date()
    descriptor[830:847] = _volume_date()
    descriptor[847:864] = b"0" * 16 + b"\x00"
    descriptor[864:881] = _volume_date()
    descriptor[881] = 1
    return bytes(descriptor)


def build_answer_iso(xml_bytes: bytes) -> bytes:
    """A single-file ISO 9660 image with a Joliet tree exposing ``Autounattend.xml``.

    The primary tree names the file ``AUTOUNATTEND.XML;1``; Windows reads the
    Joliet tree, which carries the exact long name Setup searches for.
    """
    data_sectors = max(1, -(-len(xml_bytes) // _SECTOR))
    pvd, svd, terminator = 16, 17, 18
    l_primary, m_primary, l_joliet, m_joliet = 19, 20, 21, 22
    root_primary, root_joliet = 23, 24
    data = 25
    total = data + data_sectors

    image = bytearray(total * _SECTOR)

    def put(sector: int, payload: bytes) -> None:
        image[sector * _SECTOR:sector * _SECTOR + len(payload)] = payload

    # The root record's padding byte is part of the record (ECMA-119 9.4).
    path_table_size = len(_path_table(0, big_endian=False))
    put(pvd, _volume_descriptor(joliet=False, total_sectors=total, path_table_size=path_table_size,
                                l_table=l_primary, m_table=m_primary, root_extent=root_primary))
    put(svd, _volume_descriptor(joliet=True, total_sectors=total, path_table_size=path_table_size,
                                l_table=l_joliet, m_table=m_joliet, root_extent=root_joliet))
    put(terminator, b"\xffCD001\x01")
    put(l_primary, _path_table(root_primary, big_endian=False))
    put(m_primary, _path_table(root_primary, big_endian=True))
    put(l_joliet, _path_table(root_joliet, big_endian=False))
    put(m_joliet, _path_table(root_joliet, big_endian=True))

    for root, name in (
        (root_primary, ANSWER_FILE_NAME.upper().encode("ascii") + b";1"),
        (root_joliet, ANSWER_FILE_NAME.encode("utf-16-be")),
    ):
        put(
            root,
            _directory_record(b"\x00", root, _SECTOR, directory=True)
            + _directory_record(b"\x01", root, _SECTOR, directory=True)
            + _directory_record(name, data, len(xml_bytes), directory=False),
        )
    put(data, xml_bytes)
    return bytes(image)
