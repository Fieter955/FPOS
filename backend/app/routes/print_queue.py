from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user, get_local_datetime, require_admin, write_audit
from ..database import get_db
from ..services.receipt_renderer import build_test_receipt, settings_from_branch


router = APIRouter()
MAX_ATTEMPTS = 3
LEASE_SECONDS = 120


class PrintJobCreate(BaseModel):
    content: str = Field(min_length=1)
    content_type: str = "raw"
    paper_width_mm: Optional[float] = None
    paper_height_mm: Optional[float] = None
    branch_id: Optional[int] = None

    @field_validator("content_type")
    @classmethod
    def valid_content_type(cls, value: str) -> str:
        normalized = (value or "raw").lower()
        if normalized not in {"raw", "html", "label_image"}:
            raise ValueError("content_type tidak didukung")
        return normalized


class PrintSettingsUpdate(BaseModel):
    receipt_name: str = Field(min_length=1, max_length=150)
    address: str = Field(default="", max_length=1000)
    phone: str = Field(default="", max_length=20)
    footer: str = Field(default="Terima kasih telah berbelanja!", max_length=500)
    paper_width_mm: int
    auto_print: bool = False

    @field_validator("receipt_name")
    @classmethod
    def valid_receipt_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Nama toko pada struk wajib diisi")
        return value

    @field_validator("paper_width_mm")
    @classmethod
    def valid_width(cls, value: int) -> int:
        if value not in {58, 80}:
            raise ValueError("Lebar kertas harus 58 atau 80 mm")
        return value


class AgentRegistration(BaseModel):
    name: str = Field(default="Printer Utama", min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Nama agen wajib diisi")
        return value


class AgentClaim(BaseModel):
    limit: int = Field(default=1, ge=1, le=5)


class AgentResult(BaseModel):
    success: bool
    error: Optional[str] = Field(default=None, max_length=1000)


def _active_branch(db: Session, current_user: models.User) -> models.Branch:
    branch_id = current_user.active_branch_id or current_user.branch_id
    if not branch_id:
        raise HTTPException(400, "Pilih cabang aktif terlebih dahulu")
    branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(404, "Cabang tidak ditemukan")
    return branch


def enqueue_print_job(
    db: Session,
    *,
    branch_id: int,
    content: str,
    content_type: str = "raw",
    document_type: Optional[str] = None,
    document_id: Optional[int] = None,
    created_by: Optional[int] = None,
) -> models.PrintJob:
    job = models.PrintJob(
        branch_id=branch_id,
        content=content,
        content_type=content_type,
        document_type=document_type,
        document_id=document_id,
        created_by=created_by,
        status="pending",
        attempt_count=0,
    )
    db.add(job)
    db.flush()
    return job


def _job_out(job: models.PrintJob) -> dict:
    return {
        "id": job.id,
        "branch_id": job.branch_id,
        "content_type": job.content_type,
        "document_type": job.document_type,
        "document_id": job.document_id,
        "status": job.status,
        "attempt_count": job.attempt_count or 0,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "claimed_at": job.claimed_at.isoformat() if job.claimed_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "last_error": job.last_error,
    }


def _agent_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _authenticate_agent(request: Request, db: Session) -> models.PrinterAgent:
    token = request.headers.get("X-Printer-Token", "").strip()
    if not token:
        raise HTTPException(401, "Token agen printer diperlukan")
    candidate = _agent_token_hash(token)
    agent = db.query(models.PrinterAgent).filter(
        models.PrinterAgent.token_hash == candidate,
        models.PrinterAgent.is_active.is_(True),
    ).first()
    if not agent or not hmac.compare_digest(agent.token_hash, candidate):
        raise HTTPException(401, "Token agen printer tidak valid")
    return agent


# Frontend Barcode dan pemanggil internal lama tetap dapat membuat job, tetapi
# cabang selalu diambil dari sesi pengguna—branch_id dari payload tidak dipercaya.
@router.post("/")
def create_print_job(
    payload: PrintJobCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch = _active_branch(db, current_user)
    job = enqueue_print_job(
        db,
        branch_id=branch.id,
        content=payload.content,
        content_type=payload.content_type,
        document_type="label" if payload.content_type in {"html", "label_image"} else "manual",
        created_by=current_user.id,
    )
    db.commit()
    return {"status": "queued", "job_id": job.id, "branch_id": job.branch_id}


@router.get("/settings")
def get_print_settings(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch = _active_branch(db, current_user)
    settings = settings_from_branch(branch)
    agent = db.query(models.PrinterAgent).filter(models.PrinterAgent.branch_id == branch.id).first()
    return {
        "branch_id": branch.id,
        "receipt_name": settings.store_name,
        "address": settings.address,
        "phone": settings.phone,
        "footer": settings.footer,
        "paper_width_mm": settings.paper_width_mm,
        "auto_print": settings.auto_print,
        "configured": bool(branch.receipt_name),
        "agent": {
            "name": agent.name,
            "token_last4": agent.token_last4,
            "is_active": agent.is_active,
            "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
        } if agent else None,
    }


@router.put("/settings")
def update_print_settings(
    payload: PrintSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    branch = _active_branch(db, current_user)
    branch.receipt_name = payload.receipt_name.strip()
    branch.address = payload.address.strip()
    branch.phone = payload.phone.strip()
    branch.receipt_footer = payload.footer.strip() or "Terima kasih telah berbelanja!"
    branch.receipt_paper_width_mm = payload.paper_width_mm
    branch.receipt_auto_print = payload.auto_print
    write_audit(db, current_user.id, "UPDATE", "branches", branch.id, "Ubah pengaturan struk cabang")
    db.commit()
    return get_print_settings(db=db, current_user=current_user)


@router.post("/test")
def queue_test_print(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch = _active_branch(db, current_user)
    job = enqueue_print_job(
        db,
        branch_id=branch.id,
        content=build_test_receipt(settings_from_branch(branch)),
        document_type="test",
        created_by=current_user.id,
    )
    db.commit()
    return {"status": "queued", "job_id": job.id, "branch_id": branch.id}


@router.post("/agent-token/rotate")
def rotate_agent_token(
    payload: AgentRegistration,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    branch = _active_branch(db, current_user)
    plain_token = secrets.token_urlsafe(32)
    token_hash = _agent_token_hash(plain_token)
    agent = db.query(models.PrinterAgent).filter(models.PrinterAgent.branch_id == branch.id).first()
    if agent:
        agent.name = payload.name.strip()
        agent.token_hash = token_hash
        agent.token_last4 = plain_token[-4:]
        agent.is_active = True
        agent.last_seen_at = None
    else:
        agent = models.PrinterAgent(
            branch_id=branch.id,
            name=payload.name.strip(),
            token_hash=token_hash,
            token_last4=plain_token[-4:],
        )
        db.add(agent)
    write_audit(db, current_user.id, "UPDATE", "printer_agents", branch.id, "Buat/rotasi token agen printer")
    db.commit()
    return {
        "branch_id": branch.id,
        "agent_name": agent.name,
        "token": plain_token,
        "message": "Simpan token sekarang; token tidak dapat ditampilkan kembali.",
    }


@router.get("/jobs")
def list_print_jobs(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch = _active_branch(db, current_user)
    limit = min(max(limit, 1), 200)
    query = db.query(models.PrintJob).filter(models.PrintJob.branch_id == branch.id)
    if status:
        query = query.filter(models.PrintJob.status == status)
    jobs = query.order_by(models.PrintJob.id.desc()).limit(limit).all()
    counts = {
        key: db.query(models.PrintJob).filter(
            models.PrintJob.branch_id == branch.id,
            models.PrintJob.status == key,
        ).count()
        for key in ("pending", "processing", "failed", "done", "cancelled")
    }
    return {"counts": counts, "jobs": [_job_out(job) for job in jobs]}


@router.post("/jobs/{job_id}/retry")
def retry_print_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch = _active_branch(db, current_user)
    job = db.query(models.PrintJob).filter(
        models.PrintJob.id == job_id,
        models.PrintJob.branch_id == branch.id,
    ).first()
    if not job:
        raise HTTPException(404, "Job cetak tidak ditemukan")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(400, "Hanya job gagal atau dibatalkan yang dapat dicoba ulang")
    job.status = "pending"
    job.attempt_count = 0
    job.claimed_at = None
    job.lease_until = None
    job.completed_at = None
    job.last_error = None
    write_audit(db, current_user.id, "UPDATE", "print_jobs", job.id, "Retry job cetak")
    db.commit()
    return _job_out(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_print_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch = _active_branch(db, current_user)
    job = db.query(models.PrintJob).filter(
        models.PrintJob.id == job_id,
        models.PrintJob.branch_id == branch.id,
    ).first()
    if not job:
        raise HTTPException(404, "Job cetak tidak ditemukan")
    if job.status not in {"pending", "failed"}:
        raise HTTPException(400, "Hanya job menunggu atau gagal yang dapat dibatalkan")
    job.status = "cancelled"
    job.claimed_at = None
    job.lease_until = None
    job.last_error = "Dibatalkan pengguna"
    write_audit(db, current_user.id, "UPDATE", "print_jobs", job.id, "Batalkan job cetak")
    db.commit()
    return _job_out(job)


# Endpoint agen dikecualikan dari JWT middleware, tetapi setiap request tetap
# wajib lolos validasi X-Printer-Token dan otomatis terikat ke satu cabang.
@router.post("/agent/claim")
def claim_jobs(payload: AgentClaim, request: Request, db: Session = Depends(get_db)):
    agent = _authenticate_agent(request, db)
    now = get_local_datetime()
    agent.last_seen_at = now

    expired = db.query(models.PrintJob).filter(
        models.PrintJob.branch_id == agent.branch_id,
        models.PrintJob.status == "processing",
        models.PrintJob.lease_until.is_not(None),
        models.PrintJob.lease_until < now,
    ).all()
    for job in expired:
        job.status = "failed" if (job.attempt_count or 0) >= MAX_ATTEMPTS else "pending"
        job.last_error = "Lease agen kedaluwarsa sebelum hasil diterima"
        job.claimed_at = None
        job.lease_until = None
    db.flush()

    candidates = db.query(models.PrintJob).filter(
        models.PrintJob.branch_id == agent.branch_id,
        models.PrintJob.status == "pending",
        or_(models.PrintJob.attempt_count.is_(None), models.PrintJob.attempt_count < MAX_ATTEMPTS),
    ).order_by(models.PrintJob.id.asc()).limit(payload.limit).all()

    claimed = []
    for job in candidates:
        next_attempt = (job.attempt_count or 0) + 1
        lease_until = now + timedelta(seconds=LEASE_SECONDS)
        # SQLite mengabaikan SELECT ... FOR UPDATE. UPDATE bersyarat ini memastikan
        # dua proses agen dengan token yang sama tidak dapat mengklaim job yang sama.
        updated = db.query(models.PrintJob).filter(
            models.PrintJob.id == job.id,
            models.PrintJob.branch_id == agent.branch_id,
            models.PrintJob.status == "pending",
        ).update({
            models.PrintJob.status: "processing",
            models.PrintJob.attempt_count: next_attempt,
            models.PrintJob.claimed_at: now,
            models.PrintJob.lease_until: lease_until,
        }, synchronize_session=False)
        if updated != 1:
            continue
        claimed.append({
            "id": job.id,
            "content": job.content,
            "content_type": job.content_type or "raw",
            "attempt_count": next_attempt,
            "lease_seconds": LEASE_SECONDS,
        })
    db.commit()
    return claimed


@router.post("/agent/jobs/{job_id}/result")
def report_job_result(
    job_id: int,
    payload: AgentResult,
    request: Request,
    db: Session = Depends(get_db),
):
    agent = _authenticate_agent(request, db)
    job = db.query(models.PrintJob).filter(
        models.PrintJob.id == job_id,
        models.PrintJob.branch_id == agent.branch_id,
    ).first()
    if not job:
        raise HTTPException(404, "Job cetak tidak ditemukan")
    if job.status == "done":
        return {"success": True, "status": "done", "idempotent": True}
    if job.status != "processing":
        raise HTTPException(409, f"Job tidak sedang diproses (status={job.status})")

    now = get_local_datetime()
    agent.last_seen_at = now
    job.claimed_at = None
    job.lease_until = None
    if payload.success:
        job.status = "done"
        job.completed_at = now
        job.last_error = None
    else:
        job.last_error = (payload.error or "Printer melaporkan kegagalan")[:1000]
        job.status = "failed" if (job.attempt_count or 0) >= MAX_ATTEMPTS else "pending"
    db.commit()
    return {"success": payload.success, "status": job.status, "attempt_count": job.attempt_count}
