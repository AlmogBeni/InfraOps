"""Shared schema primitives."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard paginated list envelope."""

    items: list[T]
    total: int
    page: int = 1
    page_size: int = 50


class HealthComponent(BaseModel):
    component: str
    status: str  # healthy | degraded | unavailable
    detail: str = ""


class HealthReport(BaseModel):
    status: str
    version: str
    environment: str
    infrastructure_mode: str


class ReadinessReport(BaseModel):
    status: str
    checks: list[HealthComponent]
