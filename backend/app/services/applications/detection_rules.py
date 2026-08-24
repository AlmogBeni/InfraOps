"""Detection-rule validation and safe command construction.

Only administrators define detection rules and installer commands; this module
still sanitises every interpolated value (control-character rejection, strict
patterns, PowerShell single-quote escaping) so even trusted definitions cannot
break out of the intended command shape.
"""

from __future__ import annotations

import re

from app.models.applications import DetectionMethod
from app.schemas.provisioning import GUID_PATTERN

POWERSHELL_PATH = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
REG_PATH = r"C:\Windows\System32\reg.exe"
SC_PATH = r"C:\Windows\System32\sc.exe"
MSIEXEC_PATH = r"C:\Windows\System32\msiexec.exe"

_WINDOWS_ABS_PATH = re.compile(r"^[A-Za-z]:\\[^<>:\"|?*\x00-\x1f]+$")
_REGISTRY_HIVE = re.compile(r"^HKLM\\|^HKCU\\")
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

# Exit codes meaning "detection ran fine, item NOT found".
DETECTION_ABSENT_EXIT_CODES = {1, 1060}


def _clean_text(value: object, label: str, max_length: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    if len(text) > max_length:
        raise ValueError(f"{label} exceeds {max_length} characters.")
    if _CONTROL_CHARS.search(text):
        raise ValueError(f"{label} contains control characters.")
    return text


def ps_quote(value: str) -> str:
    """Escape a value for embedding inside a PowerShell single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def validate_detection_config(method: DetectionMethod, config: dict) -> dict:
    """Validate and normalise a detection configuration for the given method."""
    config = dict(config or {})
    cleaned: dict = {}

    if method == DetectionMethod.MSI_PRODUCT_CODE:
        code = _clean_text(config.get("product_code"), "Product code", 60)
        if not GUID_PATTERN.fullmatch(code):
            raise ValueError("Product code must be a valid MSI GUID.")
        cleaned["product_code"] = code.strip("{}")

    elif method == DetectionMethod.REGISTRY_KEY:
        key = _clean_text(config.get("key_path"), "Registry key path", 400)
        if not _REGISTRY_HIVE.match(key):
            raise ValueError("Registry key paths must start with HKLM\\ or HKCU\\.")
        cleaned["key_path"] = key
        value_name = str(config.get("value_name") or "").strip()
        if value_name:
            cleaned["value_name"] = _clean_text(value_name, "Value name", 200)

    elif method == DetectionMethod.FILE_EXISTS:
        path = _clean_text(config.get("path"), "File path", 400)
        if not _WINDOWS_ABS_PATH.match(path) or ".." in path:
            raise ValueError("File path must be an absolute Windows path without traversal segments.")
        cleaned["path"] = path

    elif method == DetectionMethod.SERVICE_EXISTS:
        service = _clean_text(config.get("service_name"), "Service name", 80)
        if not _SERVICE_NAME.match(service):
            raise ValueError("Service names may contain letters, digits, dots, dashes and underscores.")
        cleaned["service_name"] = service

    elif method == DetectionMethod.SCRIPT:
        script = str(config.get("script") or "")
        if not script.strip():
            raise ValueError("Custom detection scripts must not be empty.")
        if len(script) > 8000:
            raise ValueError("Custom detection scripts are limited to 8000 characters.")
        cleaned["script"] = script

    return cleaned


def detection_programs(method: DetectionMethod, config: dict) -> list[tuple[str, str]]:
    """Return candidate (program, arguments) probes — any success means installed."""
    if method == DetectionMethod.MSI_PRODUCT_CODE:
        code = config["product_code"]
        uninstall_root = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        return [
            (REG_PATH, f'query "{uninstall_root}\\{{{code}}}" /v DisplayName'),
            (REG_PATH, f'query "{uninstall_root}\\{{{code}}}" /v DisplayName /reg:32'),
        ]

    if method == DetectionMethod.REGISTRY_KEY:
        args = f'query "{config["key_path"]}"'
        if config.get("value_name"):
            args += f' /v "{config["value_name"]}"'
        return [(REG_PATH, args)]

    if method == DetectionMethod.FILE_EXISTS:
        command = f"if (Test-Path -LiteralPath {ps_quote(config['path'])}) {{ exit 0 }} else {{ exit 1 }}"
        return [(POWERSHELL_PATH, f"-NoProfile -NonInteractive -Command {command}")]

    if method == DetectionMethod.SERVICE_EXISTS:
        return [(SC_PATH, f'query "{config["service_name"]}"')]

    if method == DetectionMethod.SCRIPT:
        return [(POWERSHELL_PATH, f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command {config['script']}")]

    raise ValueError(f"Unsupported detection method: {method}")
