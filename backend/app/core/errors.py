"""Explicit application error types and HTTP exception handlers.

Infrastructure failures must never surface as opaque ``500`` errors. The
:class:`InfraOperationError` carries a human readable explanation, a technical
detail string (preserved separately for administrators) and a recommended
action — this maps directly onto the UX requirements for understandable
automation failures.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """Base class for all deliberate application errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"


class AuthorizationError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class DomainValidationError(AppError):
    """Business-rule validation failure (distinct from schema validation)."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_failed"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


class InfraOperationError(Exception):
    """A failure while performing an infrastructure operation.

    Raised by service layers and captured by the job engine. Everything except
    ``technical_detail`` is written for humans; the technical detail is stored
    separately and only shown to administrators.
    """

    def __init__(
        self,
        human_message: str,
        *,
        reason: str,
        recommended_action: str,
        technical_detail: str = "",
        retryable: bool = True,
    ) -> None:
        super().__init__(human_message)
        self.human_message = human_message
        self.reason = reason
        self.recommended_action = recommended_action
        self.technical_detail = technical_detail
        self.retryable = retryable

    def summary(self) -> str:
        return f"{self.human_message} Reason: {self.reason}"


def register_exception_handlers(app: FastAPI) -> None:
    """Install consistent JSON error responses for the whole API."""

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": [str(loc) for loc in err["loc"]], "msg": err["msg"], "type": err.get("type")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "schema_validation_failed",
                    "message": "Request payload failed schema validation.",
                    "details": {"issues": details},
                }
            },
        )

    @app.exception_handler(InfraOperationError)
    async def _infrastructure_error_handler(
        _: Request, exc: InfraOperationError
    ) -> JSONResponse:
        """Return actionable infrastructure failures without leaking technical detail."""
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": {
                    "code": "infrastructure_operation_failed",
                    "message": exc.human_message,
                    "details": {
                        "reason": exc.reason,
                        "recommended_action": exc.recommended_action,
                        "retryable": exc.retryable,
                    },
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected internal error occurred. The incident has been logged.",
                    "details": {},
                }
            },
        )
