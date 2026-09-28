"""Worker liveness, race-free reservations, refresh-token registry and a
TRUNCATE-proof audit trail.

Revision ID: 0007_job_liveness_and_hardening
Revises: 0006_vm_deployment_lifecycle
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_job_liveness_and_hardening"
down_revision = "0006_vm_deployment_lifecycle"
branch_labels = None
depends_on = None

_ACTIVE = "status IN ('QUEUED', 'RUNNING', 'INTERRUPTED')"
_IP_RESERVING = "status IN ('QUEUED', 'RUNNING', 'INTERRUPTED', 'ACTION_REQUIRED')"


def upgrade() -> None:
    # A new enum value cannot be referenced in the transaction that adds it,
    # and the partial indexes below reference INTERRUPTED.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'INTERRUPTED'")

    op.add_column("provisioning_jobs", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.add_column("provisioning_jobs", sa.Column("reserved_ipv4", sa.String(45), nullable=True))
    op.add_column("provisioning_jobs", sa.Column("worker_id", sa.String(120), nullable=True))
    op.add_column(
        "provisioning_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Older releases could admit two concurrent active jobs for one VM name.
    # Keep the oldest and fail the rest so the uniqueness guarantee can hold.
    op.execute(
        f"""
        UPDATE provisioning_jobs
        SET status = 'FAILED',
            finished_at = now(),
            error_summary = 'Duplicate active job for the same VM name (closed during upgrade).'
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY lower(vm_name) ORDER BY queued_at, id
                ) AS position
                FROM provisioning_jobs
                WHERE {_ACTIVE}
            ) ranked
            WHERE ranked.position > 1
        )
        """
    )
    # Backfill reservations for jobs that can still configure a static address
    # (oldest job wins if two already share one).
    op.execute(
        f"""
        UPDATE provisioning_jobs AS job
        SET reserved_ipv4 = candidates.address
        FROM (
            SELECT DISTINCT ON (address) job_id, address
            FROM (
                SELECT j.id AS job_id,
                       r.payload -> 'network' -> 'ipv4' ->> 'address' AS address,
                       j.queued_at
                FROM provisioning_jobs AS j
                JOIN vm_provisioning_requests AS r ON r.job_id = j.id
                WHERE r.payload -> 'network' ->> 'mode' = 'STATIC'
                  AND j.{_IP_RESERVING}
            ) AS static_jobs
            WHERE address IS NOT NULL
            ORDER BY address, queued_at
        ) AS candidates
        WHERE candidates.job_id = job.id
        """
    )
    # RUNNING rows are left untouched: they have no heartbeat yet, so the
    # worker's reaper marks them INTERRUPTED once they are demonstrably stale.

    op.create_index(
        "uq_provisioning_jobs_active_vm_name",
        "provisioning_jobs",
        [sa.text("lower(vm_name)")],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )
    op.create_index(
        "uq_provisioning_jobs_reserved_ipv4",
        "provisioning_jobs",
        ["reserved_ipv4"],
        unique=True,
        postgresql_where=sa.text(f"reserved_ipv4 IS NOT NULL AND {_IP_RESERVING}"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Row triggers do not fire for TRUNCATE; add a statement-level guard.
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION infraops_forbid_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.drop_table("refresh_tokens")
    op.drop_index("uq_provisioning_jobs_reserved_ipv4", table_name="provisioning_jobs")
    op.drop_index("uq_provisioning_jobs_active_vm_name", table_name="provisioning_jobs")
    op.execute("UPDATE provisioning_jobs SET status = 'FAILED' WHERE status = 'INTERRUPTED'")
    op.drop_column("provisioning_jobs", "heartbeat_at")
    op.drop_column("provisioning_jobs", "worker_id")
    op.drop_column("provisioning_jobs", "reserved_ipv4")
    op.drop_column("provisioning_jobs", "request_fingerprint")
    # PostgreSQL enum values are retained (see 0006).
