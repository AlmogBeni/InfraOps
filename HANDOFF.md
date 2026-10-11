# InfraOps — handoff (2026-10-11)

Paste this file into a new conversation, or ask Claude to read `HANDOFF.md`, to continue.

## What InfraOps is

An internal platform that provisions VMware vSphere VMs end to end. It is built with:

- FastAPI, SQLAlchemy async and Alembic on PostgreSQL;
- an asyncio worker using pyVmomi;
- a React, TypeScript and Vite wizard.

Repo: github.com/AlmogBeni/InfraOps. Work happens directly on `main`. The user tests against a real vCenter (`srvildc19.corp.deltagalil.com`, service account `DOMCORP\infraops.svc`).

**Rules to keep:**

- Guest execution never uses a shell.
- Secrets reach the guest only as self-deleting files.
- Every VM carries an ownership marker.
- The audit trail is append-only.
- The application uses a least-privilege DB role.
- Access is controlled by RBAC.
- Never edit existing migrations; add new forward ones.

## How provisioning works (main @ `14fe6de`)

Every VM is created empty, and Windows Server is installed unattended from an ISO. There is no other source (OVF/OVA support was removed) and no human gate anywhere. Jobs either complete or fail with an actionable error. The only manual prerequisite is placing the ISO on a datastore.

The 26 stages (`backend/app/workers/state_machine.py`):

- **Before Windows:** validate_request, connect_vcenter, validate_infrastructure, create_vm, configure_hardware, attach_network_adapter.
- **Installation:** prepare_unattended_install, power_on, wait_for_guest_os ("Install Windows"), wait_for_tools.
- **After installation:** cleanup_unattended_media, add_data_disks, initialize_data_disks.
- **Guest configuration:** configure_guest_network, validate_network, configure_hostname, join_domain, reboot_guest, wait_guest_ready.
- **Certificates and software:** root and intermediate certificates, validate_certificates, resolve_dependencies, install_applications, validate_applications.
- **Last:** final_validation.

### Key behaviour

**The VM `create_vm` builds**
- an LSI Logic SAS controller holding only the OS disk;
- the Windows ISO on SATA 0:0 and the host Tools ISO (`[] /vmimages/tools-isoimages/windows.iso`) on SATA 0:1;
- the answer CD on SATA 0:2.

All three CDs are temporary.

**Answer CD** (`backend/app/services/windows_unattend.py`). Golden files are in `backend/tests/golden/`. It holds two files:
- `Autounattend.xml`:
  - EULA accepted; disk 0 wiped, with ModifyPartitions numbered 1..n and Windows on C:;
  - `/IMAGE/INDEX` 2 or 4, which are Standard or Datacenter with the Desktop Experience;
  - an optional product key; all OOBE pages hidden;
  - one AutoLogon.
- `InfraOps-FirstLogon.ps1`:
  - During specialize, Windows copies it to `C:\Windows\Setup\Scripts`. First logon runs that copy, with the CD only as a fallback.
  - It finds the Tools CD by looking for `setup64.exe` or `setup.exe`, skipping the Windows ISO (the only CD with `\sources`), and installs Tools silently.
  - It logs to `C:\Windows\Temp\infraops-firstlogon.log`; the Tools installer logs to `C:\Windows\Temp\infraops-vmtools-msi.log`.

**Boot prompt:** the space bar is pressed through `PutUsbScanCodes` every 3 s for 45 s, about 16 vCenter tasks.

**"Install Windows" is watched, not timed.**
- Every 20 s it reads the power state, host-measured CPU use (`quickStats.overallCpuUsage` against `runtime.maxCpuUsage`) and the Tools status.
- Every minute it writes `artifacts.live_status`, which the job page shows on the running stage.
- It succeeds when Tools runs. Tools is installed only at first logon, after OOBE, so no guest file transfer is needed.
- It keeps waiting while the VM is busy, defined as CPU at least 3% of capacity and at least 100 MHz.
- It fails when the VM powers off, after 30 min idle, or at the "Install Windows" limit: default 360 min, configurable 30–720.

**Cleanup**, in order:
1. delete the answer CD and its file;
2. disconnect the ISOs and set the boot order to disk only;
3. scrub the cached answer files and AutoLogon values in the guest. This step fails the stage, retryably, if it can't run.

**Data disks** are hot-added after installation, then brought online and formatted NTFS (`Data1`, `Data2`, …).

**Guest configuration** needs VMware Tools. Every step uploads a PowerShell script through Tools, and those file transfers go directly to the ESXi host on TCP 443, not through vCenter.
- Static IP is applied after a live conflict re-check.
- Domain join uses the credential selected in the wizard; its password is passed as a self-deleting file.
- Final validation checks the IP, the domain identity and `InstallationType == Server` (Desktop Experience).

**Console screenshot** is taken when power_on, wait_for_guest_os or wait_for_tools fails. Administrators view it on the job page.

**Preflight** runs server-side on every submit. It blocks:
- non-Windows-Server ISOs (volume label must contain `SSS_`);
- bad product keys;
- ESXi hosts that cannot be reached over HTTPS;
- placement, capacity, IP-conflict, credential, certificate, application and installer-root problems.

**Credentials**, including domain join and product keys, live in **Administration → Credentials**, encrypted in PostgreSQL. `.env` variables such as `SECRETS_DOMAIN_JOIN_USERNAME` are not read by anything. The wizard preselects a credential named `domain-join`.

**Migration `0009_iso_only_unattended`** removed the ACTION_REQUIRED and WAITING states and renamed the stage clone_vm to create_vm. Jobs from removed workflows stay read-only and cannot be retried.

## Recent commits on main

- `8c6148d` ISO-only and no human gates: the big refactor, plus migration 0009.
- `df4d229` Answer-file partitions numbered in order; fewer boot key presses.
- `0c27462` Desktop Experience editions; ESXi transfer errors name the host and cause; HTTPS preflight to the ESXi hosts; cleanup releases the ISOs first.
- `a079e94` First-logon Tools script on the answer CD accepts `setup64.exe` or `setup.exe`, shows progress and logs.
- `14fe6de` "Install Windows" watches the VM live instead of a fixed timeout; the Tools script is copied to disk during specialize.

## Status of real-vCenter testing

- **Confirmed working:**
  - the ISO boots unattended under EFI;
  - disk partitioning;
  - Windows Server 2025 Standard installs with the Desktop Experience;
  - AutoLogon reaches the desktop;
  - the ESXi HTTPS preflight check passes.
- **Last run (INFRAOPS-TEST, before `14fe6de`) was extremely slow:** still at "Installing 42%" after 2 h. The old fixed limit failed it, and cleanup removed the answer CD, so that VM can never get Tools. It must be deleted.
- **Not yet seen working on real vCenter:**
  - Tools installation at first logon;
  - everything after it: data disks, static IP, domain join, certificates, applications, final validation.

## Next steps

1. **Before the next run:**
   - deploy `14fe6de`;
   - delete the INFRAOPS-TEST VM;
   - in **Settings → Default operation time limits** raise "Install Windows" if it still shows 120 (a saved value overrides the new 360 default);
   - create the `domain-join` credential if it doesn't exist.
2. **During the run,** read the stage's live status.
   - If it fails as idle, use the console screenshot.
   - If the desktop shows without Tools, read `C:\Windows\Temp\infraops-firstlogon.log` and check what is at the Tools CD's root. An empty drive means the host doesn't provide the Tools ISO.
3. **Investigate why Setup is so slow** on that host or datastore; 15–40 min is normal. The live CPU figure shows whether the VM is busy or starved.
4. **Manual end-to-end checklist** on real vCenter:
   - EFI with and without Secure Boot, and BIOS;
   - Server 2019, 2022 and 2025, Standard and Datacenter;
   - data disks, static IP and DHCP, domain join with an OU, certificates, an application with a dependency;
   - the failure path: screenshot, then retry;
   - cancelling during installation removes the answer media (`UNATTENDED_MEDIA_REMOVED` audit event).
5. **Not implemented (by decision):** Windows client editions (10/11), which preflight blocks. If wanted later, they need a client answer file (generic key, local account, extra OOBE pages) and, for Windows 11, a vTPM.
6. **Possible improvement discussed:** applying the static IP during Windows Setup (Microsoft-Windows-TCPIP, keyed to the NIC MAC) so it doesn't depend on Tools. Domain join should stay a Tools-based stage, because putting the domain password in the answer file is forbidden.

## Working in this repo (Windows dev machine)

- **Backend venv:** `backend/.venv/Scripts/python.exe` (Python 3.13; `greenlet` was installed manually for local Alembic). Running app code outside pytest needs these environment variables: `ENVIRONMENT=development INFRASTRUCTURE_MODE=mock SECRET_KEY=<32+ chars> COOKIE_SECURE=false`.
- **What CI runs** (`.github/workflows/ci.yml`):
  - `ruff check app tests migrations`;
  - `alembic upgrade head`, then `downgrade -1`, then `upgrade head`, then `alembic check` and `python -m app.db.roles`;
  - `pytest`;
  - `pytest tests/integration` with `INFRAOPS_TEST_DATABASE_URL` (runtime role) and `INFRAOPS_MIGRATION_TEST_URL` (owner role connecting to the `postgres` DB);
  - frontend `npm run typecheck && npm test && npm run bundle`.
- **Last results:** backend 354 passed, 7 skipped (DB-only); frontend 93 passed; bundle builds.
- **Local PostgreSQL:** CI's database steps were last run against portable PostgreSQL 16 binaries in a session scratchpad, which is gone. Re-download them if needed and run on a private port.
- **Golden answer files:** regenerate with `UPDATE_GOLDEN=1 pytest tests/test_windows_unattend.py -k golden`. Generated PowerShell can be checked locally with `[System.Management.Automation.Language.Parser]::ParseInput`, and answer ISOs with `Mount-DiskImage`.
- **Files must be LF** (`.gitattributes` sets `eol=lf`). When editing through Python, write with `newline="\n"`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The user asks explicitly before each commit and push to main.
- **Docs to keep in sync:** `README.md`, `docs/vm-deployment-lifecycle.md`, `docs/vmware-integration.md` (privilege table), `docs/vm-provisioning.md`, `docs/production-deployment.md` (upgrade notes), `docs/security.md`, `docs/guest-configuration.md`.
