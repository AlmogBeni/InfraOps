"""Provision the least-privilege runtime database role.

Run after ``alembic upgrade head`` with ``MIGRATION_DATABASE_URL`` pointing at
the schema *owner*. The API and worker connect with ``DATABASE_URL`` as a
separate runtime role that owns nothing, so it cannot drop the audit trigger,
alter tables, or TRUNCATE; on ``audit_events`` it may only INSERT and SELECT.

``python -m app.db.roles`` is idempotent. It creates the runtime role from the
credentials embedded in ``DATABASE_URL`` (or updates its password) and
re-applies grants for every table, including ones added by new migrations.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)

APPEND_ONLY_TABLES = ("audit_events",)
READ_ONLY_TABLES = ("alembic_version",)


async def _quoted(conn, template: str, *values: str) -> str:
    """Render DDL with server-side identifier/literal quoting (format %I/%L)."""
    params = {f"p{index}": value for index, value in enumerate(values)}
    placeholders = ", ".join(f"CAST(:p{index} AS text)" for index in range(len(values)))
    result = await conn.execute(
        text(f"SELECT format(CAST(:template AS text), {placeholders})"), {"template": template, **params}
    )
    return str(result.scalar_one())


async def provision_runtime_role() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    if not settings.migration_database_url:
        log.warning(
            "MIGRATION_DATABASE_URL is not set: the application connects as the schema owner. "
            "Configure a separate runtime role to make the audit trail tamper-resistant."
        )
        return 0

    owner_url = make_url(settings.migration_database_url)
    runtime_url = make_url(settings.database_url)
    runtime_role = runtime_url.username or ""
    if not runtime_role or runtime_role == owner_url.username:
        log.warning(
            "DATABASE_URL uses the schema owner '%s'; runtime role separation is disabled.",
            owner_url.username,
        )
        return 0
    if not runtime_url.password:
        log.error("DATABASE_URL must include the runtime role password.")
        return 1

    engine = create_async_engine(settings.migration_database_url)
    try:
        async with engine.begin() as conn:
            exists = (
                await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": runtime_role})
            ).scalar_one_or_none()
            verb = "ALTER" if exists else "CREATE"
            await conn.execute(
                text(
                    await _quoted(
                        conn,
                        f"{verb} ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L",
                        runtime_role,
                        str(runtime_url.password),
                    )
                )
            )
            database = owner_url.database or "postgres"
            statements = [
                ("GRANT CONNECT ON DATABASE %I TO %I", (database, runtime_role)),
                ("GRANT USAGE ON SCHEMA public TO %I", (runtime_role,)),
                ("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I", (runtime_role,)),
                (
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I",
                    (runtime_role,),
                ),
                ("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I", (runtime_role,)),
            ]
            for table in APPEND_ONLY_TABLES:
                statements.append(("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE %I FROM %I", (table, runtime_role)))
            for table in READ_ONLY_TABLES:
                statements.append(("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE %I FROM %I", (table, runtime_role)))
            for template, values in statements:
                await conn.execute(text(await _quoted(conn, template, *values)))
            owned = (
                await conn.execute(
                    text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = :name"),
                    {"name": runtime_role},
                )
            ).scalar_one()
            if owned:
                log.error(
                    "Runtime role '%s' owns %d table(s); ownership bypasses these grants. "
                    "Reassign ownership to the migration role (REASSIGN OWNED BY).",
                    runtime_role,
                    owned,
                )
                return 1
        log.info("Runtime database role '%s' provisioned with least-privilege grants.", runtime_role)
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(provision_runtime_role()))
