"""Login rate limiting shared by every API process.

Counters live in Redis (fixed one-minute windows keyed by client IP and by
username), so limits survive restarts and apply across replicas. If Redis is
unreachable the limiter degrades to a per-process sliding window rather than
failing open.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class SlidingWindowRateLimiter:
    """In-process fallback."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        """Return True when the action is permitted under the configured limit."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


class RedisRateLimiter:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = aioredis.from_url(redis_url or get_settings().redis_url, decode_responses=True)
        self._fallback = SlidingWindowRateLimiter()

    async def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        bucket = int(time.time() // window_seconds)
        redis_key = f"infraops:ratelimit:{digest}:{bucket}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds * 2)
                count, _ = await pipe.execute()
            return int(count) <= limit
        except Exception:  # noqa: BLE001 - degrade to local limiting, never fail open
            log.warning("Rate limiter backend unavailable; using in-process fallback.")
            return self._fallback.allow(key, limit, window_seconds)


_login_limiter: RedisRateLimiter | None = None


def get_login_rate_limiter() -> RedisRateLimiter:
    global _login_limiter
    if _login_limiter is None:
        _login_limiter = RedisRateLimiter()
    return _login_limiter
