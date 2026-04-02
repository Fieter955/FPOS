"""
iPos 5.0 — Multi Satuan dengan Konversi Harga Otomatis
Fitur:
  - Daftarkan konversi: 1 Lusin = 12 Pcs
  - Harga per satuan kecil auto-calculate
  - Di POS bisa pilih jual per lusin atau per pcs
  - Stok dikurangi proporsional sesuai satuan yang dijual
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

router = APIRouter()


class UnitConversionCreate(BaseModel):
    item_id: int
    unit_id: int           # satuan besar (Lusin)
    base_unit_id: int      # satuan kecil (Pcs)
    conversion_factor: float  # 1 lusin = 12 pcs
    buy_price: float = 0      # harga beli per satuan besar
    sell_price: float = 0     # harga jual per satuan besar


@router.get("/item/{item_id}")
def get_item_conversions(
    item_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """Ambil semua konversi satuan untuk item tertentu"""
    item = db.query(models.Item).get(item_id)
    if not item:
        raise HTTPException(404, "Item tidak ditemukan")

    convs = db.query(models.UnitConversion).filter(
        models.UnitConversion.item_id == item_id,
        models.UnitConversion.is_active == True
    ).all()

    result = []
    for c in convs:
        price_per_base = c.sell_price / c.conversion_factor if c.conversion_factor > 0 else 0
        buy_per_base = c.buy_price / c.conversion_factor if c.conversion_factor > 0 else 0
        result.append({
            "id": c.id,
            "unit_id": c.unit_id,
            "unit_name": c.unit.name if c.unit else "-",
            "unit_abbreviation": c.unit.abbreviation if c.unit else "-",
            "base_unit_id": c.base_unit_id,
            "base_unit_name": c.base_unit.name if c.base_unit else "-",
            "base_unit_abbreviation": c.base_unit.abbreviation if c.base_unit else "-",
            "conversion_factor": c.conversion_factor,
            "buy_price": c.buy_price,
            "sell_price": c.sell_price,
            "buy_price_per_base_unit": round(buy_per_base, 2),
            "sell_price_per_base_unit": round(price_per_base, 2),
            "label": f"1 {c.unit.name if c.unit else '?'} = {c.conversion_factor:.0f} {c.base_unit.name if c.base_unit else '?'}",
        })

    # Tambahkan satuan dasar item itu sendiri
    base_conversion = {
        "id": 0,
        "unit_id": item.unit_id,
        "unit_name": item.unit.name if item.unit else "Pcs",
        "unit_abbreviation": item.unit.abbreviation if item.unit else "pcs",
        "base_unit_id": item.unit_id,
        "base_unit_name": item.unit.name if item.unit else "Pcs",
        "base_unit_abbreviation": item.unit.abbreviation if item.unit else "pcs",
        "conversion_factor": 1,
        "buy_price": item.buy_price,
        "sell_price": item.sell_price,
        "buy_price_per_base_unit": item.buy_price,
        "sell_price_per_base_unit": item.sell_price,
        "label": f"Satuan dasar ({item.unit.name if item.unit else 'Pcs'})",
        "is_base": True,
    }

    return {"item": {"id": item.id, "name": item.name, "code": item.code},
            "conversions": [base_conversion] + result}


@router.get("/")
def get_all_conversions(
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    convs = db.query(models.UnitConversion).filter(
        models.UnitConversion.is_active == True
    ).offset(skip).limit(limit).all()

    return [{
        "id": c.id,
        "item_id": c.item_id,
        "item_name": c.item.name if c.item else "-",
        "item_code": c.item.code if c.item else "-",
        "unit_name": c.unit.name if c.unit else "-",
        "base_unit_name": c.base_unit.name if c.base_unit else "-",
        "conversion_factor": c.conversion_factor,
        "sell_price": c.sell_price,
        "sell_price_per_base": round(c.sell_price / c.conversion_factor, 2) if c.conversion_factor > 0 else 0,
    } for c in convs]


@router.post("/")
def create_conversion(
    data: UnitConversionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Validasi item
    item = db.query(models.Item).get(data.item_id)
    if not item:
        raise HTTPException(404, "Item tidak ditemukan")

    # Validasi units
    unit = db.query(models.Unit).get(data.unit_id)
    base_unit = db.query(models.Unit).get(data.base_unit_id)
    if not unit or not base_unit:
        raise HTTPException(404, "Satuan tidak ditemukan")

    if data.unit_id == data.base_unit_id:
        raise HTTPException(400, "Satuan besar dan kecil tidak boleh sama")

    if data.conversion_factor <= 0:
        raise HTTPException(400, "Faktor konversi harus lebih dari 0")

    # Cek duplikat
    existing = db.query(models.UnitConversion).filter(
        models.UnitConversion.item_id == data.item_id,
        models.UnitConversion.unit_id == data.unit_id,
        models.UnitConversion.is_active == True
    ).first()
    if existing:
        raise HTTPException(400, f"Konversi {unit.name} untuk item ini sudah ada")

    conv = models.UnitConversion(**data.model_dump())
    db.add(conv)
    db.commit()
    db.refresh(conv)

    write_audit(db, current_user.id, "CREATE", "unit_conversions", conv.id,
                f"Konversi {unit.name} → {base_unit.name} untuk {item.name}")
    db.commit()

    price_per_base = data.sell_price / data.conversion_factor
    return {
        "id": conv.id,
        "message": f"Konversi dibuat: 1 {unit.name} = {data.conversion_factor:.0f} {base_unit.name}",
        "sell_price_per_base": round(price_per_base, 2)
    }


@router.put("/{conv_id}")
def update_conversion(
    conv_id: int, data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    conv = db.query(models.UnitConversion).get(conv_id)
    if not conv:
        raise HTTPException(404, "Konversi tidak ditemukan")
    for k, v in data.items():
        if hasattr(conv, k):
            setattr(conv, k, v)
    db.commit()
    return {"message": "Konversi diperbarui"}


@router.delete("/{conv_id}")
def delete_conversion(
    conv_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    conv = db.query(models.UnitConversion).get(conv_id)
    if not conv:
        raise HTTPException(404, "Konversi tidak ditemukan")
    conv.is_active = False
    db.commit()
    return {"message": "Konversi dihapus"}


@router.post("/calculate")
def calculate_conversion(data: dict, db: Session = Depends(get_db),
                          _=Depends(get_current_user)):
    """
    Hitung harga dan stok berdasarkan satuan yang dipilih.
    Input: {item_id, unit_conversion_id, qty}
    Output: {unit_name, qty_in_base_unit, sell_price, total}
    """
    item_id = data.get("item_id")
    conv_id = data.get("unit_conversion_id", 0)
    qty = float(data.get("qty", 1))

    item = db.query(models.Item).get(item_id)
    if not item:
        raise HTTPException(404, "Item tidak ditemukan")

    if conv_id == 0:
        # Satuan dasar
        return {
            "unit_name": item.unit.name if item.unit else "Pcs",
            "unit_abbreviation": item.unit.abbreviation if item.unit else "pcs",
            "qty_in_base_unit": qty,
            "sell_price": item.sell_price,
            "total": item.sell_price * qty,
            "available_stock": item.stock,
            "enough_stock": item.stock >= qty,
        }

    conv = db.query(models.UnitConversion).get(conv_id)
    if not conv or conv.item_id != item_id:
        raise HTTPException(404, "Konversi tidak ditemukan")

    qty_in_base = qty * conv.conversion_factor
    return {
        "unit_name": conv.unit.name if conv.unit else "-",
        "unit_abbreviation": conv.unit.abbreviation if conv.unit else "-",
        "qty_in_base_unit": qty_in_base,
        "sell_price": conv.sell_price,
        "sell_price_per_base": round(conv.sell_price / conv.conversion_factor, 2),
        "total": conv.sell_price * qty,
        "available_stock": item.stock,
        "available_in_this_unit": item.stock / conv.conversion_factor,
        "enough_stock": item.stock >= qty_in_base,
    }
