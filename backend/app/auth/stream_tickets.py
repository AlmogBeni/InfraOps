"""Short-lived, single-use tickets for the job event stream (SSE).

``EventSource`` cannot send an ``Authorization`` header. Instead of placing an
access token in the URL (where it would reach proxy logs and browser history),
the client POSTs with its bearer token to obtain a random ticket that is valid
for one connection to one job's stream for a few seconds. Only the SSE route
accepts tickets; every other route requires the ``Authorization`` header.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid

import redis.asyncio as aioredis

from app.core.config import get_settings

TICKET_TTL_SECONDS = 30
_KEY_PREFIX = "infraops:stream-ticket:"


def _key(ticket: str) -> str:
    # Store only a hash so a Redis dump never contains usable tickets.
    return _KEY_PREFIX + hashlib.sha256(ticket.encode("utf-8")).hexdigest()


class StreamTicketStore:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = aioredis.from_url(redis_url or get_settings().redis_url, decode_responses=True)

    async def issue(self, *, user_id: uuid.UUID, job_id: uuid.UUID) -> str:
        ticket = secrets.token_urlsafe(32)
        payload = json.dumps({"user_id": str(user_id), "job_id": str(job_id)})
        await self._redis.set(_key(ticket), payload, ex=TICKET_TTL_SECONDS)
        return ticket

    async def consume(self, ticket: str, *, job_id: uuid.UUID) -> uuid.UUID | None:
        """Atomically redeem a ticket; returns the user id or None."""
        if not ticket or len(ticket) > 128:
            return None
        raw = await self._redis.getdel(_key(ticket))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if data.get("job_id") != str(job_id):
                return None
            return uuid.UUID(data["user_id"])
        except (ValueError, KeyError, TypeError):
            return None


_store: StreamTicketStore | None = None


def get_stream_ticket_store() -> StreamTicketStore:
    global _store
    if _store is None:
        _store = StreamTicketStore()
    return _store
