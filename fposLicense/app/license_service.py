import json
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models, schemas
from .config import Settings
from .security import new_installation_token, hash_token, sign_decision, verify_token


BLOCK_DURATION = timedelta(hours=24)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class LicenseService:
    """Owns every license state transition and its audit trail."""

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def activate(self, request: schemas.ActivateRequest) -> schemas.SignedDecision:
        license_record = self._license(request.license_key)
        installation = self._find_installation(request.installation_id)
        token: str | None = None
        now = datetime.now(timezone.utc)

        if installation and installation.license_id != license_record.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Installation belongs to another license")

        if installation is None:
            token = new_installation_token()
            installation = models.Installation(
                installation_id=request.installation_id,
                license_id=license_record.id,
                token_hash=hash_token(token),
                hardware_fingerprint=request.hardware_fingerprint,
                hostname=request.hostname,
                fpos_url=str(request.fpos_url) if request.fpos_url else None,
            )
            self.db.add(installation)
            self.db.flush()
            if license_record.active_installation_id is None:
                license_record.active_installation_id = installation.id
            else:
                license_record.conflict_open = True
                installation.status = models.InstallationStatus.CONFLICT
                for other in license_record.installations:
                    if other.status != models.InstallationStatus.BLOCKED:
                        other.status = models.InstallationStatus.CONFLICT
            self._event(license_record.id, installation.installation_id, "activated")
        else:
            self._update_identity(installation, request)

        installation.last_seen_at = now
        self.db.commit()

        # A second installation remains provisionally usable until its daily check.
        decision = self._decision(
            license_record,
            installation,
            force_status=schemas.DecisionStatus.ACTIVE,
            conflict_pending=license_record.conflict_open,
        )
        return self._signed(decision, token)

    def check(self, request: schemas.AuthenticatedDeviceRequest) -> schemas.SignedDecision:
        license_record, installation = self._authenticated(request)
        self._update_identity(installation, request)
        installation.last_seen_at = datetime.now(timezone.utc)

        if license_record.conflict_open:
            installation.status = models.InstallationStatus.CONFLICT
            decision = self._decision(license_record, installation)
        elif installation.id == license_record.active_installation_id:
            installation.status = models.InstallationStatus.ACTIVE
            installation.blocked_until = None
            decision = self._decision(license_record, installation)
        else:
            installation.status = models.InstallationStatus.BLOCKED
            if installation.blocked_until is None:
                installation.blocked_until = datetime.now(timezone.utc) + BLOCK_DURATION
            decision = self._decision(license_record, installation)

        self.db.commit()
        return self._signed(decision)

    def select_server(self, request: schemas.SelectServerRequest) -> schemas.SignedDecision:
        license_record, requester = self._authenticated(request)
        selected = next(
            (
                item
                for item in license_record.installations
                if item.installation_id == request.selected_installation_id
            ),
            None,
        )
        if selected is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Selected installation not found")
        if request.confirmation_hostname.strip() != selected.hostname:
            raise HTTPException(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "Hostname confirmation does not match",
            )

        now = datetime.now(timezone.utc)
        license_record.active_installation_id = selected.id
        license_record.conflict_open = False
        for installation in license_record.installations:
            if installation.id == selected.id:
                installation.status = models.InstallationStatus.ACTIVE
                installation.blocked_until = None
            else:
                installation.status = models.InstallationStatus.BLOCKED
                installation.blocked_until = now + BLOCK_DURATION
        self._event(
            license_record.id,
            requester.installation_id,
            "server_selected",
            {"selected_installation_id": selected.installation_id},
        )
        self.db.commit()
        return self._signed(self._decision(license_record, requester))

    def reclaim(self, request: schemas.AuthenticatedDeviceRequest) -> schemas.SignedDecision:
        license_record, installation = self._authenticated(request)
        blocked_until = _aware(installation.blocked_until)
        if installation.id == license_record.active_installation_id:
            return self._signed(self._decision(license_record, installation))
        if blocked_until and blocked_until > datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_423_LOCKED, "The 24-hour waiting period is still active")

        license_record.conflict_open = True
        for item in license_record.installations:
            item.status = models.InstallationStatus.CONFLICT
        self._event(license_record.id, installation.installation_id, "reclaim_requested")
        self.db.commit()
        return self._signed(self._decision(license_record, installation))

    def force_select(self, license_key: str, installation_id: str) -> None:
        """Vendor-only recovery path; device endpoints never call this method."""
        license_record = self._license(license_key)
        selected = next(
            (item for item in license_record.installations if item.installation_id == installation_id),
            None,
        )
        if selected is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Selected installation not found")
        now = datetime.now(timezone.utc)
        license_record.active_installation_id = selected.id
        license_record.conflict_open = False
        for installation in license_record.installations:
            if installation.id == selected.id:
                installation.status = models.InstallationStatus.ACTIVE
                installation.blocked_until = None
            else:
                installation.status = models.InstallationStatus.BLOCKED
                installation.blocked_until = now + BLOCK_DURATION
        self._event(
            license_record.id,
            selected.installation_id,
            "server_force_selected",
            {"selected_installation_id": selected.installation_id},
        )
        self.db.commit()

    def _license(self, license_key: str) -> models.License:
        record = self.db.scalar(
            select(models.License)
            .where(models.License.license_key == license_key)
            .options(selectinload(models.License.installations))
        )
        if record is None or record.status != models.LicenseStatus.ACTIVE:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "License is not active")
        return record

    def _find_installation(self, installation_id: str) -> models.Installation | None:
        return self.db.scalar(
            select(models.Installation).where(
                models.Installation.installation_id == installation_id
            )
        )

    def _authenticated(
        self, request: schemas.AuthenticatedDeviceRequest
    ) -> tuple[models.License, models.Installation]:
        license_record = self._license(request.license_key)
        installation = next(
            (
                item
                for item in license_record.installations
                if item.installation_id == request.installation_id
            ),
            None,
        )
        if installation is None or not verify_token(
            request.installation_token, installation.token_hash
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid installation credentials")
        return license_record, installation

    @staticmethod
    def _update_identity(installation: models.Installation, request: schemas.DeviceIdentity) -> None:
        installation.hostname = request.hostname
        installation.hardware_fingerprint = request.hardware_fingerprint
        installation.fpos_url = str(request.fpos_url) if request.fpos_url else None

    def _decision(
        self,
        license_record: models.License,
        installation: models.Installation,
        *,
        force_status: schemas.DecisionStatus | None = None,
        conflict_pending: bool = False,
    ) -> dict:
        now = datetime.now(timezone.utc)
        blocked_until = _aware(installation.blocked_until)
        if force_status:
            decision_status = force_status
        elif license_record.conflict_open:
            decision_status = schemas.DecisionStatus.CONFLICT
        elif installation.id == license_record.active_installation_id:
            decision_status = schemas.DecisionStatus.ACTIVE
        else:
            decision_status = schemas.DecisionStatus.BLOCKED

        return {
            "version": 1,
            "license_key": license_record.license_key,
            "installation_id": installation.installation_id,
            "status": decision_status.value,
            "conflict_pending": conflict_pending,
            "server_time": now.isoformat(),
            "blocked_until": blocked_until.isoformat() if blocked_until else None,
            "can_reclaim": bool(blocked_until and blocked_until <= now),
            "installations": [
                {
                    "installation_id": item.installation_id,
                    "hostname": item.hostname,
                    "fpos_url": item.fpos_url,
                    "status": item.status,
                    "last_seen_at": _aware(item.last_seen_at).isoformat(),
                }
                for item in sorted(license_record.installations, key=lambda row: row.first_seen_at)
            ],
        }

    def _signed(self, decision: dict, token: str | None = None) -> schemas.SignedDecision:
        return schemas.SignedDecision(
            decision=decision,
            signature=sign_decision(decision, self.settings),
            installation_token=token,
        )

    def _event(
        self,
        license_id: int,
        installation_id: str | None,
        event_type: str,
        details: dict | None = None,
    ) -> None:
        self.db.add(
            models.LicenseEvent(
                license_id=license_id,
                installation_id=installation_id,
                event_type=event_type,
                details=json.dumps(details or {}, separators=(",", ":")),
            )
        )
