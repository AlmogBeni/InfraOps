"""Dashboard aggregation schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import HealthComponent
from app.schemas.jobs import JobOut


class DashboardStats(BaseModel):
    vms_provisioned_this_month: int
    success_rate_percent: float | None = None
    average_duration_seconds: float | None = None
    failed_jobs: int
    active_jobs: int


class DashboardResponse(BaseModel):
    stats: DashboardStats
    recent_jobs: list[JobOut]
    health: list[HealthComponent]
