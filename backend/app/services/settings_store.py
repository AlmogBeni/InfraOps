"""Platform settings store — typed access to administrator-editable settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import PlatformSetting

SETTING_VM_NAME_POLICY = "vm_name_policy_regex"
SETTING_ALLOWED_INSTALLER_ROOTS = "allowed_installer_roots"
SETTING_DEFAULT_TIMEOUTS = "default_timeouts"
SETTING_IPAM_ENABLED = "ipam_enabled"
SETTING_ENVIRONMENT_LABEL = "environment_label"

DEFAULTS: dict[str, object] = {
    SETTING_VM_NAME_POLICY: "",
    SETTING_ALLOWED_INSTALLER_ROOTS: ["\\\\software.company.local\\packages\\"],
    SETTING_DEFAULT_TIMEOUTS: {},
    SETTING_IPAM_ENABLED: False,
    SETTING_ENVIRONMENT_LABEL: "INTERNAL",
}

KNOWN_KEYS = set(DEFAULTS)


async def load_platform_settings(db: AsyncSession) -> dict[str, object]:
    """Return the effective settings (defaults overlaid with stored values)."""
    result = await db.execute(select(PlatformSetting))
    stored = {row.key: row.value for row in result.scalars().all()}
    effective = dict(DEFAULTS)
    for key, value in stored.items():
        if key in KNOWN_KEYS and value is not None:
            effective[key] = value
    return effective


async def save_platform_setting(db: AsyncSession, key: str, value: object, updated_by) -> None:
    row = await db.get(PlatformSetting, key)
    if row is None:
        row = PlatformSetting(key=key, value={"value": value}, updated_by=updated_by)
        db.add(row)
    else:
        row.value = {"value": value}
        row.updated_by = updated_by
    await db.flush()


def read_stored_value(raw: object) -> object:
    """Settings rows wrap their payload as {"value": ...}."""
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw


async def load_effective(db: AsyncSession) -> dict[str, object]:
    effective = await load_platform_settings(db)
    return {key: read_stored_value(value) for key, value in effective.items()}
