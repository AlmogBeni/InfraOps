"""FastAPI application factory / entrypoint."""

from __future__ import annotations

import uuid as uuid_module
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app import __version__
from app.api.v1 import api_v1_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import bind_logging_context, configure_logging, get_logger
from app.core.metrics import http_requests_total
from app.db.base import Base
from app.db.session import engine

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log.info("InfraOps API starting (environment=%s infrastructure_mode=%s)",
             settings.environment.value, settings.infrastructure_mode.value)
    yield
    log.info("InfraOps API shutting down")
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} — Infrastructure Automation Platform",
        version=__version__,
        description=(
            "Internal automation portal for the IT infrastructure team. "
            "Phase 1: VM provisioning against VMware vSphere with guest OS "
            "configuration, certificate deployment and application installation."
        ),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def security_and_metrics_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid_module.uuid4().hex
        bind_logging_context(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        path = request.scope.get("route").path if request.scope.get("route") else request.url.path
        http_requests_total.inc(method=request.method, path=path[:80], status=str(response.status_code))
        return response

    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/api/docs")

    return app


app = create_app()
