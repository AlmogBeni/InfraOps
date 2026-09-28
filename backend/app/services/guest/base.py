"""Guest operations interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.errors import InfraOperationError
from app.services.guest.scripts import (
    POWERSHELL_PATH,
    GuestCommand,
    build_script_file,
    encode_script,
    new_temp_path,
    new_token,
    powershell_file_arguments,
)
from app.services.vmware.base import VCenterTarget


@dataclass(frozen=True)
class GuestCredentials:
    """Local/domain credentials used inside the guest OS.

    The repr is deliberately redacted so accidental logging can never leak
    passwords.
    """

    username: str
    password: str

    def __repr__(self) -> str:  # pragma: no cover
        return f"GuestCredentials(username={self.username!r}, password='[REDACTED]')"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class GuestOperations(ABC):
    """Controlled execution surface inside a Windows guest.

    Implementations must treat every argument as pre-validated structured
    data and must never log credentials or secret material. Nothing is ever
    interpreted by ``cmd.exe`` (see :mod:`app.services.guest.scripts`).
    """

    name: str = "abstract"

    async def run_powershell(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        script: str,
        timeout_seconds: float,
        *,
        secrets: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Upload ``script`` as a ``.ps1`` file and run it with ``-File``.

        ``secrets`` are uploaded as separate temporary files and exposed to the
        script as ``$InfraOpsSecrets['<name>']``; they never appear in the
        script text or on a command line.
        """
        token = new_token()
        script_path = new_temp_path(token, "ps1")
        secret_paths = {
            name: new_temp_path(f"{token}-{index}", "dat")
            for index, name in enumerate(secrets or {})
        }
        uploaded: list[str] = []
        try:
            for name, path in secret_paths.items():
                await self.upload_file(
                    target, vm_name, credentials, (secrets or {})[name].encode("utf-8"), path
                )
                uploaded.append(path)
            await self.upload_file(
                target,
                vm_name,
                credentials,
                encode_script(build_script_file(script, secret_paths)),
                script_path,
            )
            uploaded.append(script_path)
            return await self.run_program(
                target,
                vm_name,
                credentials,
                POWERSHELL_PATH,
                powershell_file_arguments(script_path),
                timeout_seconds,
            )
        finally:
            for path in uploaded:
                try:
                    await self.delete_file(target, vm_name, credentials, path)
                except InfraOperationError:
                    pass  # secret files delete themselves; the script may be gone

    async def run_command(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        command: GuestCommand,
        timeout_seconds: float,
    ) -> CommandResult:
        if command.is_powershell:
            return await self.run_powershell(
                target, vm_name, credentials, command.script or "", timeout_seconds
            )
        return await self.run_program(
            target, vm_name, credentials, command.program or "", command.arguments, timeout_seconds
        )

    @abstractmethod
    async def run_program(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        program_path: str,
        arguments: str,
        timeout_seconds: float,
        working_directory: str | None = None,
    ) -> CommandResult:
        """Start ``program`` directly (no shell) and capture its combined output."""

    @abstractmethod
    async def upload_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials,
        content: bytes, guest_path: str,
    ) -> None: ...

    @abstractmethod
    async def download_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> bytes: ...

    @abstractmethod
    async def delete_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> None: ...

    @abstractmethod
    async def file_exists(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> bool: ...
