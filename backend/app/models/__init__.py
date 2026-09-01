"""All ORM models. Importing this package registers every table with the
declarative base so Alembic autogenerate and ``create_all`` see the full schema.
"""

from app.models.applications import (
    Application,
    ApplicationDependency,
    DetectionMethod,
    InstallerType,
)
from app.models.audit import AuditEvent
from app.models.certificates import Certificate, CertificatePackage, CertificateStore, CertificateType
from app.models.infrastructure import VCenterConnection
from app.models.jobs import (
    TERMINAL_JOB_STATUSES,
    JobStatus,
    JobType,
    ProvisioningJob,
    ProvisioningJobStep,
    StepStatus,
    VmProvisioningRequest,
)
from app.models.platform import PlatformSetting, SecretReference
from app.models.rbac import Role, UserRole
from app.models.user import User

__all__ = [
    "Application",
    "ApplicationDependency",
    "AuditEvent",
    "Certificate",
    "CertificatePackage",
    "CertificateStore",
    "CertificateType",
    "DetectionMethod",
    "InstallerType",
    "JobStatus",
    "JobType",
    "PlatformSetting",
    "ProvisioningJob",
    "ProvisioningJobStep",
    "Role",
    "SecretReference",
    "StepStatus",
    "TERMINAL_JOB_STATUSES",
    "User",
    "UserRole",
    "VCenterConnection",
    "VmProvisioningRequest",
]
