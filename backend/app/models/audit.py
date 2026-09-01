"""Tamper-resistant audit trail.

Rows are append-only: the initial migration installs a PostgreSQL trigger that
rejects UPDATE/DELETE on this table at the database level. Secrets must never
be written here — :mod:`app.audit.recorder` redacts defensively before insert.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_primary_key


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    resource_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    datacenter_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    datacenter_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    result: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    detail_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_log_record(self) -> dict:
        """Structured representation matching the documented audit contract."""
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user": self.username,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "job_id": str(self.job_id) if self.job_id else None,
            "datacenter_id": self.datacenter_id,
            "datacenter_name": self.datacenter_name,
            "result": self.result,
            "source_ip": self.source_ip,
            "details": self.details,
        }
