"""Guest execution never goes through cmd.exe and never exposes secrets."""

from __future__ import annotations

import pytest

from app.services.guest.base import CommandResult, GuestCredentials, GuestOperations
from app.services.guest.scripts import (
    POWERSHELL_PATH,
    RUNNER_FAILURE_EXIT_CODE,
    build_process_runner,
    build_script_file,
    ps_quote,
)
from app.workers.stages import DOMAIN_JOIN_PASSWORD_SECRET, build_domain_join_script

CREDENTIALS = GuestCredentials(username="admin", password="local-secret")
HOSTILE = 'p&ss|w<o>rd^%PATH%"\'x'


class RecordingGuest(GuestOperations):
    name = "recording"

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.programs: list[tuple[str, str]] = []

    async def run_program(self, target, vm_name, credentials, program_path, arguments, timeout_seconds,
                          working_directory=None) -> CommandResult:
        self.programs.append((program_path, arguments))
        return CommandResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.0)

    async def upload_file(self, target, vm_name, credentials, content, guest_path) -> None:
        self.uploads[guest_path] = content

    async def download_file(self, target, vm_name, credentials, guest_path) -> bytes:
        return b""

    async def delete_file(self, target, vm_name, credentials, guest_path) -> None:
        self.deleted.append(guest_path)

    async def file_exists(self, target, vm_name, credentials, guest_path) -> bool:
        return guest_path in self.uploads


@pytest.mark.asyncio
async def test_run_powershell_uploads_script_and_keeps_secret_out_of_script_and_command_line() -> None:
    guest = RecordingGuest()
    script = build_domain_join_script("corp.example.com", "svc-join", "OU=R&D,DC=corp", "SRV01")

    await guest.run_powershell(
        object(), "SRV01", CREDENTIALS, script, 60, secrets={DOMAIN_JOIN_PASSWORD_SECRET: HOSTILE}
    )

    assert len(guest.programs) == 1
    program, arguments = guest.programs[0]
    assert program == POWERSHELL_PATH
    assert "cmd.exe" not in program.lower() and "-Command" not in arguments
    assert arguments.startswith("-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ")
    assert HOSTILE not in arguments

    script_paths = [path for path in guest.uploads if path.endswith(".ps1")]
    secret_paths = [path for path in guest.uploads if path.endswith(".dat")]
    assert len(script_paths) == 1 and len(secret_paths) == 1
    assert script_paths[0] in arguments
    uploaded_script = guest.uploads[script_paths[0]].decode("utf-8-sig")
    assert HOSTILE not in uploaded_script
    assert "$InfraOpsSecrets['domain_join_password']" in uploaded_script
    assert f"Remove-Item -LiteralPath {ps_quote(secret_paths[0])}" in uploaded_script
    assert guest.uploads[secret_paths[0]] == HOSTILE.encode("utf-8")
    # Every temporary file is removed afterwards.
    assert set(guest.deleted) == set(guest.uploads)


def test_runner_embeds_program_and_arguments_as_literals() -> None:
    runner = build_process_runner(
        r"C:\Program Files\x\setup.exe", '/S /D="C:\\a&b" %PATH% | more', r"C:\Windows\Temp\o.log", 30
    )
    assert "$psi.FileName = 'C:\\Program Files\\x\\setup.exe'" in runner
    assert "$psi.Arguments = '/S /D=\"C:\\a&b\" %PATH% | more'" in runner
    assert "$psi.UseShellExecute = $false" in runner
    assert "cmd.exe" not in runner.lower()
    assert f"exit {RUNNER_FAILURE_EXIT_CODE}" in runner


def test_runner_rejects_control_characters() -> None:
    with pytest.raises(ValueError):
        build_process_runner("setup.exe", "/q\r\nmalicious", r"C:\Windows\Temp\o.log", 30)


def test_secret_names_are_validated() -> None:
    with pytest.raises(ValueError):
        build_script_file("Write-Output 1", {"bad name'); rm -r": r"C:\Windows\Temp\x.dat"})


def test_single_quote_escaping_is_complete() -> None:
    assert ps_quote("it's") == "'it''s'"
    assert ps_quote("$(evil)") == "'$(evil)'"


@pytest.mark.asyncio
async def test_vmware_tools_starts_powershell_runner_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.guest.vmware_tools import VMwareToolsGuestOperations

    guest = object.__new__(VMwareToolsGuestOperations)
    uploads: dict[str, bytes] = {}
    started: list[tuple[str, str]] = []

    async def upload(target, vm_name, credentials, content, path):
        uploads[path] = content

    async def download(target, vm_name, credentials, path):
        return b"captured output"

    async def delete(target, vm_name, credentials, path):
        return None

    async def start_and_wait(target, vm_name, credentials, program, arguments, timeout, *, program_label):
        started.append((program, arguments))
        return 3010

    monkeypatch.setattr(guest, "upload_file", upload)
    monkeypatch.setattr(guest, "download_file", download)
    monkeypatch.setattr(guest, "delete_file", delete)
    monkeypatch.setattr(guest, "_start_and_wait", start_and_wait)

    result = await guest.run_program(
        object(), "SRV01", CREDENTIALS, r"C:\Windows\System32\msiexec.exe", '/i "\\\\srv\\a&b.msi" /qn', 120
    )

    assert result.exit_code == 3010
    assert result.stdout == "captured output"
    program, arguments = started[0]
    assert program == POWERSHELL_PATH
    assert "cmd.exe" not in arguments.lower()
    runner_path = next(path for path in uploads if path.endswith("-run.ps1"))
    assert f'-File "{runner_path}"' in arguments
    runner = uploads[runner_path].decode("utf-8-sig")
    assert "$psi.Arguments = '/i \"\\\\srv\\a&b.msi\" /qn'" in runner
