from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from ..database import get_db
from .. import models
from ..auth import get_current_user, write_audit
from ..services.shift_service import open_branch_shifts, require_active_branch_id

router = APIRouter()
import pytz
WITA = pytz.timezone("Asia/Makassar")
def get_local_datetime(): return datetime.now(WITA)

@router.get("/current")
def get_current_shift(db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    shifts = open_branch_shifts(db, current_user)
    if not shifts:
        return None

    # Data lama mungkin masih mempunyai beberapa shift terbuka per cabang.
    # Tampilkan yang paling lama agar dapat ditutup manual satu per satu, tetapi
    # tandai konflik supaya frontend tidak mengizinkan kasir masuk ke POS.
    result = _shift_summary(shifts[0], db)
    result["has_conflict"] = len(shifts) > 1
    result["open_shift_count"] = len(shifts)
    return result


@router.post("/open")
def open_shift(data: dict, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    branch_id = require_active_branch_id(current_user)

    # Kunci baris cabang pada DB yang mendukung SELECT FOR UPDATE. Bersama unique
    # partial index SQLite, ini mencegah dua kasir membuka shift secara bersamaan.
    branch = (
        db.query(models.Branch)
        .filter(models.Branch.id == branch_id)
        .with_for_update()
        .first()
    )
    if not branch:
        raise HTTPException(400, "Cabang aktif tidak ditemukan.")
    if open_branch_shifts(db, current_user, for_update=True, limit=1):
        raise HTTPException(400, "Cabang ini sudah memiliki shift yang terbuka.")

    shift = models.Shift(
        user_id=current_user.id,
        branch_id=branch_id,
        opening_cash=float(data.get("opening_cash", 0)),
        status="open"
    )
    db.add(shift)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Cabang ini sudah memiliki shift yang terbuka.")

    write_audit(db, current_user.id, "CREATE", "shifts", shift.id,
                f"Buka shift cabang {branch_id} dengan kas awal {shift.opening_cash}")
    db.commit()
    db.refresh(shift)

    return {"id": shift.id, "message": "Shift dibuka", "opened_at": shift.opened_at.isoformat() if shift.opened_at else None}


@router.post("/{shift_id}/close")
def close_shift(shift_id: int, data: dict, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    branch_id = require_active_branch_id(current_user)
    shift = db.query(models.Shift).filter(
        models.Shift.id == shift_id,
        models.Shift.branch_id == branch_id,
    ).with_for_update().first()

    if not shift: raise HTTPException(404, "Shift tidak ditemukan")
    if shift.status == "closed": raise HTTPException(400, "Shift sudah ditutup")

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
    opener_name = shift.user.full_name or shift.user.username
    closer_name = current_user.full_name or current_user.username
    write_audit(
        db,
        current_user.id,
        "UPDATE",
        "shifts",
        shift.id,
        f"Tutup shift cabang {branch_id}; dibuka oleh {opener_name}; ditutup oleh {closer_name}",
    )
    db.commit()

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
    branch_id = require_active_branch_id(current_user)
    q = db.query(models.Shift).filter(models.Shift.branch_id == branch_id)

    shifts = q.order_by(models.Shift.id.desc()).offset(skip).limit(limit).all()
    return [_shift_summary(s, db) for s in shifts]


@router.get("/{shift_id}")
def get_shift(shift_id: int, db: Session = Depends(get_db),
              current_user: models.User = Depends(get_current_user)):
    branch_id = require_active_branch_id(current_user)
    shift = db.query(models.Shift).filter(
        models.Shift.id == shift_id,
        models.Shift.branch_id == branch_id,
    ).first()

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
        "branch_id": shift.branch_id,
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
