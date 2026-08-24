"""Guest-login and domain-join credentials must remain separate."""

from __future__ import annotations

import pytest

from app.schemas.provisioning import DomainJoinSpec, GuestSpec
from app.workers.context import JobRunContext
from tests.conftest import make_request


class RecordingSecrets:
    def __init__(self) -> None:
        self.requested: list[str] = []

    async def get_secret(self, name: str) -> str:
        self.requested.append(name)
        return "local-user" if name.endswith("/username") else "local-password"


@pytest.mark.asyncio
async def test_domain_join_does_not_replace_local_guest_login() -> None:
    request = make_request(
        guest=GuestSpec(
            template_id="template-1",
            domain_join=DomainJoinSpec(
                domain="ad.example.test",
                credential_secret_ref="domain-join",
            ),
        )
    )
    secrets = RecordingSecrets()
    context = JobRunContext(
        db=None,  # type: ignore[arg-type]
        job=None,  # type: ignore[arg-type]
        request=request,
        target=None,  # type: ignore[arg-type]
        vmware=None,  # type: ignore[arg-type]
        guest_ops=None,  # type: ignore[arg-type]
        cert_deployer=None,  # type: ignore[arg-type]
        app_installer=None,  # type: ignore[arg-type]
        secrets=secrets,  # type: ignore[arg-type]
        publisher=None,  # type: ignore[arg-type]
    )

    credentials = await context.resolve_guest_credentials()

    assert credentials.username == "local-user"
    assert secrets.requested == [
        "guest-local-admin/username",
        "guest-local-admin/password",
    ]
