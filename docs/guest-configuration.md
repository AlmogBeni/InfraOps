# Windows Guest Configuration

Guest automation runs through `app/services/guest/` — a controlled execution surface on
top of the VMware Tools guest API. Nothing SSH/SMB-based is required, and no agent is
installed ahead of time beyond VMware Tools.

## Interface

```python
class GuestOperations(ABC):
    async def run_powershell(target, vm_name, credentials, script,
                             timeout_seconds, *, secrets=None) -> CommandResult
    async def run_program(target, vm_name, credentials, program_path,
                          arguments, timeout_seconds, working_directory=None) -> CommandResult
    async def run_command(target, vm_name, credentials, GuestCommand, timeout_seconds)
    async def upload_file(...)   # bytes → guest path
    async def download_file(...)
    async def delete_file(...)
    async def file_exists(...)
```

`CommandResult` carries exit code, captured output and duration. Credentials
(`GuestCredentials`) render as `[REDACTED]` in reprs so accidental logging cannot leak
passwords.

## Production adapter (VMware Tools)

**Environment dependent** — requires VMware Tools running in the guest and valid local
administrator credentials resolved live from encrypted backend storage.

Nothing is ever interpreted by `cmd.exe` (`app/services/guest/scripts.py`):

* **PowerShell scripts** (`run_powershell`) are uploaded with `InitiateFileTransferToGuest`
  as a randomly named `C:\Windows\Temp\infraops-<random>.ps1` (UTF-8 with BOM) and run
  with `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <path>`.
  Exit codes follow `-File` semantics (`exit N`, or 1 for an uncaught terminating error).
* **Native programs** (`run_program`: `certutil`, `reg`, `sc`, `msiexec`, EXE installers,
  `shutdown`, `hostname`) are started by an uploaded *runner* script, itself started
  directly as `powershell.exe -File <runner>` through `StartProgramInGuest`. The runner
  launches the program with `System.Diagnostics.Process` (`UseShellExecute=false`, i.e.
  CreateProcess — no shell parsing), embeds the program path and argument string as
  single-quoted PowerShell literals, captures stdout/stderr into a temporary log file and
  exits with the program's exit code. It enforces the timeout itself (exit 1460) and exits
  9009 if the program cannot be started, so a runner failure is never mistaken for a
  detection rule's "absent" (1).
* **Secrets** (`run_powershell(..., secrets={...})`) are uploaded as separate random files;
  a preamble reads each into `$InfraOpsSecrets['<name>']` and deletes the file before the
  script body runs. Secret values never appear in the script text, on any command line,
  in 4688 process-creation events or in 4104 script-block logs.
* The runner, script, secret and output files are deleted afterwards. When a stage times
  out or the job is cancelled, the runner process is terminated
  (`TerminateProcessInGuest`); a program the runner already started (e.g. `msiexec`) may
  run to completion, which is why every installer is detection-guarded on retry.
* Failures map to actionable errors: invalid guest login, guest operations unavailable
  (Tools not running), timeouts. Ambiguous VM names (duplicates in different folders) are
  refused rather than guessed.

## What provisioning does inside the guest

| Stage | Mechanism |
|---|---|
| Windows installation | `Autounattend.xml` on a temporary virtual floppy configures image index, locale, keyboard, time zone, computer name and local administrator; the selected Windows ISO remains the only datastore-backed CD-ROM |
| First boot of a package | An answer file (specialize + oobeSystem only: computer name, time zone, locale, keyboard, local administrator password, every OOBE page skipped) is uploaded to `C:\Windows\Temp` and passed to `sysprep /generalize /oobe /reboot /unattend:` started detached; InfraOps then waits until `HKLM\SYSTEM\Setup` reports Setup/OOBE finished with the requested name. Sealed packages also get it as first-boot floppy media (see [vm-deployment-lifecycle.md](vm-deployment-lifecycle.md#windows-first-boot)) |
| Static IP / gateway / DNS | Right before applying: ICMP + vCenter-inventory re-check that the address is still free (the VM's own address is ignored on retry). Then an idempotent PowerShell script: DHCP disabled, existing IPv4 addresses and default route removed, `New-NetIPAddress`, `Set-DnsClientServerAddress` (adapter auto-detected, `ErrorActionPreference=Stop`) |
| DHCP mode | `Set-NetIPInterface -Dhcp Enabled` + DNS reset |
| Gateway/DNS validation | `Test-Connection` to gateway, `Resolve-DnsName` via first DNS server |
| Hostname | `$env:COMPUTERNAME` probe then `Rename-Computer -Force` |
| Domain join | `Add-Computer` with a PSCredential whose password is read from a self-deleting secret file (never in the script text); optional OU path (quoted literal, so values such as `OU=R&D` are safe); `-NewName` only when Windows does not already have the requested name (Add-Computer skips the join when it equals the current name) |
| Reboot | `shutdown.exe /r /t 10`, followed by availability probes (`hostname.exe`) that tolerate Tools being unavailable while the guest restarts |
| Answer-file cleanup | blank+ISO and Windows packages: after the floppy is removed, cached copies under `C:\Windows\Panther` / `Sysprep`, the uploaded `C:\Windows\Temp\infraops-unattend-*.xml` and AutoLogon residue are deleted |
| Certificates | see below |
| Applications | detection probes + controlled installers |

## Certificate deployment (idempotent)

1. Probe presence (uploaded script):
   `Get-ChildItem Cert:\LocalMachine\<Root|CA> | Where Thumbprint -eq '<fingerprint>'`
   — thumbprints are hex-validated before interpolation.
2. Skip when already present (idempotency) or refuse when expired.
3. Upload the public PEM to `C:\Windows\Temp\<generated>.cer` via the encrypted VMware
   file channel, import with `certutil -addstore -f Root|CA <file>`, delete the temp file.
4. Verify by re-probing the exact thumbprint.

Stores used (computer account, not user profile):

* ROOT → `Cert:\LocalMachine\Root` (Trusted Root Certification Authorities)
* INTERMEDIATE → `Cert:\LocalMachine\CA` (Intermediate Certification Authorities)

Private keys never pass through this platform — only public certificate bodies.

## Application installation

Detection methods (exit-code contract documented in the admin UI):

| Method | Probe |
|---|---|
| MSI_PRODUCT_CODE | `reg.exe query …\Uninstall\{GUID} /v DisplayName` (+32-bit view) |
| REGISTRY_KEY | `reg.exe query <key> [/v value]` |
| FILE_EXISTS | PowerShell `Test-Path -LiteralPath` (uploaded script) |
| SERVICE_EXISTS | `sc.exe query <name>` (1060 = absent) |
| SCRIPT | administrator-defined PowerShell, uploaded as a `.ps1` file (exit 0 installed / 1 absent) |

Installers:

* MSI → `msiexec /i "<path>" <admin args> /qn /norestart` (3010 ⇒ REBOOT_REQUIRED)
* EXE → `<path>` started directly with `<admin args>`
* POWERSHELL → `powershell -NoProfile -ExecutionPolicy Bypass -File "<path>" <args>`

Installer paths must be absolute UNC or drive paths inside an approved repository root
(segment-boundary match, no `..`, quotes or wildcards; extension must match the type) —
see `app/services/applications/paths.py`.

Every application is detected before install (skip when present) and re-detected during
validation afterwards.
