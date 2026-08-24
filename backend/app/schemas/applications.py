"""Application catalog schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

from app.models.applications import DetectionMethod, InstallerType
from app.services.applications.detection_rules import validate_detection_config


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    description: str
    installer_type: InstallerType
    installer_path: str
    install_arguments: str
    detection_method: DetectionMethod
    detection_config: dict
    timeout_seconds: int
    reboot_required: bool
    enabled: bool
    dependency_ids: list[str] = []
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    version: str = Field(default="", max_length=50)
    description: str = Field(default="", max_length=2000)
    installer_type: InstallerType
    installer_path: str = Field(min_length=3, max_length=500)
    install_arguments: str = Field(default="", max_length=2000)
    detection_method: DetectionMethod
    detection_config: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=600, ge=30, le=14400)
    reboot_required: bool = False
    enabled: bool = True
    dependency_ids: list[UUID4] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _validate_detection(self) -> "ApplicationCreate":
        self.detection_config = validate_detection_config(self.detection_method, self.detection_config)
        return self


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=200)
    version: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    installer_type: InstallerType | None = None
    installer_path: str | None = Field(default=None, min_length=3, max_length=500)
    install_arguments: str | None = Field(default=None, max_length=2000)
    detection_method: DetectionMethod | None = None
    detection_config: dict | None = None
    timeout_seconds: int | None = Field(default=None, ge=30, le=14400)
    reboot_required: bool | None = None
    enabled: bool | None = None
    dependency_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _validate_detection_pair(self) -> "ApplicationUpdate":
        if self.detection_method is not None and self.detection_config is not None:
            self.detection_config = validate_detection_config(self.detection_method, self.detection_config)
        return self
