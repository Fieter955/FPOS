import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models, schemas
from .config import Settings, get_settings
from .database import get_db
from .license_service import LicenseService
from .security import public_key_b64, require_admin


device_router = APIRouter(prefix="/api/v1", tags=["devices"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def get_service(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> LicenseService:
    return LicenseService(db, settings)


@device_router.get("/public-key")
def get_public_key(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {"algorithm": "Ed25519", "public_key": public_key_b64(settings)}


@device_router.post("/activate", response_model=schemas.SignedDecision)
def activate(
    payload: schemas.ActivateRequest, service: LicenseService = Depends(get_service)
) -> schemas.SignedDecision:
    return service.activate(payload)


@device_router.post("/check", response_model=schemas.SignedDecision)
def check(
    payload: schemas.AuthenticatedDeviceRequest,
    service: LicenseService = Depends(get_service),
) -> schemas.SignedDecision:
    return service.check(payload)


@device_router.post("/select-server", response_model=schemas.SignedDecision)
def select_server(
    payload: schemas.SelectServerRequest, service: LicenseService = Depends(get_service)
) -> schemas.SignedDecision:
    return service.select_server(payload)


@device_router.post("/reclaim", response_model=schemas.SignedDecision)
def reclaim(
    payload: schemas.AuthenticatedDeviceRequest,
    service: LicenseService = Depends(get_service),
) -> schemas.SignedDecision:
    return service.reclaim(payload)


@admin_router.post(
    "/licenses", response_model=schemas.LicenseAdminView, dependencies=[Depends(require_admin)]
)
def create_license(payload: schemas.CreateLicenseRequest, db: Session = Depends(get_db)):
    existing = db.scalar(select(models.License).where(models.License.license_key == payload.license_key))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "License key already exists")
    record = models.License(license_key=payload.license_key, owner_name=payload.owner_name)
    db.add(record)
    db.flush()
    db.add(
        models.LicenseEvent(
            license_id=record.id,
            event_type="license_created",
            details=payload.owner_name,
        )
    )
    db.commit()
    db.refresh(record)
    return schemas.LicenseAdminView(
        license_key=record.license_key,
        owner_name=record.owner_name,
        status=record.status,
        conflict_open=record.conflict_open,
        installations=[],
    )


@admin_router.post("/licenses/generate", dependencies=[Depends(require_admin)])
def generate_license_key() -> dict[str, str]:
    chunks = [secrets.token_hex(2).upper() for _ in range(4)]
    return {"license_key": "FPOS-" + "-".join(chunks)}


@admin_router.get(
    "/licenses", response_model=list[schemas.LicenseAdminView], dependencies=[Depends(require_admin)]
)
def list_licenses(db: Session = Depends(get_db)):
    records = db.scalars(
        select(models.License)
        .options(selectinload(models.License.installations))
        .order_by(models.License.created_at.desc())
    ).all()
    return [
        schemas.LicenseAdminView(
            license_key=record.license_key,
            owner_name=record.owner_name,
            status=record.status,
            conflict_open=record.conflict_open,
            installations=[
                schemas.InstallationSummary(
                    installation_id=item.installation_id,
                    hostname=item.hostname,
                    fpos_url=item.fpos_url,
                    status=item.status,
                    last_seen_at=item.last_seen_at,
                )
                for item in record.installations
            ],
        )
        for record in records
    ]


@admin_router.post(
    "/licenses/{license_key}/status",
    response_model=schemas.LicenseAdminView,
    dependencies=[Depends(require_admin)],
)
def update_license_status(
    license_key: str,
    payload: schemas.UpdateLicenseStatusRequest,
    db: Session = Depends(get_db),
):
    normalized_key = license_key.strip().upper()
    record = db.scalar(
        select(models.License)
        .where(models.License.license_key == normalized_key)
        .options(selectinload(models.License.installations))
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    record.status = payload.status
    db.add(
        models.LicenseEvent(
            license_id=record.id,
            event_type="license_status_changed",
            details=payload.status,
        )
    )
    db.commit()
    return schemas.LicenseAdminView(
        license_key=record.license_key,
        owner_name=record.owner_name,
        status=record.status,
        conflict_open=record.conflict_open,
        installations=[
            schemas.InstallationSummary(
                installation_id=item.installation_id,
                hostname=item.hostname,
                fpos_url=item.fpos_url,
                status=item.status,
                last_seen_at=item.last_seen_at,
            )
            for item in record.installations
        ],
    )


@admin_router.post(
    "/licenses/{license_key}/select/{installation_id}",
    dependencies=[Depends(require_admin)],
)
def force_select_server(
    license_key: str,
    installation_id: str,
    service: LicenseService = Depends(get_service),
) -> dict[str, bool]:
    service.force_select(license_key.strip().upper(), installation_id)
    return {"success": True}
