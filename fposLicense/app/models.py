from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LicenseStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class InstallationStatus(StrEnum):
    ACTIVE = "active"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    license_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default=LicenseStatus.ACTIVE)
    active_installation_id: Mapped[int | None] = mapped_column(nullable=True)
    conflict_open: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    installations: Mapped[list[Installation]] = relationship(
        back_populates="license", cascade="all, delete-orphan"
    )


class Installation(Base):
    __tablename__ = "installations"
    __table_args__ = (
        Index("ix_installations_license_status", "license_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    license_id: Mapped[int] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64))
    hardware_fingerprint: Mapped[str] = mapped_column(String(128))
    hostname: Mapped[str] = mapped_column(String(128))
    fpos_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=InstallationStatus.ACTIVE)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    license: Mapped[License] = relationship(back_populates="installations")


class LicenseEvent(Base):
    __tablename__ = "license_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    license_id: Mapped[int | None] = mapped_column(
        ForeignKey("licenses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

