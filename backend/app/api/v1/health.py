"""Health, readiness and metrics endpoints (never expose secrets)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.metrics import render_metrics
from app.db.session import engine
from app.secrets.service import get_secrets_service
from app.schemas.common import HealthComponent, HealthReport, ReadinessReport

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport)
async def health() -> HealthReport:
    settings = get_settings()
    return HealthReport(
        status="ok",
        version=__version__,
        environment=settings.environment.value,
        infrastructure_mode=settings.infrastructure_mode.value,
    )


@router.get("/health/ready", response_model=ReadinessReport)
async def readiness() -> ReadinessReport:
    checks: list[HealthComponent] = []

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks.append(HealthComponent(component="database", status="healthy"))
    except Exception as exc:  # noqa: BLE001
        checks.append(HealthComponent(component="database", status="unavailable",
                                      detail=str(exc)[:200]))

    settings = get_settings()
    try:
        client = redis_from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        await client.aclose()
        checks.append(HealthComponent(component="redis", status="healthy"))
    except Exception as exc:  # noqa: BLE001
        checks.append(HealthComponent(component="redis", status="unavailable",
                                      detail=str(exc)[:200]))

    secrets_ok = await get_secrets_service().healthcheck()
    checks.append(
        HealthComponent(
            component=f"secrets ({get_secrets_service().provider_name})",
            status="healthy" if secrets_ok else "degraded",
        )
    )

    overall = "ready" if all(c.status != "unavailable" for c in checks) else "not-ready"
    return ReadinessReport(status=overall, checks=checks)


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")
