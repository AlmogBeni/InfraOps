"""Store UI-managed credential pairs using authenticated encryption.

Revision ID: 0005_encrypted_credentials
Revises: 0004_add_audit_datacenter
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_encrypted_credentials"
down_revision = "0004_add_audit_datacenter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "secret_references",
        sa.Column("purpose", sa.String(length=40), nullable=False, server_default="generic"),
    )
    op.add_column("secret_references", sa.Column("encrypted_username", sa.Text()))
    op.add_column("secret_references", sa.Column("encrypted_password", sa.Text()))
    op.add_column(
        "secret_references",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("secret_references", "provider", server_default="database")


def downgrade() -> None:
    op.alter_column("secret_references", "provider", server_default="env")
    op.drop_column("secret_references", "revision")
    op.drop_column("secret_references", "encrypted_password")
    op.drop_column("secret_references", "encrypted_username")
    op.drop_column("secret_references", "purpose")
