"""Remove the former development/demo records.

Revision ID: 0002_remove_dev_seed_data
Revises: 0001_initial
Create Date: 2026-08-24
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

revision = "0002_remove_dev_seed_data"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

MOCK_VCENTER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")

DEMO_INSTALLER_PATHS = (
    r"\\software.company.local\packages\runtime\vc_redist.x64.exe",
    r"\\software.company.local\packages\monitoring\agent.msi",
    r"\\software.company.local\packages\backup\agent.msi",
    r"\\software.company.local\packages\edr\agent-setup.exe",
    r"\\software.company.local\packages\tools\7z2408-x64.msi",
)


def upgrade() -> None:
    connection = op.get_bind()

    applications = sa.table(
        "applications",
        sa.column("installer_path", sa.String),
    )
    connection.execute(
        sa.delete(applications).where(applications.c.installer_path.in_(DEMO_INSTALLER_PATHS))
    )

    packages = sa.table(
        "certificate_packages",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
    )
    connection.execute(
        sa.delete(packages).where(
            packages.c.name == "Corporate Standard Certificates",
            packages.c.description == "Development trust chain deployed to every provisioned server.",
        )
    )

    vcenters = sa.table("vcenters", sa.column("id", sa.Uuid))
    connection.execute(sa.delete(vcenters).where(vcenters.c.id == MOCK_VCENTER_ID))

    secret_references = sa.table(
        "secret_references",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
    )
    seeded_references = (
        ("vcsa-prod", "Mock vCenter service account (SECRETS_VCSA_PROD_* env vars)."),
        ("guest-local-admin", "Local Windows administrator used for guest automation."),
        ("domain-join", "Domain account used for automatic domain joins."),
    )
    for name, description in seeded_references:
        connection.execute(
            sa.delete(secret_references).where(
                secret_references.c.name == name,
                secret_references.c.description == description,
            )
        )

    users = sa.table(
        "users",
        sa.column("username", sa.String),
        sa.column("email", sa.String),
    )
    connection.execute(
        sa.delete(users).where(
            sa.or_(
                sa.and_(users.c.username == "admin", users.c.email == "admin@example.internal"),
                sa.and_(users.c.username == "operator", users.c.email == "operator@example.internal"),
                sa.and_(users.c.username == "viewer", users.c.email == "viewer@example.internal"),
            )
        )
    )

    platform_settings = sa.table("platform_settings", sa.column("key", sa.String))
    connection.execute(
        sa.delete(platform_settings).where(platform_settings.c.key == "ipam_enabled")
    )


def downgrade() -> None:
    # Removed demo credentials/certificates must never be recreated by a downgrade.
    pass
