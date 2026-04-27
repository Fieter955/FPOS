"""
iPos 5.0 - Multi Satuan Virtual

Konsep:
  - Barang turunan tetap muncul sebagai barang baru/SKU baru.
  - Stok barang turunan tidak disimpan sendiri, tetapi membaca stok induk.
  - Saat barang turunan dijual, stok fisik barang induk yang berkurang.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user, write_audit
from ..database import get_db
from ..services.virtual_units import (
    get_effective_buy_price,
    get_effective_stock_from_source,
    get_required_stock_qty,
    get_stock_source_item,
    is_virtual_variant,
)

router = APIRouter()


class VirtualVariantPayload(BaseModel):
    source_item_id: int
    child_name: str
    child_unit_id: int
    conversion_factor: float = Field(..., gt=0)
    sell_price: float = Field(0, ge=0)
    child_code: Optional[str] = None
    barcode: Optional[str] = None


def _normalize_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _get_branch_visible_stock(
    db: Session,
    item: models.Item,
    current_user: models.User,
) -> float:
    branch_id = current_user.active_branch_id
    if not branch_id:
        return float(item.stock or 0)

    gudang = db.query(models.Warehouse.id).filter(
        models.Warehouse.branch_id == branch_id
    ).first()
    if not gudang:
        return 0.0

    return float(
        db.query(models.WarehouseStock.stock).filter(
            models.WarehouseStock.warehouse_id == gudang[0],
            models.WarehouseStock.item_id == item.id,
        ).scalar()
        or 0
    )


def _serialize_source_item(
    db: Session,
    item: models.Item,
    current_user: models.User,
):
    stock = _get_branch_visible_stock(db, item, current_user)
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "barcode": item.barcode,
        "unit_id": item.unit_id,
        "unit_name": item.unit.name if item.unit else "-",
        "unit_abbreviation": item.unit.abbreviation if item.unit else "-",
        "buy_price": float(item.buy_price or 0),
        "sell_price": float(item.sell_price or 0),
        "stock": round(stock, 4),
        "category_id": item.category_id,
        "is_virtual_variant": bool(item.is_virtual_variant),
    }


def _serialize_variant(
    db: Session,
    conv: models.UnitConversion,
    current_user: models.User,
):
    source_item = db.query(models.Item).get(conv.item_id)
    child_item = db.query(models.Item).get(conv.child_item_id) if conv.child_item_id else None
    if not source_item or not child_item or not child_item.is_active:
        return None

    source_stock = _get_branch_visible_stock(db, source_item, current_user)
    child_buy_price = get_effective_buy_price(db, child_item, item_map={source_item.id: source_item})
    child_stock = get_effective_stock_from_source(child_item, source_stock)
    margin_percent = 0.0
    if child_buy_price > 0:
        margin_percent = ((float(child_item.sell_price or 0) - child_buy_price) / child_buy_price) * 100

    return {
        "conversion_id": conv.id,
        "source_item_id": source_item.id,
        "source_item_name": source_item.name,
        "source_unit_name": source_item.unit.name if source_item.unit else "-",
        "source_unit_abbreviation": source_item.unit.abbreviation if source_item.unit else "-",
        "source_stock": round(source_stock, 4),
        "child_item_id": child_item.id,
        "child_code": child_item.code,
        "child_name": child_item.name,
        "child_barcode": child_item.barcode,
        "child_unit_id": child_item.unit_id,
        "child_unit_name": child_item.unit.name if child_item.unit else "-",
        "child_unit_abbreviation": child_item.unit.abbreviation if child_item.unit else "-",
        "conversion_factor": float(conv.conversion_factor or 0),
        "buy_price_auto": round(child_buy_price, 4),
        "sell_price": float(child_item.sell_price or 0),
        "margin_percent": round(margin_percent, 2),
        "available_stock": round(child_stock, 4),
        "required_parent_qty_per_unit": round(get_required_stock_qty(child_item, 1), 4),
    }


def _generate_unique_child_code(
    db: Session,
    source_item: models.Item,
    child_unit: models.Unit,
    requested_code: Optional[str] = None,
    exclude_item_id: Optional[int] = None,
) -> str:
    if requested_code:
        code = requested_code.strip().upper()
        q = db.query(models.Item).filter(models.Item.code == code)
        if exclude_item_id:
            q = q.filter(models.Item.id != exclude_item_id)
        if q.first():
            raise HTTPException(400, f"Kode barang {code} sudah dipakai")
        return code

    unit_code = _normalize_text(child_unit.abbreviation) or _normalize_text(child_unit.name) or "SAT"
    unit_code = "".join(ch for ch in unit_code.upper() if ch.isalnum())[:8] or "SAT"
    base_code = f"{source_item.code}-{unit_code}"
    code = base_code
    suffix = 2

    while True:
        q = db.query(models.Item).filter(models.Item.code == code)
        if exclude_item_id:
            q = q.filter(models.Item.id != exclude_item_id)
        if not q.first():
            return code
        code = f"{base_code}-{suffix}"
        suffix += 1


def _ensure_source_item(db: Session, item_id: int) -> models.Item:
    item = db.query(models.Item).get(item_id)
    if not item or not item.is_active:
        raise HTTPException(404, "Barang induk tidak ditemukan")
    if is_virtual_variant(item):
        raise HTTPException(400, "Pilih barang induk asli, bukan barang multi-satuan turunan")
    if not item.unit_id:
        raise HTTPException(400, "Barang induk belum punya satuan dasar")
    return item


def _ensure_child_unit(db: Session, unit_id: int) -> models.Unit:
    unit = db.query(models.Unit).get(unit_id)
    if not unit:
        raise HTTPException(404, "Satuan turunan tidak ditemukan")
    return unit


def _sync_child_barcode(db: Session, child_item: models.Item):
    if child_item.barcode:
        return

    from .barcode_gen import _generate_barcode_value

    child_item.barcode = _generate_barcode_value(child_item.code, "CODE128")
    existing = db.query(models.BarcodeLabel).filter(
        models.BarcodeLabel.item_id == child_item.id
    ).first()
    if not existing:
        db.add(models.BarcodeLabel(
            item_id=child_item.id,
            barcode_value=child_item.barcode,
            barcode_type="CODE128",
            label_text=child_item.name[:30],
        ))


@router.get("/source-items")
def search_source_items(
    search: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.Item).filter(
        models.Item.is_active == True,
        (models.Item.is_virtual_variant == False) | (models.Item.is_virtual_variant == None),
    )

    if search:
        q = q.filter(
            models.Item.name.ilike(f"%{search}%")
            | models.Item.code.ilike(f"%{search}%")
            | models.Item.barcode.ilike(f"%{search}%")
        )

    items = q.order_by(models.Item.name.asc()).limit(limit).all()
    return [_serialize_source_item(db, item, current_user) for item in items]


@router.get("/config/{source_item_id}")
def get_virtual_unit_config(
    source_item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    source_item = _ensure_source_item(db, source_item_id)
    convs = db.query(models.UnitConversion).filter(
        models.UnitConversion.item_id == source_item_id,
        models.UnitConversion.child_item_id != None,
        models.UnitConversion.is_active == True,
    ).order_by(models.UnitConversion.id.asc()).all()

    rows = []
    for conv in convs:
        row = _serialize_variant(db, conv, current_user)
        if row:
            rows.append(row)

    units = db.query(models.Unit).order_by(models.Unit.name.asc()).all()
    return {
        "source_item": _serialize_source_item(db, source_item, current_user),
        "units": [
            {
                "id": unit.id,
                "name": unit.name,
                "abbreviation": unit.abbreviation,
            }
            for unit in units
        ],
        "rows": rows,
    }


@router.post("/variant")
def create_virtual_variant(
    data: VirtualVariantPayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    source_item = _ensure_source_item(db, data.source_item_id)
    child_unit = _ensure_child_unit(db, data.child_unit_id)

    if child_unit.id == source_item.unit_id:
        raise HTTPException(400, "Satuan turunan harus berbeda dari satuan induk")

    duplicate = db.query(models.UnitConversion).filter(
        models.UnitConversion.item_id == source_item.id,
        models.UnitConversion.base_unit_id == child_unit.id,
        models.UnitConversion.is_active == True,
    ).first()
    if duplicate:
        raise HTTPException(400, f"Multi satuan {child_unit.name} untuk barang ini sudah ada")

    child_name = _normalize_text(data.child_name)
    if not child_name:
        raise HTTPException(400, "Nama barang turunan wajib diisi")

    child_code = _generate_unique_child_code(
        db,
        source_item=source_item,
        child_unit=child_unit,
        requested_code=data.child_code,
    )
    child_buy_price = float(source_item.buy_price or 0) * float(data.conversion_factor)
    suggested_sell_price = (
        float(data.sell_price)
        if data.sell_price > 0
        else (float(source_item.sell_price or 0) * float(data.conversion_factor))
    )

    child_item = models.Item(
        code=child_code,
        name=child_name,
        category_id=source_item.category_id,
        unit_id=child_unit.id,
        buy_price=child_buy_price,
        sell_price=suggested_sell_price,
        stock=0,
        min_stock=0,
        description=source_item.description,
        barcode=_normalize_text(data.barcode) or None,
        parent_item_id=source_item.id,
        conversion_factor_to_parent=float(data.conversion_factor),
        is_virtual_variant=True,
        is_active=True,
    )
    db.add(child_item)
    db.flush()
    _sync_child_barcode(db, child_item)

    conv = models.UnitConversion(
        item_id=source_item.id,
        child_item_id=child_item.id,
        unit_id=source_item.unit_id,
        base_unit_id=child_unit.id,
        conversion_factor=float(data.conversion_factor),
        buy_price=child_buy_price,
        sell_price=suggested_sell_price,
        is_active=True,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)

    write_audit(
        db,
        current_user.id,
        "CREATE",
        "unit_conversions",
        conv.id,
        f"Multi satuan virtual dibuat untuk {source_item.name} -> {child_item.name}",
    )
    db.commit()

    row = _serialize_variant(db, conv, current_user)
    return {"message": "Barang multi-satuan berhasil dibuat", "row": row}


@router.put("/variant/{conv_id}")
def update_virtual_variant(
    conv_id: int,
    data: VirtualVariantPayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conv = db.query(models.UnitConversion).get(conv_id)
    if not conv or not conv.is_active or not conv.child_item_id:
        raise HTTPException(404, "Multi satuan tidak ditemukan")

    source_item = _ensure_source_item(db, conv.item_id)
    if source_item.id != data.source_item_id:
        raise HTTPException(400, "Barang induk tidak boleh diganti dari baris yang sudah ada")

    child_item = db.query(models.Item).get(conv.child_item_id)
    if not child_item:
        raise HTTPException(404, "Barang turunan tidak ditemukan")

    child_unit = _ensure_child_unit(db, data.child_unit_id)
    if child_unit.id == source_item.unit_id:
        raise HTTPException(400, "Satuan turunan harus berbeda dari satuan induk")

    duplicate = db.query(models.UnitConversion).filter(
        models.UnitConversion.item_id == source_item.id,
        models.UnitConversion.base_unit_id == child_unit.id,
        models.UnitConversion.is_active == True,
        models.UnitConversion.id != conv.id,
    ).first()
    if duplicate:
        raise HTTPException(400, f"Multi satuan {child_unit.name} untuk barang ini sudah ada")

    child_name = _normalize_text(data.child_name)
    if not child_name:
        raise HTTPException(400, "Nama barang turunan wajib diisi")

    child_code = _generate_unique_child_code(
        db,
        source_item=source_item,
        child_unit=child_unit,
        requested_code=data.child_code or child_item.code,
        exclude_item_id=child_item.id,
    )
    child_item.name = child_name
    child_item.code = child_code
    child_item.unit_id = child_unit.id
    child_item.parent_item_id = source_item.id
    child_item.conversion_factor_to_parent = float(data.conversion_factor)
    child_item.is_virtual_variant = True
    child_item.sell_price = float(data.sell_price or 0)
    child_item.buy_price = get_effective_buy_price(db, child_item, item_map={source_item.id: source_item})
    if _normalize_text(data.barcode):
        child_item.barcode = _normalize_text(data.barcode)
    else:
        child_item.barcode = None
        _sync_child_barcode(db, child_item)

    conv.unit_id = source_item.unit_id
    conv.base_unit_id = child_unit.id
    conv.conversion_factor = float(data.conversion_factor)
    conv.buy_price = float(child_item.buy_price or 0)
    conv.sell_price = float(child_item.sell_price or 0)

    db.commit()

    write_audit(
        db,
        current_user.id,
        "UPDATE",
        "unit_conversions",
        conv.id,
        f"Multi satuan virtual diperbarui untuk {source_item.name} -> {child_item.name}",
    )
    db.commit()

    row = _serialize_variant(db, conv, current_user)
    return {"message": "Barang multi-satuan berhasil diperbarui", "row": row}


@router.delete("/variant/{conv_id}")
def delete_virtual_variant(
    conv_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conv = db.query(models.UnitConversion).get(conv_id)
    if not conv or not conv.is_active:
        raise HTTPException(404, "Multi satuan tidak ditemukan")

    child_item = db.query(models.Item).get(conv.child_item_id) if conv.child_item_id else None
    conv.is_active = False
    if child_item:
        child_item.is_active = False

    db.commit()
    write_audit(
        db,
        current_user.id,
        "DELETE",
        "unit_conversions",
        conv.id,
        f"Multi satuan virtual dinonaktifkan (conversion #{conv.id})",
    )
    db.commit()
    return {"message": "Barang multi-satuan dinonaktifkan"}
