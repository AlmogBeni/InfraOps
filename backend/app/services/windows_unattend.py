"""Build temporary Windows Setup answer media without persisting plaintext secrets."""

from __future__ import annotations

import io
import re
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
        "ForEach-Object { $root=$_.DeviceID; @((Join-Path $root 'setup.exe'),"
        "(Join-Path $root 'setup64.exe')) } | "
        "Where-Object { Test-Path $_ } | Select-Object -First 1; "
        "if ($installer) { Start-Process $installer -ArgumentList '/s /v /qn REBOOT=R' -Wait; exit 0 }; "
        "Start-Sleep -Seconds 15 } while ((Get-Date) -lt $limit); exit 1\""
    )
    _text(command, "CommandLine", tools_script)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_unattend_iso(xml_bytes: bytes) -> bytes:
    """Create a small Joliet ISO with Autounattend.xml at its root."""
    try:
        import pycdlib
    except ImportError as exc:  # pragma: no cover - dependency checked in deployment
        raise RuntimeError("pycdlib is required to build unattended Windows media") from exc

    image = pycdlib.PyCdlib()
    image.new(interchange_level=3, joliet=3, vol_ident="INFRAOPS")
    source = io.BytesIO(xml_bytes)
    image.add_fp(
        source,
        len(xml_bytes),
        iso_path="/AUTOUNAT.XML;1",
        joliet_path="/Autounattend.xml",
    )
    output = io.BytesIO()
    image.write_fp(output)
    image.close()
    return output.getvalue()
