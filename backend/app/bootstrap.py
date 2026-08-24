"""Create the minimum reference data required to operate InfraOps.

This bootstrap intentionally creates no infrastructure, certificates,
applications, credential references, or example users. It is safe to run on
every API start and creates a local administrator only when none exists.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.session import session_factory
from app.models.rbac import Role, UserRole
from app.models.user import User

log = get_logger(__name__)

ROLE_DEFINITIONS = (
    ("viewer", "Read-only access to jobs, history and audit logs."),
    ("operator", "Can provision VMs and manage guest configuration."),
    ("administrator", "Full administrative access to the platform."),
)


async def bootstrap() -> None:
    settings = get_settings()
    async with session_factory() as db:
        roles: dict[str, Role] = {}
        for role_name, description in ROLE_DEFINITIONS:
            role = await db.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                role = Role(name=role_name, description=description)
                db.add(role)
                await db.flush()
            roles[role_name] = role

        administrator_exists = await db.scalar(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(Role.name == "administrator", User.is_active.is_(True))
            .limit(1)
        )
        if administrator_exists is not None:
            await db.commit()
            log.info("Required roles verified; an active administrator already exists.")
            return

        username = settings.bootstrap_admin_username.strip()
        email = settings.bootstrap_admin_email.strip() or None
        password = settings.bootstrap_admin_password
        if not username or not password:
            raise RuntimeError(
                "No active administrator exists. Set BOOTSTRAP_ADMIN_USERNAME and "
                "BOOTSTRAP_ADMIN_PASSWORD for the first start."
            )
        if len(password) < 16 or password == "ChangeMe_DevOnly!123":
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_PASSWORD must contain at least 16 characters and "
                "must not use the former development password."
            )

        existing_user = await db.scalar(select(User).where(User.username == username))
        if existing_user is not None:
            raise RuntimeError(
                f"Bootstrap username '{username}' already exists without the administrator role. "
                "Choose another username or assign the role directly in PostgreSQL."
            )

        administrator = User(
            username=username,
            email=email,
            full_name="Platform Administrator",
            password_hash=hash_password(password),
        )
        db.add(administrator)
        await db.flush()
        db.add(UserRole(user_id=administrator.id, role_id=roles["administrator"].id))
        await db.commit()
        log.warning(
            "Created bootstrap administrator '%s'. Remove BOOTSTRAP_ADMIN_PASSWORD "
            "from the runtime environment before restarting.",
            username,
        )


if __name__ == "__main__":
    configure_logging(get_settings().log_level, "console")
    asyncio.run(bootstrap())
