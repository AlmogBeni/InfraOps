"""Live job-event publishing over Redis pub/sub (consumed by the SSE endpoint)."""

from __future__ import annotations

import json

import redis.asyncio as aioredis

from app.core.config import get_settings


def job_channel(job_id: str) -> str:
    return f"infraops:job-events:{job_id}"


def make_event(
    job_id: str,
    *,
    stage: str | None,
    status: str,
    progress: int,
    message: str = "",
    **extra,
) -> dict:
    event = {
        "job_id": job_id,
        "stage": stage,
        "status": status,
        "progress": progress,
        "message": message,
    }
    event.update(extra)
    return event


class JobEventPublisher:
    """Publishes JSON events to a per-job Redis channel."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = aioredis.from_url(
            redis_url or get_settings().redis_url, decode_responses=True
        )

    async def publish(self, job_id: str, event: dict) -> None:
        try:
            await self._redis.publish(job_channel(job_id), json.dumps(event, default=str))
        except Exception:  # noqa: BLE001 - event delivery must never break a job
            from app.core.logging import get_logger

            get_logger(__name__).warning("Failed to publish job event for %s", job_id)

    async def publish_stage(
        self, job_id: str, *, stage: str | None, status: str, progress: int, message: str = ""
    ) -> None:
        await self.publish(job_id, make_event(job_id, stage=stage, status=status,
                                              progress=progress, message=message))

    async def subscribe(self, job_id: str):
        """Return a pub/sub subscribed to the job channel (used by SSE)."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(job_channel(job_id))
        return pubsub

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # noqa: BLE001
            pass
