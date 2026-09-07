import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


LICENSE_KEY_PATTERN = re.compile(r"^(?:FPOS|IPOS)-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$")


def normalize_license_key(value: str) -> str:
    normalized = value.strip().upper()
    if not LICENSE_KEY_PATTERN.fullmatch(normalized):
        raise ValueError("Format license key tidak valid")
    return normalized


class DecisionStatus(StrEnum):
    ACTIVE = "active"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class DeviceIdentity(BaseModel):
    installation_id: str = Field(
        min_length=16, max_length=64, pattern=r"^[A-Za-z0-9-]+$"
    )
    hostname: str = Field(min_length=1, max_length=128)
    hardware_fingerprint: str = Field(min_length=8, max_length=128)
    fpos_url: HttpUrl | None = None


class ActivateRequest(DeviceIdentity):
    license_key: str = Field(min_length=8, max_length=64)

    @field_validator("license_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return normalize_license_key(value)


class AuthenticatedDeviceRequest(DeviceIdentity):
    license_key: str = Field(min_length=8, max_length=64)
    installation_token: str = Field(min_length=32, max_length=128)

    @field_validator("license_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return normalize_license_key(value)


class SelectServerRequest(AuthenticatedDeviceRequest):
    selected_installation_id: str = Field(
        min_length=16, max_length=64, pattern=r"^[A-Za-z0-9-]+$"
    )
    confirmation_hostname: str = Field(min_length=1, max_length=128)


class InstallationSummary(BaseModel):
    installation_id: str
    hostname: str
    fpos_url: str | None
    status: str
    last_seen_at: datetime


class SignedDecision(BaseModel):
    decision: dict
    signature: str
    installation_token: str | None = None


class CreateLicenseRequest(BaseModel):
    license_key: str = Field(min_length=8, max_length=64)
    owner_name: str = Field(min_length=1, max_length=200)

    @field_validator("license_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return normalize_license_key(value)


class UpdateLicenseStatusRequest(BaseModel):
    status: Literal["active", "revoked"]


class LicenseAdminView(BaseModel):
    license_key: str
    owner_name: str
    status: str
    conflict_open: bool
    installations: list[InstallationSummary]
