"""Every VM is installed unattended from a Windows ISO; no job waits for a person.

* The OVF/OVA (Content Library) source is gone. Jobs that used it, and blank
  VMs created without installation media, can no longer run: active ones are
  closed with an explanation and stay readable.
* ACTION_REQUIRED / WAITING_FOR_PREREQUISITE are removed. Paused jobs become
  FAILED (or PARTIALLY_COMPLETED once their VM exists) so the stage can be
  retried, and the job_status / step_status types are rebuilt without them.
* Stage ``clone_vm`` is now ``create_vm``; data disks get their own stages.
* Timeout setting ``clone_minutes`` is now ``create_vm_minutes``.

The downgrade restores the schema; jobs closed by the upgrade stay closed.

Revision ID: 0009_iso_only_unattended
Revises: 0008_user_notifications
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_iso_only_unattended"
down_revision = "0008_user_notifications"
branch_labels = None
depends_on = None

JOB_STATUS = ("QUEUED", "RUNNING", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED")
STEP_STATUS = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "WARNING", "NOT_APPLICABLE", "CANCELLED")

_ACTIVE = "status IN ('QUEUED', 'RUNNING', 'INTERRUPTED')"
_PREVIOUS_IP_RESERVING = "status IN ('QUEUED', 'RUNNING', 'INTERRUPTED', 'ACTION_REQUIRED')"

STAGES = (
    "validate_request", "connect_vcenter", "validate_infrastructure", "create_vm",
    "configure_hardware", "attach_network_adapter", "prepare_unattended_install", "power_on",
    "wait_for_guest_os", "wait_for_tools", "cleanup_unattended_media", "add_data_disks",
    "initialize_data_disks", "configure_guest_network", "validate_network", "configure_hostname",
    "join_domain", "reboot_guest", "wait_guest_ready", "install_root_certificates",
    "install_intermediate_certificates", "validate_certificates", "resolve_dependencies",
    "install_applications", "validate_applications", "final_validation",
)
PREVIOUS_STAGES = tuple(
    "clone_vm" if key == "create_vm" else key
    for key in STAGES
    if key not in ("add_data_disks", "initialize_data_disks")
)
# (stage, current name, previous name)
RENAMED_STEPS = (
    ("power_on", "Power on and start Windows Setup", "Power on VM"),
    ("wait_for_guest_os", "Install Windows", "Wait for guest operating system"),
    ("cleanup_unattended_media", "Remove installation media", "Remove temporary unattended media"),
)

REMOVED_WORKFLOW = (
    "This job was created by a provisioning workflow that no longer exists, so it was closed "
    "during the upgrade. Submit a new request to create the VM."
)
MANUAL_STEP_REMOVED = (
    "This job was waiting for a manual step that no longer exists. Retry the failed stage to "
    "continue unattended."
)

# Jobs whose stored request has no installation ISO cannot run any more.
_REMOVED_WORKFLOW_JOBS = """
    SELECT r.job_id FROM vm_provisioning_requests AS r
    WHERE coalesce(r.payload -> 'guest' ->> 'iso_id', '') = ''
"""


def _renumber(stages: tuple[str, ...]) -> None:
    values = ", ".join(f"('{key}', {index})" for index, key in enumerate(stages))
    op.execute(
        f"""
        UPDATE provisioning_job_steps AS step SET sequence = ordered.position
        FROM (VALUES {values}) AS ordered(stage_key, position)
        WHERE step.stage_key = ordered.stage_key
        """
    )


def _rebuild_enum(name: str, table: str, values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{value}'" for value in values)
    op.execute(f"ALTER TYPE {name} RENAME TO {name}_previous")
    op.execute(f"CREATE TYPE {name} AS ENUM ({labels})")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN status TYPE {name} USING status::text::{name}")
    op.execute(f"DROP TYPE {name}_previous")


def _rename_timeout(old: str, new: str) -> None:
    # Settings rows wrap their payload as {"value": {...}}.
    op.execute(
        f"""
        UPDATE platform_settings
        SET value = jsonb_set(
            value, '{{value}}',
            ((value -> 'value') - '{old}') || jsonb_build_object('{new}', (value -> 'value') -> '{old}')
        )
        WHERE key = 'default_timeouts' AND jsonb_typeof(value -> 'value') = 'object'
          AND (value -> 'value') ? '{old}'
        """
    )


def upgrade() -> None:
    op.drop_index("uq_provisioning_jobs_reserved_ipv4", table_name="provisioning_jobs")
    op.drop_index("uq_provisioning_jobs_active_vm_name", table_name="provisioning_jobs")

    op.execute("UPDATE provisioning_job_steps SET stage_key = 'create_vm' WHERE stage_key = 'clone_vm'")
    op.execute("UPDATE provisioning_jobs SET current_stage = 'create_vm' WHERE current_stage = 'clone_vm'")

    # Close jobs of the removed workflows that could still run.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE provisioning_job_steps
            SET status = 'FAILED', finished_at = now(), error_human = :message
            WHERE status IN ('RUNNING', 'WAITING_FOR_PREREQUISITE')
              AND job_id IN (
                  SELECT id FROM provisioning_jobs
                  WHERE status IN ('QUEUED', 'RUNNING', 'INTERRUPTED', 'ACTION_REQUIRED')
              )
              AND job_id IN ({_REMOVED_WORKFLOW_JOBS})
            """
        ),
        {"message": REMOVED_WORKFLOW},
    )
    # A job whose VM exists ends PARTIALLY_COMPLETED, as the pipeline does.
    _vm_created = """
        EXISTS (
            SELECT 1 FROM provisioning_job_steps AS step
            WHERE step.job_id = provisioning_jobs.id AND step.stage_key = 'create_vm'
              AND step.status IN ('SUCCEEDED', 'SKIPPED')
        )
    """
    bind.execute(
        sa.text(
            f"""
            UPDATE provisioning_jobs
            SET status = CASE WHEN {_vm_created} THEN 'PARTIALLY_COMPLETED' ELSE 'FAILED' END::job_status,
                finished_at = coalesce(finished_at, now()),
                error_summary = :message,
                error_detail = coalesce(action_required, error_detail)
            WHERE status IN ('QUEUED', 'RUNNING', 'INTERRUPTED', 'ACTION_REQUIRED')
              AND id IN ({_REMOVED_WORKFLOW_JOBS})
            """
        ),
        {"message": REMOVED_WORKFLOW},
    )

    # Paused jobs: the waiting stage fails so it can be retried unattended.
    bind.execute(
        sa.text(
            """
            UPDATE provisioning_job_steps
            SET status = 'FAILED', finished_at = now(), error_human = :message
            WHERE status = 'WAITING_FOR_PREREQUISITE'
              AND job_id IN (SELECT id FROM provisioning_jobs WHERE status = 'ACTION_REQUIRED')
            """
        ),
        {"message": MANUAL_STEP_REMOVED},
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE provisioning_jobs
            SET status = CASE WHEN {_vm_created} THEN 'PARTIALLY_COMPLETED' ELSE 'FAILED' END::job_status,
                finished_at = coalesce(finished_at, now()),
                error_summary = :message,
                error_detail = coalesce(action_required, error_detail)
            WHERE status = 'ACTION_REQUIRED'
            """
        ),
        {"message": MANUAL_STEP_REMOVED},
    )
    # Anything still waiting belongs to a finished job.
    bind.execute(
        sa.text(
            """
            UPDATE provisioning_job_steps AS step
            SET status = CASE WHEN job.status = 'CANCELLED' THEN 'CANCELLED' ELSE 'FAILED' END::step_status,
                finished_at = coalesce(step.finished_at, now()),
                error_human = coalesce(step.error_human, :message)
            FROM provisioning_jobs AS job
            WHERE job.id = step.job_id AND step.status = 'WAITING_FOR_PREREQUISITE'
            """
        ),
        {"message": MANUAL_STEP_REMOVED},
    )
    op.execute("UPDATE notifications SET kind = 'JOB_FAILED' WHERE kind = 'JOB_ACTION_REQUIRED'")

    _renumber(STAGES)
    for stage_key, name, _previous in RENAMED_STEPS:
        bind.execute(
            sa.text(
                f"""
                UPDATE provisioning_job_steps SET name = :name
                WHERE stage_key = :stage_key AND job_id NOT IN ({_REMOVED_WORKFLOW_JOBS})
                """
            ),
            {"name": name, "stage_key": stage_key},
        )
    _rename_timeout("clone_minutes", "create_vm_minutes")

    op.drop_column("provisioning_jobs", "action_required")
    _rebuild_enum("job_status", "provisioning_jobs", JOB_STATUS)
    _rebuild_enum("step_status", "provisioning_job_steps", STEP_STATUS)

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
        postgresql_where=sa.text(f"reserved_ipv4 IS NOT NULL AND {_ACTIVE}"),
    )


def downgrade() -> None:
    # A new enum value cannot be referenced in the transaction that adds it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'ACTION_REQUIRED'")
        op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'WAITING_FOR_PREREQUISITE'")

    op.add_column("provisioning_jobs", sa.Column("action_required", sa.Text(), nullable=True))
    op.drop_index("uq_provisioning_jobs_reserved_ipv4", table_name="provisioning_jobs")
    op.create_index(
        "uq_provisioning_jobs_reserved_ipv4",
        "provisioning_jobs",
        ["reserved_ipv4"],
        unique=True,
        postgresql_where=sa.text(f"reserved_ipv4 IS NOT NULL AND {_PREVIOUS_IP_RESERVING}"),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM provisioning_job_steps
            WHERE stage_key IN ('add_data_disks', 'initialize_data_disks')
            """
        )
    )
    op.execute(
        """
        UPDATE provisioning_jobs SET current_stage = 'cleanup_unattended_media'
        WHERE current_stage IN ('add_data_disks', 'initialize_data_disks')
        """
    )
    op.execute("UPDATE provisioning_job_steps SET stage_key = 'clone_vm' WHERE stage_key = 'create_vm'")
    op.execute("UPDATE provisioning_jobs SET current_stage = 'clone_vm' WHERE current_stage = 'create_vm'")
    _renumber(PREVIOUS_STAGES)
    for stage_key, _name, previous in RENAMED_STEPS:
        bind.execute(
            sa.text("UPDATE provisioning_job_steps SET name = :name WHERE stage_key = :stage_key"),
            {"name": previous, "stage_key": stage_key},
        )
    _rename_timeout("create_vm_minutes", "clone_minutes")
    op.execute(
        """
        UPDATE platform_settings SET value = jsonb_set(value, '{value}', (value -> 'value') - 'os_installation_minutes')
        WHERE key = 'default_timeouts' AND jsonb_typeof(value -> 'value') = 'object'
        """
    )
