"""User accounts.

``password_hash`` is ``NULL`` when authentication is delegated to an external
enterprise provider (LDAP / OIDC / SAML) — the schema is prepared for that
without storing anything sensitive locally.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, String, Uuid, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, updated_at_column, uuid_primary_key
from app.models.rbac import Role, UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    roles: Mapped[list[Role]] = relationship(
        secondary=UserRole.__table__, lazy="selectin", viewonly=True
    )

    @property
    def role_names(self) -> list[str]:
        return sorted(role.name for role in self.roles)

    def has_any_role(self, *names: str) -> bool:
        return any(role.name in names for role in self.roles)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"
