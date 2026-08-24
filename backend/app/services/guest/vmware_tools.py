"""Production guest operations through the VMware Tools guest API.

ENVIRONMENT DEPENDENT — requires VMware Tools running inside the target VM
and valid guest credentials resolved from the secrets provider.

Output capture: ``StartProgramInGuest`` cannot stream stdout, so commands are
wrapped through ``cmd.exe /c "... > tempfile 2>&1"`` and the temporary output
file is downloaded and deleted afterwards. Only validated structured values
ever reach argument strings.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import httpx

from app.core.errors import InfraOperationError
from app.core.logging import get_logger
from app.secrets.service import SecretsService
from app.services.guest.base import CommandResult, GuestCredentials, GuestOperations
from app.services.vmware.base import VCenterTarget
from app.services.vmware.vsphere import HAS_PYVMOMI, VsphereVMwareService

log = get_logger(__name__)

_PROCESS_POLL_SECONDS = 2.0
_TRANSFER_TIMEOUT_SECONDS = 60.0

if HAS_PYVMOMI:
    from pyVmomi import vim


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
            content = si.RetrieveContent()
            view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
            try:
                for vm in view.view:
                    if vm.name.lower() == vm_name.lower():
                        return vm
            finally:
                view.Destroy()
            return None

        vm = await self._vsphere._with_session(target, op)
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
        started = dt.datetime.now(dt.timezone.utc)
        vm = await self._find_vm(target, vm_name)
        auth = vim.vm.guest.NamePasswordAuthentication(
            username=credentials.username, password=credentials.password
        )
        process_manager = vm._stub.host.guestOperationsManager.processManager  # type: ignore[attr-defined]

        output_path = rf"C:\Windows\Temp\infraops-{uuid.uuid4().hex}.log"
        wrapped_arguments = f'/c ""{program_path}" {arguments} > "{output_path}" 2>&1"'
        spec = vim.vm.guest.ProcessManager.ProgramSpec(
            programPath=r"C:\Windows\System32\cmd.exe",
            arguments=wrapped_arguments,
            workingDirectory=working_directory,
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

        deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout_seconds)
        proc_info = None
        while dt.datetime.now(dt.timezone.utc) < deadline:
            proc_info = await asyncio.to_thread(poll_processes)
            if proc_info is None or proc_info.endTime is not None:
                break
            await asyncio.sleep(_PROCESS_POLL_SECONDS)

        if proc_info is None:
            raise InfraOperationError(
                "The guest process could not be found after starting.",
                reason="Process disappeared without reporting an exit code.",
                recommended_action="Retry the stage; if it recurs, inspect the guest event log.",
                technical_detail=f"pid={pid}",
                retryable=True,
            )
        if proc_info.endTime is None:
            raise InfraOperationError(
                f"The guest operation exceeded its {int(timeout_seconds)} second timeout.",
                reason=f"'{program_path}' did not finish in time.",
                recommended_action="Increase the configured timeout or investigate the guest.",
                technical_detail=f"pid={pid} program={program_path}",
                retryable=True,
            )

        exit_code = int(proc_info.exitCode or 0)
        stdout_text = ""
        try:
            blob = await self.download_file(target, vm_name, credentials, output_path)
            stdout_text = blob.decode("utf-8", errors="replace")
        except InfraOperationError:
            stdout_text = "[output capture unavailable]"
        finally:
            try:
                await self.delete_file(target, vm_name, credentials, output_path)
            except InfraOperationError:
                pass

        duration = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
        return CommandResult(exit_code=exit_code, stdout=stdout_text[-8000:], stderr="", duration_seconds=duration)

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
            url = await asyncio.to_thread(prepare_url)
            async with httpx.AsyncClient(timeout=_TRANSFER_TIMEOUT_SECONDS, verify=False) as client:
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
            async with httpx.AsyncClient(timeout=_TRANSFER_TIMEOUT_SECONDS, verify=False) as client:
                response = await client.get(info.url)
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

        def listing():
            file_manager.ListFilesInGuest(vm, auth, guest_path.rsplit("\\", 1)[0], 10, False, None)

        try:
            await asyncio.to_thread(listing)
            return True
        except Exception as exc:  # noqa: BLE001
            if HAS_PYVMOMI and isinstance(exc, vim.fault.FileNotFoundFault):
                return False
            raise _wrap("file-exists", exc) from exc
