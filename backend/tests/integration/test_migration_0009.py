"""Migration 0009 against rows written by the previous release.

Needs a role that may create databases (the schema owner in CI):

    INFRAOPS_MIGRATION_TEST_URL=postgresql+asyncpg://owner:...@host/postgres pytest tests/integration
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import uuid

import asyncpg
import pytest

ADMIN_URL = os.environ.get("INFRAOPS_MIGRATION_TEST_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="INFRAOPS_MIGRATION_TEST_URL is not set")

BACKEND = pathlib.Path(__file__).resolve().parents[2]
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _with_database(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


def _alembic(url: str, *args: str) -> None:
    env = {**os.environ, "MIGRATION_DATABASE_URL": url, "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.fixture
async def database():
    name = f"infraops_migration_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_dsn(ADMIN_URL))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield _with_database(ADMIN_URL, name)
    admin = await asyncpg.connect(_dsn(ADMIN_URL))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _seed_previous_release(conn) -> dict[str, uuid.UUID]:
    await conn.execute(
        "INSERT INTO users (id, username, is_active) VALUES ($1, 'engineer', true)", USER_ID
    )
    jobs = {
        # name: (status, payload guest, reserved ip, steps [(key, name, sequence, status)])
        "OLD-SOURCE-QUEUED": ("QUEUED", {"template_id": "package-1", "iso_id": None}, None,
                              [("clone_vm", "Create virtual machine", 3, "PENDING")]),
        "NO-ISO-PAUSED": ("ACTION_REQUIRED", {"template_id": None, "iso_id": None}, None,
                          [("clone_vm", "Create virtual machine", 3, "SUCCEEDED"),
                           ("wait_for_guest_os", "Wait for guest operating system", 8,
                            "WAITING_FOR_PREREQUISITE")]),
        "ISO-PAUSED": ("ACTION_REQUIRED", {"template_id": None, "iso_id": "iso-1"}, "10.0.0.5",
                       [("clone_vm", "Create virtual machine", 3, "SUCCEEDED"),
                        ("power_on", "Power on VM", 7, "SUCCEEDED"),
                        ("wait_for_guest_os", "Wait for guest operating system", 8,
                         "WAITING_FOR_PREREQUISITE"),
                        ("configure_guest_network", "Configure guest network", 11, "PENDING")]),
        "ISO-QUEUED": ("QUEUED", {"template_id": None, "iso_id": "iso-1"}, "10.0.0.6",
                       [("clone_vm", "Create virtual machine", 3, "PENDING")]),
        "OLD-SOURCE-DONE": ("COMPLETED", {"template_id": "package-1", "iso_id": None}, None,
                            [("clone_vm", "Create virtual machine", 3, "SUCCEEDED"),
                             ("power_on", "Power on VM", 7, "SUCCEEDED")]),
    }
    ids: dict[str, uuid.UUID] = {}
    for name, (status, guest, ip, steps) in jobs.items():
        job_id = uuid.uuid4()
        ids[name] = job_id
        source = "template" if guest["template_id"] else "blank"
        await conn.execute(
            """
            INSERT INTO provisioning_jobs (id, job_type, status, vm_name, requested_by_user_id,
                                           current_stage, action_required, reserved_ipv4)
            VALUES ($1, 'vm_provisioning', $2, $3, $4, $5, $6, $7)
            """,
            job_id, status, name, USER_ID, steps[-1][0],
            "Press a key in the VM console." if status == "ACTION_REQUIRED" else None, ip,
        )
        await conn.execute(
            "INSERT INTO vm_provisioning_requests (id, job_id, payload) VALUES ($1, $2, $3::jsonb)",
            uuid.uuid4(), job_id, json.dumps({"source_type": source, "guest": guest}),
        )
        for key, step_name, sequence, step_status in steps:
            await conn.execute(
                """
                INSERT INTO provisioning_job_steps (id, job_id, stage_key, name, sequence, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid.uuid4(), job_id, key, step_name, sequence, step_status,
            )
    await conn.execute(
        """
        INSERT INTO notifications (id, user_id, job_id, kind, title, message)
        VALUES ($1, $2, $3, 'JOB_ACTION_REQUIRED', 'ISO-PAUSED needs attention', 'Press a key.')
        """,
        uuid.uuid4(), USER_ID, ids["ISO-PAUSED"],
    )
    await conn.execute(
        """
        INSERT INTO platform_settings (key, value, description)
        VALUES ('default_timeouts', '{"value": {"clone_minutes": 45, "vmware_tools_minutes": 20}}', '')
        """
    )
    return ids


async def _job(conn, name: str):
    return await conn.fetchrow(
        "SELECT status::text AS status, error_summary, error_detail FROM provisioning_jobs WHERE vm_name = $1",
        name,
    )


async def _steps(conn, name: str) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        """
        SELECT s.stage_key, s.name, s.sequence, s.status::text AS status, s.error_human
        FROM provisioning_job_steps AS s JOIN provisioning_jobs AS j ON j.id = s.job_id
        WHERE j.vm_name = $1
        """,
        name,
    )
    return {row["stage_key"]: row for row in rows}


async def _enum_labels(conn, name: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = $1",
        name,
    )
    return {row["enumlabel"] for row in rows}


async def _timeouts(conn) -> dict:
    raw = await conn.fetchval("SELECT value FROM platform_settings WHERE key = 'default_timeouts'")
    return json.loads(raw)["value"]


async def test_upgrade_closes_removed_workflows_and_removes_human_gates(database) -> None:
    _alembic(database, "upgrade", "0008_user_notifications")
    conn = await asyncpg.connect(_dsn(database))
    try:
        await _seed_previous_release(conn)
    finally:
        await conn.close()

    _alembic(database, "upgrade", "head")
    conn = await asyncpg.connect(_dsn(database))
    try:
        closed = await _job(conn, "OLD-SOURCE-QUEUED")
        assert closed["status"] == "FAILED"
        assert "no longer exists" in closed["error_summary"]
        assert "Submit a new request" in closed["error_summary"]

        no_iso = await _job(conn, "NO-ISO-PAUSED")
        assert no_iso["status"] == "PARTIALLY_COMPLETED"  # its VM exists
        assert "no longer exists" in no_iso["error_summary"]
        assert no_iso["error_detail"] == "Press a key in the VM console."

        paused = await _job(conn, "ISO-PAUSED")
        assert paused["status"] == "PARTIALLY_COMPLETED"
        assert "Retry the failed stage" in paused["error_summary"]
        steps = await _steps(conn, "ISO-PAUSED")
        assert set(steps) == {"create_vm", "power_on", "wait_for_guest_os", "configure_guest_network"}
        assert steps["wait_for_guest_os"]["status"] == "FAILED"
        assert steps["wait_for_guest_os"]["name"] == "Install Windows"
        assert steps["power_on"]["name"] == "Power on and start Windows Setup"
        assert steps["configure_guest_network"]["sequence"] == 13  # after the data-disk stages

        assert (await _job(conn, "ISO-QUEUED"))["status"] == "QUEUED"
        assert (await _job(conn, "OLD-SOURCE-DONE"))["status"] == "COMPLETED"
        history = await _steps(conn, "OLD-SOURCE-DONE")
        assert set(history) == {"create_vm", "power_on"}
        assert history["power_on"]["name"] == "Power on VM"  # history keeps its wording

        assert await conn.fetchval("SELECT kind FROM notifications") == "JOB_FAILED"
        assert await _timeouts(conn) == {"create_vm_minutes": 45, "vmware_tools_minutes": 20}
        assert "ACTION_REQUIRED" not in await _enum_labels(conn, "job_status")
        assert "WAITING_FOR_PREREQUISITE" not in await _enum_labels(conn, "step_status")
        assert not await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_name = 'provisioning_jobs' AND column_name = 'action_required'
            """
        )
        # The reservation of the still-queued job is enforced by the rebuilt index.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO provisioning_jobs (id, job_type, status, vm_name, reserved_ipv4)
                VALUES ($1, 'vm_provisioning', 'QUEUED', 'OTHER', '10.0.0.6')
                """,
                uuid.uuid4(),
            )
    finally:
        await conn.close()

    _alembic(database, "downgrade", "-1")
    conn = await asyncpg.connect(_dsn(database))
    try:
        steps = await _steps(conn, "ISO-PAUSED")
        assert "clone_vm" in steps and "create_vm" not in steps
        assert steps["configure_guest_network"]["sequence"] == 11
        assert steps["wait_for_guest_os"]["name"] == "Wait for guest operating system"
        assert await _timeouts(conn) == {"clone_minutes": 45, "vmware_tools_minutes": 20}
        assert "ACTION_REQUIRED" in await _enum_labels(conn, "job_status")
        assert await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_name = 'provisioning_jobs' AND column_name = 'action_required'
            """
        )
    finally:
        await conn.close()

    _alembic(database, "upgrade", "head")
