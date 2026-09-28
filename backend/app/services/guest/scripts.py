"""Construction of the files InfraOps executes inside Windows guests.

No command ever passes through ``cmd.exe``. Two kinds of execution exist:

* **Native programs** (``certutil``, ``reg``, ``msiexec`` …) are started by a
  small PowerShell *runner* script that is uploaded as a file and started with
  ``powershell.exe -File``. The runner launches the program with
  ``System.Diagnostics.Process`` (CreateProcess semantics, no shell parsing),
  captures stdout/stderr and writes them to a log file that the backend
  downloads. The program path and argument string are embedded as
  single-quoted PowerShell literals inside the *file*, so characters such as
  ``& | < > ^ % "`` have no special meaning anywhere.
* **PowerShell scripts** are uploaded as randomly named ``.ps1`` files and run
  through the same runner with ``-File``. Secret values (e.g. a domain-join
  password) are never part of the script text or of any process command line:
  each is uploaded to its own temporary file, read into ``$InfraOpsSecrets``
  by a short preamble and deleted before the script body runs. They therefore
  never appear in process-creation events (4688) or script-block logging
  (4104).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

POWERSHELL_PATH = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
HOSTNAME_PATH = r"C:\Windows\System32\hostname.exe"
GUEST_TEMP_DIR = r"C:\Windows\Temp"

# Exit codes reserved by the runner. 9009 mirrors cmd.exe's "command not found"
# so a runner failure is never mistaken for a detection rule's "absent" (1).
RUNNER_FAILURE_EXIT_CODE = 9009
RUNNER_TIMEOUT_EXIT_CODE = 1460  # ERROR_TIMEOUT

_SECRET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def ps_quote(value: str) -> str:
    """Return ``value`` as a PowerShell single-quoted literal.

    Inside a script *file* this is a complete escape: single-quoted strings
    expand nothing, and the only special character is ``'`` (doubled).
    """
    return "'" + value.replace("'", "''") + "'"


def new_temp_path(token: str, suffix: str) -> str:
    return rf"{GUEST_TEMP_DIR}\infraops-{token}.{suffix}"


def new_token() -> str:
    return uuid.uuid4().hex


def powershell_file_arguments(script_path: str) -> str:
    """Arguments that run one uploaded script file (paths are server-generated)."""
    return f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script_path}"'


def build_process_runner(
    program: str,
    arguments: str,
    output_path: str,
    timeout_seconds: float,
    *,
    working_directory: str | None = None,
    utf8_output: bool = False,
) -> str:
    """PowerShell script that runs one program without a shell and captures output."""
    if _CONTROL_CHARS.search(program) or _CONTROL_CHARS.search(arguments):
        raise ValueError("Program paths and arguments must not contain control characters.")
    timeout_ms = max(1000, int(timeout_seconds * 1000))
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$log = {ps_quote(output_path)}",
        "$utf8 = New-Object System.Text.UTF8Encoding $false",
        "try {",
        "    $psi = New-Object System.Diagnostics.ProcessStartInfo",
        f"    $psi.FileName = {ps_quote(program)}",
        f"    $psi.Arguments = {ps_quote(arguments)}",
        "    $psi.UseShellExecute = $false",
        "    $psi.CreateNoWindow = $true",
        "    $psi.RedirectStandardOutput = $true",
        "    $psi.RedirectStandardError = $true",
    ]
    if working_directory:
        lines.append(f"    $psi.WorkingDirectory = {ps_quote(working_directory)}")
    if utf8_output:
        lines += [
            "    $psi.StandardOutputEncoding = $utf8",
            "    $psi.StandardErrorEncoding = $utf8",
        ]
    lines += [
        "    $process = [System.Diagnostics.Process]::Start($psi)",
        "    $stdout = $process.StandardOutput.ReadToEndAsync()",
        "    $stderr = $process.StandardError.ReadToEndAsync()",
        f"    if (-not $process.WaitForExit({timeout_ms})) {{",
        "        try { $process.Kill() } catch { }",
        "        $message = 'InfraOps: the process exceeded its timeout and was terminated.'",
        "        [System.IO.File]::WriteAllText($log, $message, $utf8)",
        f"        exit {RUNNER_TIMEOUT_EXIT_CODE}",
        "    }",
        "    $process.WaitForExit()",
        "    $text = $stdout.Result",
        "    if ($stderr.Result) { $text = $text + [Environment]::NewLine + $stderr.Result }",
        "    [System.IO.File]::WriteAllText($log, $text, $utf8)",
        "    exit $process.ExitCode",
        "} catch {",
        "    $message = 'InfraOps runner error: ' + $_.Exception.Message",
        "    try { [System.IO.File]::WriteAllText($log, $message, $utf8) } catch { }",
        f"    exit {RUNNER_FAILURE_EXIT_CODE}",
        "}",
    ]
    return "\r\n".join(lines) + "\r\n"


def build_script_file(script: str, secret_paths: Mapping[str, str] | None = None) -> str:
    """Wrap a PowerShell script body with the secret-loading preamble."""
    lines = [
        "$ProgressPreference = 'SilentlyContinue'",
        "try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }",
        "$InfraOpsSecrets = @{}",
    ]
    for name, path in (secret_paths or {}).items():
        if not _SECRET_NAME.fullmatch(name):
            raise ValueError(f"Invalid secret name {name!r}.")
        quoted = ps_quote(path)
        lines += [
            "try {",
            f"    $InfraOpsSecrets[{ps_quote(name)}] = "
            f"[System.IO.File]::ReadAllText({quoted}, [System.Text.Encoding]::UTF8)",
            "} finally {",
            f"    Remove-Item -LiteralPath {quoted} -Force -ErrorAction SilentlyContinue",
            "}",
        ]
    lines.append(script)
    return "\r\n".join(lines) + "\r\n"


def encode_script(text: str) -> bytes:
    """UTF-8 with BOM: Windows PowerShell 5.1 reads BOM-less files as ANSI."""
    return b"\xef\xbb\xbf" + text.encode("utf-8")


@dataclass(frozen=True)
class GuestCommand:
    """Either a native program invocation or a PowerShell script."""

    program: str | None = None
    arguments: str = ""
    script: str | None = None

    @classmethod
    def powershell(cls, script: str) -> GuestCommand:
        return cls(script=script)

    @classmethod
    def native(cls, program: str, arguments: str = "") -> GuestCommand:
        return cls(program=program, arguments=arguments)

    @property
    def is_powershell(self) -> bool:
        return self.script is not None

    @property
    def display(self) -> str:
        if self.script is not None:
            return "powershell"
        return self.program or ""
