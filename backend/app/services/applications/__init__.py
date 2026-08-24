"""Application catalog services: detection rules, dependency resolution, installation."""

from app.services.applications.installer import (
    ApplicationDefinition,
    ApplicationInstaller,
    InstallOutcome,
)
from app.services.applications.resolver import AppNode, resolve_install_order

__all__ = [
    "ApplicationDefinition",
    "ApplicationInstaller",
    "AppNode",
    "InstallOutcome",
    "resolve_install_order",
]
