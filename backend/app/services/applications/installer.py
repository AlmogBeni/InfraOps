"""Idempotent application installation through controlled guest operations.

Operators can only *select* catalog entries; installer paths, arguments and
detection rules are administrator-defined and additionally sanitised here.
Exit code 3010 from msiexec is treated as success-with-reboot-required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.core.metrics import app_install_failures_total
from app.models.applications import InstallerType
from app.services.applications.detection_rules import (
    DETECTION_ABSENT_EXIT_CODES,
    MSIEXEC_PATH,
    POWERSHELL_PATH,
    _CONTROL_CHARS,
    detection_programs,
)
from app.services.guest.base import GuestCredentials, GuestOperations
from app.services.vmware.base import VCenterTarget

log = get_logger(__name__)

_MSI_REBOOT_EXIT_CODE = 3010


@dataclass(frozen=True)
class ApplicationDefinition:
    id: uuid.UUID
    name: str
    installer_type: InstallerType
    installer_path: str
    install_arguments: str
    detection_method: str
    detection_config: dict
    timeout_seconds: int
    reboot_required: bool


@dataclass
class InstallOutcome:
    app_id: uuid.UUID
    name: str
    status: str  # INSTALLED | ALREADY_INSTALLED | FAILED | SKIPPED | REBOOT_REQUIRED
    output: str = ""
    reboot_required: bool = False


def _sanitize_arguments(arguments: str) -> str:
    if _CONTROL_CHARS.search(arguments):
        raise InfraOperationError(
            "An application definition contains invalid control characters.",
            reason="Installer arguments include control characters.",
            recommended_action="Ask an administrator to correct the application definition.",
            technical_detail=f"arguments={arguments!r}",
            retryable=False,
        )
    return arguments.strip()


def build_install_program(app: ApplicationDefinition) -> tuple[str, str]:
    arguments = _sanitize_arguments(app.install_arguments)
    quoted_path = f'"{app.installer_path}"'
    if app.installer_type == InstallerType.MSI:
        extra = f" {arguments}" if arguments else ""
        return MSIEXEC_PATH, f"/i {quoted_path}{extra} /qn /norestart"
    if app.installer_type == InstallerType.EXE:
        return app.installer_path, arguments
    if app.installer_type == InstallerType.POWERSHELL:
        return POWERSHELL_PATH, f"-NoProfile -ExecutionPolicy Bypass -File {quoted_path} {arguments}".rstrip()
    raise InfraOperationError(
        f"Unsupported installer type '{app.installer_type}'.",
        reason="Unknown installer type in the application definition.",
        recommended_action="Ask an administrator to review the application definition.",
        retryable=False,
    )


class ApplicationInstaller:
    name = "application-installer"

    def __init__(self, guest_ops: GuestOperations) -> None:
        self._guest = guest_ops

    async def detect(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        app: ApplicationDefinition,
    ) -> bool:
        from app.models.applications import DetectionMethod

        method = DetectionMethod(app.detection_method)
        probes = detection_programs(method, app.detection_config)
        last_output = ""
        for program, arguments in probes:
            result = await self._guest.run_program(
                target, vm_name, credentials, program, arguments, min(app.timeout_seconds, 300)
            )
            if result.exit_code == 0:
                return True
            if result.exit_code in DETECTION_ABSENT_EXIT_CODES:
                last_output = result.stdout[-500:]
                continue
            raise InfraOperationError(
                f"Detection for '{app.name}' could not run inside the guest.",
                reason=f"'{program}' exited with unexpected code {result.exit_code}.",
                recommended_action="Inspect the stage output and retry application installation.",
                technical_detail=(result.stdout + result.stderr)[-1500:],
                retryable=True,
            )
        log.info("Detection: '%s' not present on %s (%s)", app.name, vm_name, last_output.strip())
        return False

    async def install(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        app: ApplicationDefinition,
    ) -> InstallOutcome:
        try:
            if await self.detect(target, vm_name, credentials, app):
                return InstallOutcome(
                    app_id=app.id, name=app.name, status="ALREADY_INSTALLED",
                    output="Detection matched — installation skipped (idempotent).",
                )

            program, arguments = build_install_program(app)
            result = await self._guest.run_program(
                target, vm_name, credentials, program, arguments, float(app.timeout_seconds)
            )

            if result.exit_code == 0:
                return InstallOutcome(
                    app_id=app.id, name=app.name, status="INSTALLED",
                    output=result.stdout[-4000:], reboot_required=app.reboot_required,
                )
            if app.installer_type == InstallerType.MSI and result.exit_code == _MSI_REBOOT_EXIT_CODE:
                return InstallOutcome(
                    app_id=app.id, name=app.name, status="REBOOT_REQUIRED",
                    output=result.stdout[-4000:], reboot_required=True,
                )

            app_install_failures_total.inc(application=app.name)
            return InstallOutcome(
                app_id=app.id, name=app.name, status="FAILED",
                output=(f"exit={result.exit_code}\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}").strip(),
            )
        except InfraOperationError as exc:
            app_install_failures_total.inc(application=app.name)
            return InstallOutcome(
                app_id=app.id, name=app.name, status="FAILED", output=exc.summary(),
            )
