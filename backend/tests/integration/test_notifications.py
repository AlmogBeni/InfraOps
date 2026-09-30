"""Notifications are private to the engineer they are addressed to.

Run with a migrated database (``alembic upgrade head``):

    INFRAOPS_TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.notifications import router
from app.auth.dependencies import get_current_user
from app.core.errors import register_exception_handlers
from app.db.session import get_db
from app.models.jobs import JobStatus
from app.models.notifications import NotificationKind
from app.models.user import User
from app.repositories.jobs import JobRepository
from app.services.notifications import notify_requester

DATABASE_URL = os.environ.get("INFRAOPS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="INFRAOPS_TEST_DATABASE_URL is not set")


@pytest.fixture
async def sessions():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


async def _user(factory) -> User:
    async with factory() as db:
        user = User(username=f"n-{uuid.uuid4().hex[:8]}", is_active=True)
        db.add(user)
        await db.commit()
        return user


def _client(factory, user: User) -> AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def db_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_requester_sees_and_reads_only_their_own_notifications(sessions) -> None:
    owner = await _user(sessions)
    other = await _user(sessions)
    async with sessions() as db:
        job = await JobRepository(db).create_job(
            vm_name=f"NOTE-{uuid.uuid4().hex[:6]}",
            datacenter_id="datacenter-21",
            requested_by_user_id=owner.id,
            idempotency_key=None,
            request_payload={},
        )
        job.status = JobStatus.COMPLETED  # never claimable by other tests' workers
        created = notify_requester(
            db, job, NotificationKind.JOB_COMPLETED, f"{job.vm_name} is ready", "Verified."
        )
        await db.commit()

    async with _client(sessions, owner) as client:
        listing = (await client.get("/notifications")).json()
        assert listing["unread_count"] == 1
        assert [item["id"] for item in listing["items"]] == [str(created.id)]
        assert listing["items"][0]["kind"] == "JOB_COMPLETED"
        assert listing["items"][0]["job_id"] == str(job.id)

    async with _client(sessions, other) as client:
        assert (await client.get("/notifications")).json() == {"items": [], "unread_count": 0}
        # Someone else's notification is indistinguishable from a missing one.
        assert (await client.post(f"/notifications/{created.id}/read")).status_code == 404

    async with _client(sessions, owner) as client:
        assert (await client.post(f"/notifications/{created.id}/read")).status_code == 204
        listing = (await client.get("/notifications")).json()
        assert listing["unread_count"] == 0
        assert listing["items"][0]["read_at"] is not None
