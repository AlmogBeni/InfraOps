"""RBAC permission matrix tests."""

from __future__ import annotations

from app.auth.permissions import Permission, permissions_for_roles, roles_grant


class TestRoleMatrix:
    def test_viewer_is_read_only(self):
        granted = permissions_for_roles(["viewer"])
        assert Permission.INFRASTRUCTURE_READ.value in granted
        assert Permission.JOBS_READ.value in granted
        assert Permission.AUDIT_READ.value in granted
        assert Permission.PROVISIONING_SUBMIT.value not in granted
        assert Permission.ADMIN_VCENTERS.value not in granted

    def test_operator_can_provision_and_retry(self):
        granted = set(permissions_for_roles(["operator"]))
        assert Permission.PROVISIONING_SUBMIT.value in granted
        assert Permission.PROVISIONING_VALIDATE.value in granted
        assert Permission.JOBS_RETRY.value in granted
        assert Permission.JOBS_CANCEL.value in granted
        # Operators never administer the platform.
        assert Permission.ADMIN_VCENTERS.value not in granted
        assert Permission.ADMIN_APPLICATIONS.value not in granted
        assert Permission.ADMIN_SETTINGS.value not in granted

    def test_administrator_grants_everything(self):
        granted = set(permissions_for_roles(["administrator"]))
        assert granted == {p.value for p in Permission}

    def test_roles_grant_lookup(self):
        assert roles_grant(["operator"], Permission.PROVISIONING_SUBMIT) is True
        assert roles_grant(["viewer"], Permission.PROVISIONING_SUBMIT) is False
        assert roles_grant([], Permission.JOBS_READ) is False

    def test_multiple_roles_union(self):
        granted = set(permissions_for_roles(["viewer", "operator"]))
        assert Permission.PROVISIONING_SUBMIT.value in granted
