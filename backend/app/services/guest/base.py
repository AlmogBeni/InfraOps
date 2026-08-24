"""Guest operations interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

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
    data and must never log credentials or secret material.
    """

    name: str = "abstract"

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
    ) -> CommandResult: ...

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
