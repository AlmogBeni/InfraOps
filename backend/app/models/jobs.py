"""Automation jobs, their step timeline and stored provisioning requests.

The job tables are deliberately generic (``job_type`` discriminator) so future
automation modules (decommissioning, snapshots, disk expansion …) reuse the
same execution, history and retry machinery.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SaEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, updated_at_column, uuid_primary_key


class JobType(str, enum.Enum):
    VM_PROVISIONING = "vm_provisioning"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.PARTIALLY_COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class ProvisioningJob(Base):
    __tablename__ = "provisioning_jobs"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    job_type: Mapped[JobType] = mapped_column(
        SaEnum(JobType, name="job_type"), nullable=False, default=JobType.VM_PROVISIONING
    )
    status: Mapped[JobStatus] = mapped_column(
        SaEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.QUEUED, index=True
    )
    vm_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    current_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    queued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()

    steps: Mapped[list["ProvisioningJobStep"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ProvisioningJobStep.sequence",
        lazy="selectin",
    )
    request: Mapped["VmProvisioningRequest | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )


class ProvisioningJobStep(Base):
    """One stage of the provisioning pipeline with its full execution record."""

    __tablename__ = "provisioning_job_steps"
    __table_args__ = (UniqueConstraint("job_id", "stage_key", name="uq_job_step_stage"),)

    id: Mapped[uuid.UUID] = uuid_primary_key()
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("provisioning_jobs.id", ondelete="CASCADE"), index=True
    )
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        SaEnum(StepStatus, name="step_status"), nullable=False, default=StepStatus.PENDING
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_human: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_technical: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = created_at_column()

    job: Mapped[ProvisioningJob] = relationship(back_populates="steps")


class VmProvisioningRequest(Base):
    """Immutable snapshot of the exact request that produced a job."""

    __tablename__ = "vm_provisioning_requests"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("provisioning_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()

    job: Mapped[ProvisioningJob] = relationship(back_populates="request")
