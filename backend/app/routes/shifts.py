from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import datetime, date
from typing import Optional
from ..database import get_db
from .. import models
from ..auth import get_current_user, write_audit, get_query # 👈 TAMBAHAN get_query

router = APIRouter()
import pytz
WITA = pytz.timezone("Asia/Makassar")
def get_local_datetime(): return datetime.now(WITA)

@router.get("/current")
def get_current_shift(db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    # 👇 UBAH: Gunakan get_query agar hanya mencari shift aktif di cabangnya
    shift = get_query(db, models.Shift, current_user).filter(
        models.Shift.user_id == current_user.id,
        models.Shift.status == "open"
    ).order_by(models.Shift.id.desc()).first()
    
    if not shift:
        return None
    return _shift_summary(shift, db)


@router.post("/open")
def open_shift(data: dict, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    # 👇 UBAH: Cek apakah sudah ada shift terbuka di cabang ini
    existing = get_query(db, models.Shift, current_user).filter(
        models.Shift.user_id == current_user.id,
        models.Shift.status == "open"
    ).with_for_update().first()
    
    if existing:
        raise HTTPException(400, "Sudah ada shift yang terbuka. Tutup shift dulu.")

    shift = models.Shift(
        user_id=current_user.id,
        branch_id=current_user.active_branch_id, # 👈 STEMPEL CABANG DI SINI!
        opening_cash=float(data.get("opening_cash", 0)),
        status="open"
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)
    
    write_audit(db, current_user.id, "CREATE", "shifts", shift.id,
                f"Buka shift dengan kas awal {shift.opening_cash}")
    
    return {"id": shift.id, "message": "Shift dibuka", "opened_at": shift.opened_at.isoformat() if shift.opened_at else None}


@router.post("/{shift_id}/close")
def close_shift(shift_id: int, data: dict, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    # 👇 UBAH: Gunakan get_query untuk keamanan tutup shift
    shift = get_query(db, models.Shift, current_user).filter(models.Shift.id == shift_id).with_for_update().first()
    
    if not shift: raise HTTPException(404, "Shift tidak ditemukan")
    if shift.status == "closed": raise HTTPException(400, "Shift sudah ditutup")
    if shift.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(403, "Bukan shift Anda")

    # Hitung total cash dari penjualan selama shift ini
    summary = _shift_summary(shift, db)
    system_cash = shift.opening_cash + summary["total_cash_sales"]
    closing_cash = float(data.get("closing_cash", 0))
    difference = closing_cash - system_cash

    shift.closing_cash = closing_cash
    shift.system_cash = system_cash
    shift.difference = difference
    shift.closed_at = get_local_datetime()
    shift.status = "closed"
    shift.notes = data.get("notes")
    db.commit()

    write_audit(db, current_user.id, "UPDATE", "shifts", shift.id, "Tutup shift")

    return {
        "message": "Shift ditutup",
        "opening_cash": shift.opening_cash,
        "total_sales": summary["total_sales"],
        "total_cash_sales": summary["total_cash_sales"],
        "total_non_cash": summary["total_non_cash"],
        "system_cash": system_cash,
        "closing_cash": closing_cash,
        "difference": difference,
        "transaction_count": summary["transaction_count"]
    }


@router.get("/")
def get_shifts(skip: int = 0, limit: int = 50,
            db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    # 👇 UBAH: Filter list shift berdasarkan cabang yang aktif
    q = get_query(db, models.Shift, current_user)
    
    if current_user.role != "admin":
        q = q.filter(models.Shift.user_id == current_user.id)
        
    shifts = q.order_by(models.Shift.id.desc()).offset(skip).limit(limit).all()
    return [_shift_summary(s, db) for s in shifts]


@router.get("/{shift_id}")
def get_shift(shift_id: int, db: Session = Depends(get_db),
              current_user: models.User = Depends(get_current_user)):
    # 👇 UBAH: Detail shift hanya bisa dibuka jika sesuai cabangnya
    shift = get_query(db, models.Shift, current_user).filter(models.Shift.id == shift_id).first()
    
    if not shift: raise HTTPException(404, "Shift tidak ditemukan")
    return _shift_summary(shift, db, detail=True)


def _shift_summary(shift: models.Shift, db: Session, detail: bool = False):
    # Aman menggunakan db.query biasa karena shift.id sudah terfilter di fungsi pemanggilnya
    sales = db.query(models.Sale).filter(models.Sale.shift_id == shift.id).all()
    total_sales = sum(s.total for s in sales)
    total_cash = sum(s.paid for s in sales if s.payment_method == "cash")
    total_transfer = sum(s.total for s in sales if s.payment_method == "transfer")
    total_card = sum(s.total for s in sales if s.payment_method == "card")

    result = {
        "id": shift.id,
        "user_id": shift.user_id,
        "user_name": shift.user.full_name or shift.user.username,
        "opening_cash": shift.opening_cash,
        "closing_cash": shift.closing_cash,
        "system_cash": shift.system_cash,
        "difference": shift.difference,
        "opened_at": shift.opened_at.isoformat() if shift.opened_at else None,
        "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
        "status": shift.status,
        "notes": shift.notes,
        "total_sales": total_sales,
        "total_cash_sales": total_cash,
        "total_transfer": total_transfer,
        "total_card": total_card,
        "total_non_cash": total_transfer + total_card,
        "transaction_count": len(sales)
    }
    if detail:
        result["sales"] = [{
            "number": s.number, "total": s.total,
            "payment_method": s.payment_method, "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None
        } for s in sales]
    return result