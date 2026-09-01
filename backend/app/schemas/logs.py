"""Structured operational log responses derived from provisioning steps."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

LogSeverity = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEventOut(BaseModel):
    id: str
    timestamp: dt.datetime
    severity: LogSeverity
    component: str
    message: str
    resource_name: str | None = None
    datacenter_name: str | None = None
    job_id: str
    details: dict = Field(default_factory=dict)


class LogListResponse(BaseModel):
    items: list[LogEventOut]
    total: int
    page: int
    page_size: int
