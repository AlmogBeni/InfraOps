"""RBAC permission matrix — the single source of truth for authorization.

Roles: viewer < operator < administrator. Backend route handlers depend on
these permissions; the frontend mirrors them purely for UX.
"""

from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    INFRASTRUCTURE_READ = "infrastructure.read"
    PROVISIONING_VALIDATE = "provisioning.validate"
    PROVISIONING_SUBMIT = "provisioning.submit"
    JOBS_READ = "jobs.read"
    JOBS_RETRY = "jobs.retry"
    JOBS_CANCEL = "jobs.cancel"
    AUDIT_READ = "audit.read"
    ADMIN_VCENTERS = "admin.vcenters"
    ADMIN_CERTIFICATES = "admin.certificates"
    ADMIN_APPLICATIONS = "admin.applications"
    ADMIN_CREDENTIALS = "admin.credentials"
    ADMIN_SETTINGS = "admin.settings"


ROLE_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.INFRASTRUCTURE_READ,
        Permission.JOBS_READ,
        Permission.AUDIT_READ,
    }
)

ROLE_OPERATOR: frozenset[Permission] = ROLE_VIEWER | {
    Permission.PROVISIONING_VALIDATE,
    Permission.PROVISIONING_SUBMIT,
    Permission.JOBS_RETRY,
    Permission.JOBS_CANCEL,
}

ROLE_ADMINISTRATOR: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": ROLE_VIEWER,
    "operator": ROLE_OPERATOR,
    "administrator": ROLE_ADMINISTRATOR,
}

ALL_ROLES: tuple[str, ...] = ("viewer", "operator", "administrator")


def permissions_for_roles(roles: list[str]) -> list[str]:
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return sorted(permission.value for permission in granted)


def roles_grant(roles: list[str], permission: Permission) -> bool:
    for role in roles:
        if permission in ROLE_PERMISSIONS.get(role, frozenset()):
            return True
    return False
