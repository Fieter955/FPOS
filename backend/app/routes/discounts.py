"""
iPos 5.0 — Diskon Bertingkat & Promosi Periode
- Diskon by qty
- Diskon by total belanja
- Promosi period (tanggal mulai-selesai)
- Auto-apply di POS
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

router = APIRouter()


class DiscountTierCreate(BaseModel):
    name: str
    item_id: Optional[int] = None        # null = berlaku semua item
    category_id: Optional[int] = None    # null = berlaku semua kategori
    min_qty: Optional[float] = None
    min_amount: Optional[float] = None
    discount_percent: float
    start_date: Optional[date] = None
    end_date: Optional[date] = None


@router.get("/")
def get_discounts(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.DiscountTier)
    if active_only:
        today = date.today()
        q = q.filter(
            models.DiscountTier.is_active == True,
            (models.DiscountTier.start_date == None) |
            (models.DiscountTier.start_date <= today),
            (models.DiscountTier.end_date == None) |
            (models.DiscountTier.end_date >= today)
        )
    tiers = q.order_by(models.DiscountTier.discount_percent.desc()).all()
    return [{
        "id": t.id, "name": t.name,
        "item_id": t.item_id,
        "item_name": t.item.name if hasattr(t, 'item') and t.item else "Semua Item",
        "category_id": t.category_id,
        "min_qty": t.min_qty, "min_amount": t.min_amount,
        "discount_percent": t.discount_percent,
        "start_date": str(t.start_date) if t.start_date else None,
        "end_date": str(t.end_date) if t.end_date else None,
        "is_active": t.is_active,
        "is_promo": bool(t.start_date or t.end_date),
        "status": _get_status(t)
    } for t in tiers]


def _get_status(t: models.DiscountTier) -> str:
    today = date.today()
    if not t.is_active: return "nonaktif"
    if t.end_date and t.end_date < today: return "expired"
    if t.start_date and t.start_date > today: return "belum mulai"
    return "aktif"


@router.post("/")
def create_discount(
    data: DiscountTierCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not data.min_qty and not data.min_amount:
        raise HTTPException(400, "Harus isi minimal salah satu: min_qty atau min_amount")
    if data.discount_percent <= 0 or data.discount_percent > 100:
        raise HTTPException(400, "Diskon harus antara 0.1% - 100%")
    if data.start_date and data.end_date and data.start_date > data.end_date:
        raise HTTPException(400, "Tanggal mulai tidak boleh setelah tanggal selesai")

    # Validasi item & kategori
    if data.item_id:
        if not db.query(models.Item).get(data.item_id):
            raise HTTPException(404, "Item tidak ditemukan")
    if data.category_id:
        if not db.query(models.Category).get(data.category_id):
            raise HTTPException(404, "Kategori tidak ditemukan")

    tier = models.DiscountTier(**data.model_dump())
    db.add(tier); db.commit(); db.refresh(tier)
    write_audit(db, current_user.id, "CREATE", "discount_tiers", tier.id,
                f"Buat diskon: {data.name} ({data.discount_percent}%)")
    db.commit()
    return {"id": tier.id, "message": "Diskon bertingkat dibuat"}


@router.put("/{tid}")
def update_discount(
    tid: int, data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    tier = db.query(models.DiscountTier).get(tid)
    if not tier: raise HTTPException(404, "Diskon tidak ditemukan")
    for k, v in data.items():
        if hasattr(tier, k): setattr(tier, k, v)
    db.commit()
    return {"message": "Diskon diperbarui"}


@router.delete("/{tid}")
def delete_discount(
    tid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    tier = db.query(models.DiscountTier).get(tid)
    if not tier: raise HTTPException(404, "Diskon tidak ditemukan")
    tier.is_active = False
    db.commit()
    return {"message": "Diskon dinonaktifkan"}


@router.post("/calculate")
def calculate_discount(data: dict, db: Session = Depends(get_db),
                        _=Depends(get_current_user)):
    """
    Hitung diskon terbaik yang applicable untuk item tertentu.
    Input: {item_id, qty, unit_price, category_id}
    Output: {discount_percent, discount_amount, final_price, applied_tier}
    """
    item_id = data.get("item_id")
    qty = float(data.get("qty", 1))
    unit_price = float(data.get("unit_price", 0))
    category_id = data.get("category_id")
    total = qty * unit_price
    today = date.today()

    # Cari semua diskon yang applicable
    q = db.query(models.DiscountTier).filter(
        models.DiscountTier.is_active == True,
        (models.DiscountTier.start_date == None) |
        (models.DiscountTier.start_date <= today),
        (models.DiscountTier.end_date == None) |
        (models.DiscountTier.end_date >= today)
    )

    tiers = q.all()
    best_discount = 0
    best_tier_name = None

    for tier in tiers:
        # Cek apakah tier berlaku untuk item ini
        item_match = (
            tier.item_id is None and tier.category_id is None  # berlaku semua
        ) or (
            tier.item_id == item_id  # berlaku spesifik item
        ) or (
            tier.category_id == category_id and tier.item_id is None  # berlaku kategori
        )
        if not item_match:
            continue

        # Cek threshold
        qty_ok = tier.min_qty is None or qty >= tier.min_qty
        amount_ok = tier.min_amount is None or total >= tier.min_amount
        if not (qty_ok and amount_ok):
            continue

        # Ambil diskon terbesar
        if tier.discount_percent > best_discount:
            best_discount = tier.discount_percent
            best_tier_name = tier.name

    discount_amount = total * (best_discount / 100)
    final_price = total - discount_amount

    return {
        "discount_percent": best_discount,
        "discount_amount": discount_amount,
        "final_price": final_price,
        "applied_tier": best_tier_name,
        "original_total": total
    }
