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
from app.models.auth_tokens import RefreshToken
from app.models.certificates import Certificate, CertificatePackage, CertificateStore, CertificateType
from app.models.infrastructure import VCenterConnection
from app.models.jobs import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobStatus,
    JobType,
    ProvisioningJob,
    ProvisioningJobStep,
    StepStatus,
    VmProvisioningRequest,
)
from app.models.notifications import Notification, NotificationKind
from app.models.platform import PlatformSetting, SecretReference
from app.models.rbac import Role, UserRole
from app.models.user import User

__all__ = [
    "ACTIVE_JOB_STATUSES",
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
    "Notification",
    "NotificationKind",
    "PlatformSetting",
    "ProvisioningJob",
    "ProvisioningJobStep",
    "RefreshToken",
    "Role",
    "SecretReference",
    "StepStatus",
    "TERMINAL_JOB_STATUSES",
    "User",
    "UserRole",
    "VCenterConnection",
    "VmProvisioningRequest",
]
