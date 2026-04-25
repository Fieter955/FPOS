from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .. import models

router = APIRouter()

# ==========================================
# SCHEMA PYDANTIC (KUNCI HANDLE 2 FRONTEND)
# ==========================================
class PrintJobCreate(BaseModel):
    content: str
    content_type: str = "raw"               # Default 'raw' untuk struk kasir
    paper_width_mm: Optional[float] = None  # Dari barcode.html (Boleh kosong)
    paper_height_mm: Optional[float] = None # Dari barcode.html (Boleh kosong)
    branch_id: Optional[int] = None         # Jika frontend tidak kirim, ambil dari header cabang aktif

# ==========================================
# 1. POST: TERIMA ANTREAN DARI FRONTEND
# ==========================================
@router.post("/")
def create_print_job(
    job: PrintJobCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Menerima antrean cetak dari frontend Barcode maupun POS Kasir.
    """
    header_branch_id = request.headers.get("X-Branch-ID")
    resolved_branch_id = job.branch_id

    if resolved_branch_id is None and header_branch_id:
        try:
            resolved_branch_id = int(header_branch_id)
        except (TypeError, ValueError):
            resolved_branch_id = None

    if resolved_branch_id is None:
        resolved_branch_id = 1

    new_job = models.PrintJob(
        branch_id=resolved_branch_id,
        content=job.content,
        content_type=job.content_type,
        status="pending"
    )
    
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    
    return {
        "success": True, 
        "message": "Print job berhasil ditambahkan",
        "job_id": new_job.id,
        "branch_id": new_job.branch_id,
    }

# ==========================================
# 2. GET: DIAMBIL OLEH AGEN PRINTER WINDOWS
# ==========================================
@router.get("/")
def get_pending_jobs(branch_id: int = 1, db: Session = Depends(get_db)):
    jobs = db.query(models.PrintJob).filter(
        models.PrintJob.branch_id == branch_id,
        models.PrintJob.status == "pending"
    ).order_by(models.PrintJob.id.asc()).all()

    if not jobs:
        return []

    for job in jobs:
        job.status = "processing"
    db.commit()

    return [{"id": j.id, "content": j.content, "content_type": getattr(j, "content_type", "raw")} for j in jobs]

# ==========================================
# 3. POST: MARK AS DONE (DARI AGEN)
# ==========================================
@router.post("/done/{job_id}")
def mark_job_done(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.PrintJob).get(job_id)
    if job:
        job.status = "done"
        db.commit()
    return {"success": True}

# ==========================================
# 4. UTILITY: RESET & BERSIHKAN
# ==========================================
@router.post("/reset-stuck")
def reset_stuck(branch_id: int = 1, db: Session = Depends(get_db)):
    stuck = db.query(models.PrintJob).filter(
        models.PrintJob.branch_id == branch_id,
        models.PrintJob.status == "processing"
    ).all()
    for job in stuck:
        job.status = "pending"
    db.commit()
    return {"reset": len(stuck)}

@router.get("/bersihkan")
def bersihkan_semua_job(branch_id: int = 1, db: Session = Depends(get_db)):
    jobs_lama = db.query(models.PrintJob).filter(
        models.PrintJob.branch_id == branch_id,
        models.PrintJob.status.in_(["pending", "processing"])
    ).all()

    jumlah = len(jobs_lama)
    for job in jobs_lama:
        db.delete(job)
    db.commit()

    return {"success": True, "dihapus": jumlah}
