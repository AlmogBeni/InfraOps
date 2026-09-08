"""Add prerequisite-aware VM deployment lifecycle state.

Revision ID: 0006_vm_deployment_lifecycle
Revises: 0005_encrypted_credentials
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_vm_deployment_lifecycle"
down_revision = "0005_encrypted_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'ACTION_REQUIRED'")
    op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'WARNING'")
    op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'WAITING_FOR_PREREQUISITE'")
    op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'NOT_APPLICABLE'")
    op.add_column(
        "provisioning_jobs",
        sa.Column("infrastructure_status", sa.String(40), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "provisioning_jobs",
        sa.Column("guest_os_status", sa.String(40), nullable=False, server_default="UNKNOWN"),
    )
    op.add_column(
        "provisioning_jobs",
        sa.Column("vmware_tools_status", sa.String(40), nullable=False, server_default="UNKNOWN"),
    )
    op.add_column(
        "provisioning_jobs",
        sa.Column("guest_provisioning_status", sa.String(40), nullable=False, server_default="PENDING"),
    )
    op.add_column("provisioning_jobs", sa.Column("action_required", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("provisioning_jobs", "action_required")
    op.drop_column("provisioning_jobs", "guest_provisioning_status")
    op.drop_column("provisioning_jobs", "vmware_tools_status")
    op.drop_column("provisioning_jobs", "guest_os_status")
    op.drop_column("provisioning_jobs", "infrastructure_status")
    # PostgreSQL enum values are intentionally retained: removing enum values
    # requires rewriting dependent columns and is unsafe during a rollback.
