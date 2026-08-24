# Windows Guest Configuration

Guest automation runs through `app/services/guest/` — a controlled execution surface on
top of the VMware Tools guest API. Nothing SSH/SMB-based is required, and no agent is
installed ahead of time beyond VMware Tools.

## Interface

```python
class GuestOperations(ABC):
    async def run_program(target, vm_name, credentials, program_path,
                          arguments, timeout_seconds, working_directory=None) -> CommandResult
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
administrator credentials resolved from the secrets provider.

* Programs start via `StartProgramInGuest`; because the API cannot stream stdout, commands
  are wrapped as `cmd.exe /c ""program" args > tempfile 2>&1"` with a per-invocation temp
  file that is downloaded and deleted afterwards.
* Failures map to actionable errors: invalid guest login, guest operations unavailable
  (Tools not running), timeouts.

## What provisioning does inside the guest

| Stage | Mechanism |
|---|---|
| Static IP / gateway / DNS | PowerShell: `New-NetIPAddress`, `Set-DnsClientServerAddress` (adapter auto-detected; DHCP disabled first) |
| DHCP mode | `Set-NetIPInterface -Dhcp Enabled` + DNS reset |
| Gateway/DNS validation | `Test-Connection` to gateway, `Resolve-DnsName` via first DNS server |
| Hostname | `$env:COMPUTERNAME` probe then `Rename-Computer -Force` |
| Domain join | `Add-Computer` with an inline PSCredential built from the resolved domain-join secret; optional OU path |
| Reboot | `Restart-Computer -Force`, followed by availability probes (`cmd /c exit 0`) |
| Certificates | see below |
| Applications | detection probes + controlled installers |

## Certificate deployment (idempotent)

1. Probe presence:
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
| FILE_EXISTS | PowerShell `Test-Path -LiteralPath` |
| SERVICE_EXISTS | `sc.exe query <name>` (1060 = absent) |
| SCRIPT | administrator-defined PowerShell (exit 0 installed / 1 absent) |

Installers:

* MSI → `msiexec /i "<path>" <admin args> /qn /norestart` (3010 ⇒ REBOOT_REQUIRED)
* EXE → `"<path>" <admin args>`
* POWERSHELL → `powershell -NoProfile -ExecutionPolicy Bypass -File "<path>" <args>`

Every application is detected before install (skip when present) and re-detected during
validation afterwards.
