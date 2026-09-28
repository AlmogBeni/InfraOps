"""Guarantees that only a real PostgreSQL can prove.

Run with a migrated database (``alembic upgrade head``):

    INFRAOPS_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.service import AuthService, RefreshTokenReuseError
from app.core.errors import AuthenticationError
from app.models.jobs import JobStatus
from app.models.user import User
from app.repositories.jobs import JobRepository

DATABASE_URL = os.environ.get("INFRAOPS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="INFRAOPS_TEST_DATABASE_URL is not set")


@pytest.fixture
async def sessions():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


async def _create_job(factory, name: str, *, ip: str | None = None, status=JobStatus.QUEUED):
    async with factory() as db:
        job = await JobRepository(db).create_job(
            vm_name=name,
            datacenter_id="datacenter-21",
            requested_by_user_id=None,
            idempotency_key=None,
            request_payload={},
            reserved_ipv4=ip,
        )
        job.status = status
        await db.commit()
        return job.id


async def test_only_one_active_job_per_vm_name_under_concurrency(sessions) -> None:
    name = f"RACE-{uuid.uuid4().hex[:6]}"
    results = await asyncio.gather(
        *[_create_job(sessions, name if i % 2 else name.lower()) for i in range(6)],
        return_exceptions=True,
    )
    created = [r for r in results if isinstance(r, uuid.UUID)]
    conflicts = [r for r in results if isinstance(r, IntegrityError)]
    assert len(created) == 1
    assert len(conflicts) == 5


async def test_static_ip_is_reserved_by_one_active_job(sessions) -> None:
    ip = f"10.99.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    await _create_job(sessions, f"IP-A-{uuid.uuid4().hex[:6]}", ip=ip)
    with pytest.raises(IntegrityError):
        await _create_job(sessions, f"IP-B-{uuid.uuid4().hex[:6]}", ip=ip)
    # A finished job releases the reservation.
    await _create_job(sessions, f"IP-C-{uuid.uuid4().hex[:6]}", ip=f"{ip}0"[:15], status=JobStatus.COMPLETED)


async def test_worker_claims_only_free_slots_and_loses_ownership_when_interrupted(sessions) -> None:
    for _ in range(3):
        await _create_job(sessions, f"SLOT-{uuid.uuid4().hex[:6]}")
    async with sessions() as db:
        claimed = await JobRepository(db).claim_due_jobs(limit=1, worker_id="worker-a")
    assert len(claimed) == 1
    job_id = claimed[0].id
    async with sessions() as db:
        assert await JobRepository(db).heartbeat(job_id, "worker-a") is False
        assert await JobRepository(db).heartbeat(job_id, "worker-b") is None
    async with sessions() as db:
        stale = await JobRepository(db).claim_stale_running_jobs(
            stale_before=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1), limit=50
        )
        assert job_id in {job.id for job in stale}
        for job in stale:
            job.status = JobStatus.INTERRUPTED
            job.worker_id = None
        await db.commit()
    async with sessions() as db:
        assert await JobRepository(db).heartbeat(job_id, "worker-a") is None


async def test_refresh_token_rotation_detects_reuse_and_logout_revokes(sessions, monkeypatch) -> None:
    from app.auth import service as service_module

    monkeypatch.setattr(service_module, "_CONCURRENT_REFRESH_GRACE", dt.timedelta(0))
    auth = AuthService()
    async with sessions() as db:
        user = User(username=f"u-{uuid.uuid4().hex[:8]}", is_active=True)
        db.add(user)
        await db.flush()
        await db.refresh(user, ["roles"])
        first = await auth.issue_tokens(db, user)
        await db.commit()

    async with sessions() as db:
        _, second = await auth.rotate_refresh_token(db, first.refresh_token)
        await db.commit()
    await asyncio.sleep(0.01)
    async with sessions() as db:
        with pytest.raises(RefreshTokenReuseError):
            await auth.rotate_refresh_token(db, first.refresh_token)
        await db.commit()
    # The whole family is revoked, including the legitimately rotated token.
    async with sessions() as db:
        with pytest.raises(AuthenticationError):
            await auth.rotate_refresh_token(db, second.refresh_token)

    async with sessions() as db:
        user = await auth.get_active_user(db, str(user.id))
        fresh = await auth.issue_tokens(db, user)
        await db.commit()
    async with sessions() as db:
        assert await auth.revoke_refresh_token(db, fresh.refresh_token) == user.id
        await db.commit()
    async with sessions() as db:
        with pytest.raises(AuthenticationError):
            await auth.rotate_refresh_token(db, fresh.refresh_token)


async def test_audit_trail_rejects_update_delete_and_truncate(sessions) -> None:
    async with sessions() as db:
        await db.execute(
            text("INSERT INTO audit_events (id, action, details) VALUES (:id, 'TEST', '{}')"),
            {"id": uuid.uuid4()},
        )
        await db.commit()
    for statement in ("UPDATE audit_events SET action = 'X'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        async with sessions() as db:
            with pytest.raises(DBAPIError, match="append-only|permission denied"):
                await db.execute(text(statement))
