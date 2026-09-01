"""Add durable datacenter context to jobs and audit events.

Revision ID: 0004_add_audit_datacenter
Revises: 0003_remove_sites
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_add_audit_datacenter"
down_revision = "0003_remove_sites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provisioning_jobs", sa.Column("datacenter_id", sa.String(length=120)))
    op.add_column("provisioning_jobs", sa.Column("datacenter_name", sa.String(length=255)))
    op.create_index("ix_provisioning_jobs_datacenter_id", "provisioning_jobs", ["datacenter_id"])
    op.create_index(
        "ix_provisioning_jobs_datacenter_name", "provisioning_jobs", ["datacenter_name"]
    )
    op.execute(
        """
        UPDATE provisioning_jobs AS jobs
        SET datacenter_id = requests.payload #>> '{compute,datacenter_id}'
        FROM vm_provisioning_requests AS requests
        WHERE requests.job_id = jobs.id
          AND jobs.datacenter_id IS NULL
        """
    )
    op.add_column("audit_events", sa.Column("datacenter_id", sa.String(length=120)))
    op.add_column("audit_events", sa.Column("datacenter_name", sa.String(length=255)))
    op.create_index("ix_audit_events_datacenter_id", "audit_events", ["datacenter_id"])
    op.create_index("ix_audit_events_datacenter_name", "audit_events", ["datacenter_name"])
    op.create_index("ix_audit_events_resource_type", "audit_events", ["resource_type"])
    op.create_index("ix_audit_events_result", "audit_events", ["result"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_result", table_name="audit_events")
    op.drop_index("ix_audit_events_resource_type", table_name="audit_events")
    op.drop_index("ix_audit_events_datacenter_id", table_name="audit_events")
    op.drop_index("ix_audit_events_datacenter_name", table_name="audit_events")
    op.drop_column("audit_events", "datacenter_name")
    op.drop_column("audit_events", "datacenter_id")
    op.drop_index("ix_provisioning_jobs_datacenter_name", table_name="provisioning_jobs")
    op.drop_index("ix_provisioning_jobs_datacenter_id", table_name="provisioning_jobs")
    op.drop_column("provisioning_jobs", "datacenter_name")
    op.drop_column("provisioning_jobs", "datacenter_id")
