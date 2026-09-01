"""Remove the obsolete logical sites table.

Revision ID: 0003_remove_sites
Revises: 0002_remove_dev_seed_data
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_remove_sites"
down_revision = "0002_remove_dev_seed_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some early deployments recorded the initial revision without creating
    # this unused table. IF EXISTS keeps those databases upgradeable.
    op.execute("DROP TABLE IF EXISTS sites")


def downgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "vcenter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vcenters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("datacenter_moref", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("vcenter_id", "name", name="uq_sites_vcenter_name"),
    )
    op.create_index("ix_sites_vcenter_id", "sites", ["vcenter_id"])
