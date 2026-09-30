"""Stream tickets, header-only bearer tokens, key rotation, idempotency."""

from __future__ import annotations

import uuid

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.auth.dependencies import _resolve_token
from app.auth.stream_tickets import StreamTicketStore
from app.core.errors import AuthenticationError


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def getdel(self, key):
        return self.data.pop(key, None)


@pytest.fixture
def store() -> StreamTicketStore:
    ticket_store = object.__new__(StreamTicketStore)
    ticket_store._redis = FakeRedis()
    return ticket_store


@pytest.mark.asyncio
async def test_stream_ticket_is_single_use_and_bound_to_one_job(store) -> None:
    user_id, job_id = uuid.uuid4(), uuid.uuid4()
    ticket = await store.issue(user_id=user_id, job_id=job_id)

    assert await store.consume(ticket, job_id=uuid.uuid4()) is None  # wrong job burns it
    ticket = await store.issue(user_id=user_id, job_id=job_id)
    assert await store.consume(ticket, job_id=job_id) == user_id
    assert await store.consume(ticket, job_id=job_id) is None  # replay rejected
    assert ticket not in "".join(store._redis.data)  # only hashes are stored


def test_bearer_token_is_accepted_only_from_the_authorization_header() -> None:
    assert _resolve_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials="abc")) == "abc"
    with pytest.raises(AuthenticationError):
        _resolve_token(None)


def test_credentials_survive_an_encryption_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from app.secrets.encryption import decrypt_secret, encrypt_secret, reencrypt_secret

    settings = get_settings()
    legacy = encrypt_secret("vcenter-password")  # derived from SECRET_KEY

    monkeypatch.setattr(settings, "credential_encryption_key", "a-dedicated-credential-key-of-32-chars!!")
    assert decrypt_secret(legacy) == "vcenter-password"  # SECRET_KEY stays a decryption fallback
    rotated = reencrypt_secret(legacy)

    monkeypatch.setattr(settings, "secret_key", "a-completely-new-jwt-signing-key-with-32+chars")
    assert decrypt_secret(rotated) == "vcenter-password"  # JWT key rotation no longer matters


def test_idempotency_fingerprint_tracks_the_request_body() -> None:
    from app.services.provisioning.service import request_fingerprint
    from tests.conftest import make_request

    first = make_request()
    same = make_request()
    different = make_request()
    different.hardware.cpu = 8
    assert request_fingerprint(first) == request_fingerprint(same)
    assert request_fingerprint(first) != request_fingerprint(different)


def test_session_policy_is_public_and_reflects_settings() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.auth import router

    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get("/auth/session-policy").json()
    assert body == {"idle_timeout_seconds": 300, "idle_warning_seconds": 60}


def _refresh_set_cookie(monkeypatch: pytest.MonkeyPatch, peer: str, headers: dict[str, str]) -> list[str]:
    from starlette.requests import Request
    from starlette.responses import Response

    from app.api.v1.auth import _set_refresh_cookie
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "172.16.0.0/12")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",  # nginx always reaches the API over plain HTTP
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
            "client": (peer, 50000),
            "server": ("backend", 8000),
        }
    )
    response = Response()
    _set_refresh_cookie(request, response, "refresh-token")
    return [part.strip().lower() for part in response.headers["set-cookie"].split(";")]


@pytest.mark.parametrize(
    ("headers", "secure"),
    [
        # Plain-HTTP deployment: browsers would discard a Secure cookie, and
        # every reload would sign the user out.
        ({"x-forwarded-proto": "http", "origin": "http://infraops.corp.example"}, False),
        ({"x-forwarded-proto": "https", "origin": "https://infraops.corp.example"}, True),
        # TLS terminated by a proxy nginx does not trust: the page origin still shows HTTPS.
        ({"x-forwarded-proto": "http", "origin": "https://infraops.corp.example"}, True),
    ],
)
def test_refresh_cookie_is_secure_exactly_when_the_browser_uses_https(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str], secure: bool
) -> None:
    attributes = _refresh_set_cookie(monkeypatch, "172.18.0.5", headers)

    assert ("secure" in attributes) is secure
    assert "httponly" in attributes
    assert "path=/api/v1/auth" in attributes
