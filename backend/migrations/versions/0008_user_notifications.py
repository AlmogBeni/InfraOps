"""In-app notifications for job outcomes.

Revision ID: 0008_user_notifications
Revises: 0007_job_liveness_and_hardening
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_user_notifications"
down_revision = "0007_job_liveness_and_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("provisioning_jobs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notifications_user_id_created_at", "notifications", ["user_id", "created_at"]
    )
    op.create_index("ix_notifications_job_id", "notifications", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_job_id", table_name="notifications")
    op.drop_index("ix_notifications_user_id_created_at", table_name="notifications")
    op.drop_table("notifications")
