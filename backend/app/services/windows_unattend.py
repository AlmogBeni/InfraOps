"""Build temporary Windows Setup answer media without persisting plaintext secrets."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from xml.etree import ElementTree as ET

UNATTEND_NS = "urn:schemas-microsoft-com:unattend"
WCM_NS = "http://schemas.microsoft.com/WMIConfig/2002/State"
ET.register_namespace("", UNATTEND_NS)
ET.register_namespace("wcm", WCM_NS)


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
    disk_configuration = ET.SubElement(setup, f"{{{UNATTEND_NS}}}DiskConfiguration")
    disk = _action_child(disk_configuration, "Disk")
    _text(disk, "DiskID", "0")
    _text(disk, "WillWipeDisk", "true")
    create = ET.SubElement(disk, f"{{{UNATTEND_NS}}}CreatePartitions")
    modify = ET.SubElement(disk, f"{{{UNATTEND_NS}}}ModifyPartitions")

    if firmware.upper() == "EFI":
        layouts = (
            (1, "EFI", "100", False, "FAT32", "System"),
            (2, "MSR", "16", False, None, None),
            (3, "Primary", None, True, "NTFS", "Windows"),
        )
        windows_partition = 3
    else:
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


def build_autounattend_xml(spec: WindowsUnattendSpec) -> bytes:
    """Return an amd64 Windows answer file. ElementTree performs XML escaping."""
    raw_username = spec.administrator_username.strip()
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
    root = ET.Element(f"{{{UNATTEND_NS}}}unattend")

    windows_pe = ET.SubElement(root, f"{{{UNATTEND_NS}}}settings", {"pass": "windowsPE"})
    intl = _component(windows_pe, "Microsoft-Windows-International-Core-WinPE")
    setup_ui = ET.SubElement(intl, f"{{{UNATTEND_NS}}}SetupUILanguage")
    _text(setup_ui, "UILanguage", spec.locale)
    for key, value in (
        ("InputLocale", spec.input_locale),
        ("SystemLocale", spec.locale),
        ("UILanguage", spec.locale),
        ("UserLocale", spec.locale),
    ):
        _text(intl, key, value)

    setup = _component(windows_pe, "Microsoft-Windows-Setup")
    user_data = ET.SubElement(setup, f"{{{UNATTEND_NS}}}UserData")
    _text(user_data, "AcceptEula", "true")
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

    specialize = ET.SubElement(root, f"{{{UNATTEND_NS}}}settings", {"pass": "specialize"})
    shell_specialize = _component(specialize, "Microsoft-Windows-Shell-Setup")
    _text(shell_specialize, "ComputerName", spec.computer_name)
    _text(shell_specialize, "TimeZone", spec.timezone)

    oobe = ET.SubElement(root, f"{{{UNATTEND_NS}}}settings", {"pass": "oobeSystem"})
    shell = _component(oobe, "Microsoft-Windows-Shell-Setup")
    _text(shell, "TimeZone", spec.timezone)
    oobe_settings = ET.SubElement(shell, f"{{{UNATTEND_NS}}}OOBE")
    _text(oobe_settings, "HideEULAPage", "true")
    _text(oobe_settings, "HideLocalAccountScreen", "true")
    _text(oobe_settings, "HideOnlineAccountScreens", "true")
    _text(oobe_settings, "HideWirelessSetupInOOBE", "true")
    _text(oobe_settings, "ProtectYourPC", "3")

    accounts = ET.SubElement(shell, f"{{{UNATTEND_NS}}}UserAccounts")
    if local_username.casefold() == "administrator":
        admin_password = ET.SubElement(accounts, f"{{{UNATTEND_NS}}}AdministratorPassword")
        _text(admin_password, "Value", spec.administrator_password)
        _text(admin_password, "PlainText", "true")
    else:
        local_accounts = ET.SubElement(accounts, f"{{{UNATTEND_NS}}}LocalAccounts")
        account = ET.SubElement(local_accounts, f"{{{UNATTEND_NS}}}LocalAccount")
        account.set(f"{{{WCM_NS}}}action", "add")
        _text(account, "Name", local_username)
        _text(account, "DisplayName", local_username)
        _text(account, "Group", "Administrators")
        password = ET.SubElement(account, f"{{{UNATTEND_NS}}}Password")
        _text(password, "Value", spec.administrator_password)
        _text(password, "PlainText", "true")

    autologon = ET.SubElement(shell, f"{{{UNATTEND_NS}}}AutoLogon")
    _text(autologon, "Enabled", "true")
    _text(autologon, "LogonCount", "1")
    _text(autologon, "Username", local_username)
    _text(autologon, "Domain", ".")
    logon_password = ET.SubElement(autologon, f"{{{UNATTEND_NS}}}Password")
    _text(logon_password, "Value", spec.administrator_password)
    _text(logon_password, "PlainText", "true")

    commands = ET.SubElement(shell, f"{{{UNATTEND_NS}}}FirstLogonCommands")
    command = ET.SubElement(commands, f"{{{UNATTEND_NS}}}SynchronousCommand")
    command.set(f"{{{WCM_NS}}}action", "add")
    _text(command, "Order", "1")
    _text(command, "Description", "Install VMware Tools")
    tools_script = (
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \""
        "$limit=(Get-Date).AddHours(2); do { "
        "$installer=Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=5' | "
        "ForEach-Object { $root=$_.DeviceID; if (-not (Test-Path "
        "(Join-Path $root 'sources\\boot.wim'))) { @((Join-Path $root 'setup.exe'),"
        "(Join-Path $root 'setup64.exe')) } } | "
        "Where-Object { Test-Path $_ } | Select-Object -First 1; "
        "if ($installer) { Start-Process $installer -ArgumentList '/s /v /qn REBOOT=R' -Wait; exit 0 }; "
        "Start-Sleep -Seconds 15 } while ((Get-Date) -lt $limit); exit 1\""
    )
    _text(command, "CommandLine", tools_script)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _fat12_set_entry(table: bytearray, cluster: int, value: int) -> None:
    offset = cluster + cluster // 2
    if cluster % 2:
        table[offset] = (table[offset] & 0x0F) | ((value << 4) & 0xF0)
        table[offset + 1] = (value >> 4) & 0xFF
    else:
        table[offset] = value & 0xFF
        table[offset + 1] = (table[offset + 1] & 0xF0) | ((value >> 8) & 0x0F)


def _short_name_checksum(name: bytes) -> int:
    checksum = 0
    for value in name:
        checksum = (((checksum & 1) << 7) | (checksum >> 1)) + value
        checksum &= 0xFF
    return checksum


def _long_name_entry(ordinal: int, units: list[int], checksum: int) -> bytes:
    entry = bytearray(32)
    entry[0] = ordinal
    entry[11] = 0x0F
    entry[12] = 0
    entry[13] = checksum
    struct.pack_into("<H", entry, 26, 0)
    for offset, value in zip(
        (1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30),
        units,
        strict=True,
    ):
        struct.pack_into("<H", entry, offset, value)
    return bytes(entry)


def build_unattend_floppy(xml_bytes: bytes) -> bytes:
    """Create a 1.44 MB FAT12 floppy image containing ``Autounattend.xml``.

    Windows Setup searches removable read/write media for this exact long file
    name. A floppy-backed answer file avoids presenting a second bootable CD-ROM
    beside the selected Windows installation ISO.
    """
    sector_size = 512
    sectors_per_fat = 9
    root_entries = 224
    root_sectors = (root_entries * 32 + sector_size - 1) // sector_size
    data_start_sector = 1 + 2 * sectors_per_fat + root_sectors
    available_clusters = 2880 - data_start_sector
    clusters_needed = max(1, (len(xml_bytes) + sector_size - 1) // sector_size)
    if clusters_needed > available_clusters:
        raise ValueError("Autounattend.xml is too large for the virtual floppy image.")

    image = bytearray(2880 * sector_size)
    image[0:3] = b"\xeb\x3c\x90"
    image[3:11] = b"INFRAOPS"
    struct.pack_into("<HBHBHHBHHHII", image, 11, 512, 1, 1, 2, root_entries, 2880,
                     0xF0, sectors_per_fat, 18, 2, 0, 0)
    image[36] = 0
    image[38] = 0x29
    struct.pack_into("<I", image, 39, 0x494F5053)
    image[43:54] = b"INFRAOPS   "
    image[54:62] = b"FAT12   "
    image[510:512] = b"\x55\xaa"

    fat = bytearray(sectors_per_fat * sector_size)
    fat[0:3] = b"\xf0\xff\xff"
    first_cluster = 2
    for index in range(clusters_needed):
        cluster = first_cluster + index
        next_cluster = 0xFFF if index == clusters_needed - 1 else cluster + 1
        _fat12_set_entry(fat, cluster, next_cluster)
    for start_sector in (1, 1 + sectors_per_fat):
        start = start_sector * sector_size
        image[start:start + len(fat)] = fat

    long_name = "Autounattend.xml"
    short_name = b"AUTOUN~1XML"
    name_units = list(struct.unpack(f"<{len(long_name)}H", long_name.encode("utf-16le")))
    name_units.append(0)
    while len(name_units) % 13:
        name_units.append(0xFFFF)
    chunks = [name_units[index:index + 13] for index in range(0, len(name_units), 13)]
    checksum = _short_name_checksum(short_name)
    directory_entries = []
    for index in reversed(range(len(chunks))):
        ordinal = index + 1
        if ordinal == len(chunks):
            ordinal |= 0x40
        directory_entries.append(_long_name_entry(ordinal, chunks[index], checksum))

    short_entry = bytearray(32)
    short_entry[0:11] = short_name
    short_entry[11] = 0x20
    struct.pack_into("<H", short_entry, 26, first_cluster)
    struct.pack_into("<I", short_entry, 28, len(xml_bytes))
    directory_entries.append(bytes(short_entry))
    root_start = (1 + 2 * sectors_per_fat) * sector_size
    directory = b"".join(directory_entries)
    image[root_start:root_start + len(directory)] = directory

    data_start = data_start_sector * sector_size
    image[data_start:data_start + len(xml_bytes)] = xml_bytes
    return bytes(image)
