"""Aggregated /api/v1 router."""

from fastapi import APIRouter

from app.api.v1.admin_applications import router as admin_applications_router
from app.api.v1.admin_certificates import router as admin_certificates_router
from app.api.v1.admin_infrastructure import router as admin_infrastructure_router
from app.api.v1.admin_platform import router as admin_platform_router
from app.api.v1.applications import router as applications_router
from app.api.v1.audit import router as audit_router
from app.api.v1.auth import router as auth_router
from app.api.v1.certificates import router as certificates_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.infrastructure import router as infrastructure_router
from app.api.v1.provisioning import router as provisioning_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(infrastructure_router)
api_v1_router.include_router(provisioning_router)
api_v1_router.include_router(applications_router)
api_v1_router.include_router(certificates_router)
api_v1_router.include_router(admin_infrastructure_router)
api_v1_router.include_router(admin_certificates_router)
api_v1_router.include_router(admin_applications_router)
api_v1_router.include_router(admin_platform_router)
api_v1_router.include_router(audit_router)
api_v1_router.include_router(dashboard_router)
