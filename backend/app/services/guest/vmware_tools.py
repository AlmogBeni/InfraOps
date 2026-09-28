"""Production guest operations through the VMware Tools guest API.

ENVIRONMENT DEPENDENT — requires VMware Tools running inside the target VM
and valid guest credentials resolved live from encrypted backend storage.

Output capture: ``StartProgramInGuest`` cannot stream stdout. Every program is
therefore started by an uploaded PowerShell runner script (``powershell.exe
-File``, never ``cmd.exe``) that launches it without a shell, captures its
output into a temporary file and exits with the program's exit code. The
runner, script and output files are downloaded/deleted afterwards. See
:mod:`app.services.guest.scripts`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import ssl
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.secrets.service import SecretsService
from app.services.guest.base import CommandResult, GuestCredentials, GuestOperations
from app.services.guest.scripts import (
    POWERSHELL_PATH,
    build_process_runner,
    encode_script,
    new_temp_path,
    new_token,
    powershell_file_arguments,
)
from app.services.vmware.base import VCenterTarget
from app.services.vmware.vsphere import HAS_PYVMOMI, VsphereVMwareService

log = get_logger(__name__)

_PROCESS_POLL_SECONDS = 2.0
_TRANSFER_TIMEOUT_SECONDS = 60.0
# The runner enforces the program timeout itself; the outer deadline only
# covers a runner that cannot report back.
_RUNNER_GRACE_SECONDS = 30.0

if HAS_PYVMOMI:
    from pyVmomi import vim


def _file_transfer_url(url: str, target: VCenterTarget) -> str:
    """Replace vSphere's wildcard transfer host with the verified vCenter host."""
    parts = urlsplit(url)
    if parts.hostname != "*":
        return url
    port = f":{parts.port}" if parts.port is not None else ""
    return urlunsplit((parts.scheme, f"{target.host}{port}", parts.path, parts.query, parts.fragment))


def _file_transfer_verify(target: VCenterTarget) -> bool | ssl.SSLContext:
    if not target.verify_ssl:
        return False
    return ssl.create_default_context(cafile=get_settings().vcenter_ca_file or None)


def _wrap(operation: str, exc: Exception, *, retryable: bool = True) -> InfraOperationError:
    technical = f"{type(exc).__name__}: {exc}"
    if HAS_PYVMOMI and isinstance(exc, vim.fault.InvalidGuestLogin):
        return InfraOperationError(
            "The guest operating system rejected the automation credentials.",
            reason="Invalid username or password for the local administrator account.",
            recommended_action=(
                "Verify the guest credential secret reference on the request "
                "and confirm the account is not locked out."
            ),
            technical_detail=technical,
            retryable=False,
        )
    if HAS_PYVMOMI and isinstance(exc, vim.fault.GuestOperationsUnavailable):
        return InfraOperationError(
            "VMware Tools guest operations are unavailable inside the VM.",
            reason="VMware Tools is not running or the guest API is disabled.",
            recommended_action="Confirm VMware Tools is running in vCenter, then retry this stage.",
            technical_detail=technical,
            retryable=True,
        )
    if HAS_PYVMOMI and isinstance(exc, vim.fault.GuestOperationsFault):
        return InfraOperationError(
            f"The guest rejected the '{operation}' operation.",
            reason=str(exc),
            recommended_action="Inspect the guest event log and retry the failed stage.",
            technical_detail=technical,
            retryable=True,
        )
    return InfraOperationError(
        f"Guest operation '{operation}' failed.",
        reason="An unexpected error occurred while communicating with the VM.",
        recommended_action="Verify VM and VMware Tools health, then retry.",
        technical_detail=technical,
        retryable=retryable,
    )


class VMwareToolsGuestOperations(GuestOperations):
    name = "vmware-tools"

    def __init__(self, secrets: SecretsService) -> None:
        if not HAS_PYVMOMI:  # pragma: no cover
            raise RuntimeError("pyvmomi is required for the VMware Tools guest adapter.")
        self._secrets = secrets
        self._vsphere = VsphereVMwareService(secrets)

    async def _find_vm(self, target: VCenterTarget, vm_name: str):
        def op(si):
            # Refuses ambiguous names instead of acting on an arbitrary match.
            return self._vsphere._find_vm_by_name(si.RetrieveContent(), vm_name)

        vm = await self._vsphere._with_session(target, op, operation="guest-find-vm")
        if vm is None:
            raise InfraOperationError(
                f"VM '{vm_name}' was not found while performing guest operations.",
                reason="VM missing from inventory.",
                recommended_action="Check the job timeline and vCenter inventory.",
                retryable=False,
            )
        return vm

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
        started = dt.datetime.now(dt.UTC)
        token = new_token()
        runner_path = new_temp_path(f"{token}-run", "ps1")
        output_path = new_temp_path(token, "log")
        try:
            runner = build_process_runner(
                program_path,
                arguments,
                output_path,
                timeout_seconds,
                working_directory=working_directory,
                utf8_output=program_path.lower() == POWERSHELL_PATH.lower(),
            )
        except ValueError as exc:
            raise InfraOperationError(
                "A guest command contained invalid characters.",
                reason=str(exc),
                recommended_action="Ask an administrator to correct the definition that produced it.",
                retryable=False,
            ) from exc

        await self.upload_file(target, vm_name, credentials, encode_script(runner), runner_path)
        try:
            exit_code = await self._start_and_wait(
                target,
                vm_name,
                credentials,
                POWERSHELL_PATH,
                powershell_file_arguments(runner_path),
                timeout_seconds + _RUNNER_GRACE_SECONDS,
                program_label=program_path,
            )
            stdout_text = ""
            try:
                blob = await self.download_file(target, vm_name, credentials, output_path)
                stdout_text = blob.decode("utf-8", errors="replace")
            except InfraOperationError:
                stdout_text = "[output capture unavailable]"
        finally:
            for path in (output_path, runner_path):
                try:
                    await self.delete_file(target, vm_name, credentials, path)
                except InfraOperationError:
                    pass

        duration = (dt.datetime.now(dt.UTC) - started).total_seconds()
        return CommandResult(exit_code=exit_code, stdout=stdout_text[-8000:], stderr="", duration_seconds=duration)

    async def _start_and_wait(
        self,
        target: VCenterTarget,
        vm_name: str,
        credentials: GuestCredentials,
        program_path: str,
        arguments: str,
        timeout_seconds: float,
        *,
        program_label: str,
    ) -> int:
        """Start one process directly through the Tools API and wait for its exit code."""
        vm = await self._find_vm(target, vm_name)
        auth = vim.vm.guest.NamePasswordAuthentication(
            username=credentials.username, password=credentials.password
        )
        process_manager = vm._stub.host.guestOperationsManager.processManager  # type: ignore[attr-defined]
        spec = vim.vm.guest.ProcessManager.ProgramSpec(
            programPath=program_path,
            arguments=arguments,
        )

        def start():
            return process_manager.StartProgramInGuest(vm, auth, spec)

        try:
            pid = await asyncio.to_thread(start)
        except Exception as exc:  # noqa: BLE001
            raise _wrap("start-program", exc) from exc

        def poll_processes():
            procs = process_manager.ListProcessesInGuest(vm, auth, [pid])
            return procs[0] if procs else None

        def terminate():
            process_manager.TerminateProcessInGuest(vm, auth, pid)

        deadline = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=timeout_seconds)
        proc_info = None
        try:
            while dt.datetime.now(dt.UTC) < deadline:
                proc_info = await asyncio.to_thread(poll_processes)
                if proc_info is None or proc_info.endTime is not None:
                    break
                await asyncio.sleep(_PROCESS_POLL_SECONDS)
        except asyncio.CancelledError:
            # Stage timeout or job cancellation: do not leave work running.
            try:
                await asyncio.shield(asyncio.to_thread(terminate))
            except Exception:  # noqa: BLE001 - best effort
                pass
            raise

        if proc_info is None:
            raise InfraOperationError(
                "The guest process could not be found after starting.",
                reason="Process disappeared without reporting an exit code.",
                recommended_action="Retry the stage; if it recurs, inspect the guest event log.",
                technical_detail=f"pid={pid}",
                retryable=True,
            )
        if proc_info.endTime is None:
            try:
                await asyncio.to_thread(terminate)
            except Exception:  # noqa: BLE001 - best effort
                pass
            raise InfraOperationError(
                f"The guest operation exceeded its {int(timeout_seconds)} second timeout.",
                reason=f"'{program_label}' did not finish in time.",
                recommended_action="Increase the configured timeout or investigate the guest.",
                technical_detail=f"pid={pid} program={program_label}",
                retryable=True,
            )
        return int(proc_info.exitCode or 0)

    async def upload_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials,
        content: bytes, guest_path: str,
    ) -> None:
        vm = await self._find_vm(target, vm_name)
        auth = vim.vm.guest.NamePasswordAuthentication(
            username=credentials.username, password=credentials.password
        )
        file_manager = vm._stub.host.guestOperationsManager.fileManager  # type: ignore[attr-defined]

        def prepare_url():
            return file_manager.InitiateFileTransferToGuest(
                vm, auth, guest_path, vim.vm.guest.FileAttributes(), len(content), True
            )

        try:
            url = _file_transfer_url(await asyncio.to_thread(prepare_url), target)
            async with httpx.AsyncClient(
                timeout=_TRANSFER_TIMEOUT_SECONDS, verify=_file_transfer_verify(target)
            ) as client:
                response = await client.put(url, content=content)
            if response.status_code >= 300:
                raise RuntimeError(f"File transfer HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001
            raise _wrap("upload-file", exc) from exc

    async def download_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> bytes:
        vm = await self._find_vm(target, vm_name)
        auth = vim.vm.guest.NamePasswordAuthentication(
            username=credentials.username, password=credentials.password
        )
        file_manager = vm._stub.host.guestOperationsManager.fileManager  # type: ignore[attr-defined]

        def prepare():
            return file_manager.InitiateFileTransferFromGuest(vm, auth, guest_path)

        try:
            info = await asyncio.to_thread(prepare)
            url = _file_transfer_url(info.url, target)
            async with httpx.AsyncClient(
                timeout=_TRANSFER_TIMEOUT_SECONDS, verify=_file_transfer_verify(target)
            ) as client:
                response = await client.get(url)
            if response.status_code >= 300:
                raise RuntimeError(f"File transfer HTTP {response.status_code}")
            return response.content
        except Exception as exc:  # noqa: BLE001
            raise _wrap("download-file", exc) from exc

    async def delete_file(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> None:
        vm = await self._find_vm(target, vm_name)
        auth = vim.vm.guest.NamePasswordAuthentication(
            username=credentials.username, password=credentials.password
        )
        file_manager = vm._stub.host.guestOperationsManager.fileManager  # type: ignore[attr-defined]

        def delete():
            file_manager.DeleteFileInGuest(vm, auth, guest_path)

        try:
            await asyncio.to_thread(delete)
        except Exception as exc:  # noqa: BLE001
            raise _wrap("delete-file", exc) from exc

    async def file_exists(
        self, target: VCenterTarget, vm_name: str, credentials: GuestCredentials, guest_path: str
    ) -> bool:
        vm = await self._find_vm(target, vm_name)
        auth = vim.vm.guest.NamePasswordAuthentication(
            username=credentials.username, password=credentials.password
        )
        file_manager = vm._stub.host.guestOperationsManager.fileManager  # type: ignore[attr-defined]

        directory, _, file_name = guest_path.rpartition("\\")
        pattern = "^" + re.escape(file_name) + "$"

        def listing():
            return file_manager.ListFilesInGuest(vm, auth, directory, 0, 1, pattern)

        try:
            result = await asyncio.to_thread(listing)
            return bool(getattr(result, "files", None))
        except Exception as exc:  # noqa: BLE001
            if HAS_PYVMOMI and isinstance(exc, vim.fault.FileNotFoundFault):
                return False
            raise _wrap("file-exists", exc) from exc
