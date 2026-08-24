"""Initial InfraOps schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


JOB_TYPE = ("vm_provisioning",)
JOB_STATUS = ("QUEUED", "RUNNING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED")
STEP_STATUS = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "CANCELLED")
CERT_TYPE = ("ROOT", "INTERMEDIATE")
CERT_STORE = ("Root", "CA")
INSTALLER_TYPE = ("MSI", "EXE", "POWERSHELL")
DETECTION_METHOD = (
    "MSI_PRODUCT_CODE", "REGISTRY_KEY", "FILE_EXISTS", "SERVICE_EXISTS", "SCRIPT",
)


def _enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def _create_enums() -> None:
    for name, values in (
        ("job_type", JOB_TYPE),
        ("job_status", JOB_STATUS),
        ("step_status", STEP_STATUS),
        ("certificate_type", CERT_TYPE),
        ("certificate_store", CERT_STORE),
        ("installer_type", INSTALLER_TYPE),
        ("detection_method", DETECTION_METHOD),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)


def upgrade() -> None:
    _create_enums()

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=50), nullable=False, unique=True),
        sa.Column("description", sa.String(length=500), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(length=100), nullable=False, unique=True, index=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(),
                  sa.ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )

    op.create_table(
        "vcenters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False, unique=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username_secret_ref", sa.String(length=200), nullable=False),
        sa.Column("password_secret_ref", sa.String(length=200), nullable=False),
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_connection_state", sa.String(length=20), nullable=True),
        sa.Column("last_connection_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.alter_column("vcenters", "port", server_default="443")

    op.create_table(
        "sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("vcenter_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vcenters.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("datacenter_moref", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("vcenter_id", "name", name="uq_sites_vcenter_name"),
    )

    op.create_table(
        "certificate_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "certificates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("certificate_packages.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("friendly_name", sa.String(length=200), nullable=False),
        sa.Column("certificate_type", _enum("certificate_type", CERT_TYPE), nullable=False),
        sa.Column("destination_store", _enum("certificate_store", CERT_STORE), nullable=False),
        sa.Column("subject_cn", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("fingerprint_sha256", sa.String(length=64), nullable=False, index=True),
        sa.Column("not_before", sa.Date(), nullable=True),
        sa.Column("not_after", sa.Date(), nullable=True),
        sa.Column("pem_body", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("installer_type", _enum("installer_type", INSTALLER_TYPE), nullable=False),
        sa.Column("installer_path", sa.String(length=500), nullable=False),
        sa.Column("install_arguments", sa.Text(), nullable=False, server_default=""),
        sa.Column("detection_method", _enum("detection_method", DETECTION_METHOD), nullable=False),
        sa.Column("detection_config", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("reboot_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "application_dependencies",
        sa.Column("app_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("depends_on_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("app_id", "depends_on_id", name="uq_application_dependency"),
        sa.CheckConstraint("app_id <> depends_on_id", name="ck_no_self_dependency"),
    )

    op.create_table(
        "provisioning_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", _enum("job_type", JOB_TYPE), nullable=False),
        sa.Column("status", _enum("job_status", JOB_STATUS), nullable=False, index=True),
        sa.Column("vm_name", sa.String(length=100), nullable=False, index=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("current_stage", sa.String(length=80), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "provisioning_job_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("provisioning_jobs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("stage_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", _enum("step_status", STEP_STATUS), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error_human", sa.Text(), nullable=True),
        sa.Column("error_technical", sa.Text(), nullable=True),
        sa.Column("artifacts", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "stage_key", name="uq_job_step_stage"),
    )

    op.create_table(
        "vm_provisioning_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("provisioning_jobs.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False, index=True),
        sa.Column("resource_type", sa.String(length=60), nullable=True),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("result", sa.String(length=30), nullable=True),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("detail_text", sa.Text(), nullable=True),
    )

    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "secret_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False, unique=True),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="env"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Tamper resistance: reject any UPDATE/DELETE against the audit trail.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION infraops_forbid_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION infraops_forbid_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS infraops_forbid_audit_mutation()")
    for table in (
        "secret_references", "platform_settings", "audit_events",
        "vm_provisioning_requests", "provisioning_job_steps", "provisioning_jobs",
        "application_dependencies", "applications", "certificates",
        "certificate_packages", "sites", "vcenters", "user_roles", "users", "roles",
    ):
        op.drop_table(table)
    for name, _values in (
        ("job_type", JOB_TYPE),
        ("job_status", JOB_STATUS),
        ("step_status", STEP_STATUS),
        ("certificate_type", CERT_TYPE),
        ("certificate_store", CERT_STORE),
        ("installer_type", INSTALLER_TYPE),
        ("detection_method", DETECTION_METHOD),
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
