"""Serial Management API with globally unique serials and immutable history."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user, write_audit
from ..config import settings
from ..database import get_db
from ..permissions import has_permission, has_role
from ..services.serials import ACTIVE_STOCK_STATUSES, normalize_serial, receive_serials
from ..services.virtual_units import get_conversion_factor, get_stock_source_item, is_virtual_variant

router = APIRouter()


class SerialWarehouseList(BaseModel):
    warehouse_id: int
    serials: list[str] = Field(default_factory=list)


class SerialActivation(BaseModel):
    item_id: int
    warehouses: list[SerialWarehouseList] = Field(default_factory=list)
    preview_token: Optional[str] = None


def _require(db: Session, user: models.User, action: str):
    if not has_permission(db, user, "inventory.serial", action):
        raise HTTPException(403, "Akses Serial Management ditolak")


SERIAL_OPTION_PURPOSES = {
    "sale": ("sales.cashier", "create", {"in_stock"}),
    "sale_return": ("sales.return", "create", {"sold"}),
    "purchase_receive": ("purchase.transaction", "create", set()),
    "purchase_return": ("purchase.return", "create", {"in_stock", "quarantine"}),
    "purchase_return_unlinked": ("purchase.return", "create", {"in_stock", "quarantine"}),
    "trade_in_sale": ("sales.trade_in", "create", {"in_stock"}),
    "trade_in_return": ("sales.trade_in", "create", {"sold"}),
    "warehouse_transfer": ("inventory.transfer", "create", {"in_stock", "quarantine"}),
    "assembly_material": ("assembly.transaction", "create", {"in_stock"}),
    "assembly_result": ("assembly.finished_goods", "create", set()),
    "inventory_item_out": ("inventory.item_out", "create", {"in_stock", "quarantine"}),
    "inventory_quarantine": ("inventory.item_out", "create", {"in_stock"}),
    "inventory_quarantine_resolution": ("inventory.item_out", "update", {"quarantine"}),
}


@router.get("/options")
def transaction_serial_options(
    purpose: str,
    item_id: int = Query(..., ge=1),
    source_line_id: Optional[int] = Query(None, ge=1),
    quarantine_id: Optional[int] = None,
    warehouse_id: Optional[int] = Query(None, ge=1),
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Return serial choices scoped to the permission of a transaction flow."""
    rule = SERIAL_OPTION_PURPOSES.get(purpose)
    if not rule:
        raise HTTPException(400, "Tujuan pemilihan serial tidak valid")
    permission, action, statuses = rule
    if not has_permission(db, user, permission, action):
        raise HTTPException(403, "Akses memilih serial untuk transaksi ini ditolak")

    entered_item = db.query(models.Item).get(item_id)
    if not entered_item or not entered_item.is_active:
        raise HTTPException(404, "Barang tidak ditemukan atau nonaktif")
    serial_item = get_stock_source_item(db, entered_item)
    factor = get_conversion_factor(entered_item) if is_virtual_variant(entered_item) else 1.0
    tracking_required = bool(serial_item.tracks_serial)
    active_branch_id = user.active_branch_id or user.branch_id

    source_serial_keys: set[str] | None = None
    if purpose in {"sale_return", "purchase_return"}:
        if not source_line_id:
            raise HTTPException(400, "Pilih faktur asal sebelum memilih serial")
        if purpose == "sale_return":
            source_line = db.query(models.SaleItem).join(models.Sale).filter(
                models.SaleItem.id == source_line_id,
                models.Sale.branch_id == active_branch_id,
            ).first()
            source_document_id = source_line.sale_id if source_line else None
            source_reference_type = "sale"
        else:
            source_line = db.query(models.PurchaseItem).join(models.Purchase).filter(
                models.PurchaseItem.id == source_line_id,
                models.Purchase.branch_id == active_branch_id,
            ).first()
            source_document_id = source_line.purchase_id if source_line else None
            source_reference_type = "purchase"
        if not source_line:
            raise HTTPException(404, "Baris faktur asal tidak ditemukan")
        source_stock_item = get_stock_source_item(db, source_line.item)
        if source_stock_item.id != serial_item.id:
            raise HTTPException(400, "Barang tidak sesuai dengan baris faktur")
        source_serial_keys = {
            normalize_serial(value)
            for value in json.loads(source_line.serials_json or "[]")
            if str(value).strip()
        }
        if not source_serial_keys and purpose == "sale_return":
            same_item_lines = db.query(models.SaleItem.id).filter(
                models.SaleItem.sale_id == source_document_id,
                models.SaleItem.item_id == source_line.item_id,
            ).count()
            if same_item_lines == 1:
                source_serial_keys = {
                    value
                    for (value,) in db.query(models.ItemSerial.normalized_serial)
                    .join(models.ItemSerialEvent, models.ItemSerialEvent.serial_id == models.ItemSerial.id)
                    .filter(
                        models.ItemSerial.item_id == serial_item.id,
                        models.ItemSerialEvent.reference_type == source_reference_type,
                        models.ItemSerialEvent.reference_id == source_document_id,
                        models.ItemSerialEvent.to_status == "sold",
                    ).all()
                }
        if tracking_required and not source_serial_keys:
            raise HTTPException(409, "Asal serial pada faktur lama tidak dapat dipastikan")

    quarantine = None
    if quarantine_id is not None:
        if quarantine_id < 1:
            raise HTTPException(400, "Sumber karantina tidak valid")
        quarantine = db.query(models.InventoryQuarantine).filter(
            models.InventoryQuarantine.id == quarantine_id,
            models.InventoryQuarantine.branch_id == active_branch_id,
            models.InventoryQuarantine.qty_open > 0,
        ).first()
        if not quarantine:
            raise HTTPException(404, "Sumber karantina tidak ditemukan atau sudah selesai")
        if quarantine.item_id != serial_item.id:
            raise HTTPException(400, "Barang tidak sesuai dengan sumber karantina")
        if warehouse_id is not None and quarantine.warehouse_id != warehouse_id:
            raise HTTPException(400, "Gudang tidak sesuai dengan sumber karantina")
    elif purpose == "inventory_quarantine_resolution":
        raise HTTPException(400, "Sumber karantina wajib dipilih")

    rows = []
    has_more = False
    if tracking_required and statuses:
        q = db.query(models.ItemSerial).filter(
            models.ItemSerial.item_id == serial_item.id,
            models.ItemSerial.status.in_(statuses),
        )
        if source_serial_keys is not None:
            q = q.filter(models.ItemSerial.normalized_serial.in_(source_serial_keys))
        if quarantine is not None:
            q = q.join(
                models.ItemSerialEvent,
                models.ItemSerialEvent.serial_id == models.ItemSerial.id,
            ).filter(
                models.ItemSerialEvent.reference_type == "inventory_quarantine",
                models.ItemSerialEvent.reference_id == quarantine.id,
                models.ItemSerialEvent.to_status == "quarantine",
            )
        if active_branch_id:
            q = q.filter(models.ItemSerial.branch_id == active_branch_id)
        if warehouse_id is not None:
            warehouse = db.query(models.Warehouse).get(warehouse_id)
            if not warehouse or (
                active_branch_id and warehouse.branch_id != active_branch_id
            ):
                raise HTTPException(404, "Gudang tidak ditemukan pada cabang aktif")
            q = q.filter(models.ItemSerial.warehouse_id == warehouse_id)
        if search and search.strip():
            q = q.filter(
                models.ItemSerial.normalized_serial.contains(normalize_serial(search))
            )
        found = q.distinct().order_by(models.ItemSerial.serial.asc()).offset(skip).limit(limit + 1).all()
        has_more = len(found) > limit
        rows = found[:limit]

    return {
        "tracking_required": tracking_required,
        "entered_item_id": entered_item.id,
        "serial_item_id": serial_item.id,
        "serial_item_name": serial_item.name,
        "quantity_factor": factor,
        "statuses": sorted(statuses),
        "has_more": has_more,
        "items": [
            {
                "id": row.id,
                "serial": row.serial,
                "status": row.status,
                "warehouse_id": row.warehouse_id,
            }
            for row in rows
        ],
    }


def _snapshot(db: Session, user: models.User, item_id: int):
    item = db.query(models.Item).get(item_id)
    if not item or not item.is_active:
        raise HTTPException(404, "Barang tidak ditemukan atau nonaktif")
    rows = db.query(models.WarehouseStock, models.Warehouse).join(
        models.Warehouse, models.Warehouse.id == models.WarehouseStock.warehouse_id
    ).filter(
        models.WarehouseStock.item_id == item_id,
    ).order_by(models.Warehouse.id).all()
    payload = [{"warehouse_id": w.id, "warehouse_name": w.name, "stock": float(ws.stock or 0)} for ws, w in rows]
    raw = json.dumps({"item_id": item_id, "warehouses": payload}, sort_keys=True, separators=(",", ":"))
    token = hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return item, payload, token


@router.get("")
def list_serials(
    search: Optional[str] = None, item_id: Optional[int] = None,
    warehouse_id: Optional[int] = None, status: Optional[str] = None,
    skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user),
):
    _require(db, user, "view")
    q = db.query(models.ItemSerial).join(models.Item)
    if user.active_branch_id:
        q = q.filter(models.ItemSerial.branch_id == user.active_branch_id)
    if search:
        key = normalize_serial(search)
        q = q.filter(models.ItemSerial.normalized_serial.contains(key))
    if item_id:
        q = q.filter(models.ItemSerial.item_id == item_id)
    if warehouse_id:
        q = q.filter(models.ItemSerial.warehouse_id == warehouse_id)
    if status:
        q = q.filter(models.ItemSerial.status == status)
    return [{
        "id": row.id, "serial": row.serial, "normalized_serial": row.normalized_serial,
        "item_id": row.item_id, "item_code": row.item.code, "item_name": row.item.name,
        "status": row.status, "warehouse_id": row.warehouse_id, "branch_id": row.branch_id,
    } for row in q.order_by(models.ItemSerial.id.desc()).offset(skip).limit(limit).all()]


@router.get("/resolve")
def resolve_serial(value: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _require(db, user, "view")
    row = db.query(models.ItemSerial).filter(models.ItemSerial.normalized_serial == normalize_serial(value)).first()
    if not row or (user.active_branch_id and row.branch_id != user.active_branch_id):
        raise HTTPException(404, "Serial tidak ditemukan pada cabang aktif")
    return {"id": row.id, "serial": row.serial, "item_id": row.item_id, "item_code": row.item.code,
            "item_name": row.item.name, "status": row.status, "warehouse_id": row.warehouse_id}


@router.get("/{serial_id}/history")
def serial_history(serial_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _require(db, user, "view")
    row = db.query(models.ItemSerial).get(serial_id)
    if not row or (user.active_branch_id and row.branch_id != user.active_branch_id):
        raise HTTPException(404, "Serial tidak ditemukan pada cabang aktif")
    return [{
        "id": event.id, "event_type": event.event_type, "from_status": event.from_status,
        "to_status": event.to_status, "from_warehouse_id": event.from_warehouse_id,
        "to_warehouse_id": event.to_warehouse_id, "reference_type": event.reference_type,
        "reference_id": event.reference_id, "reference_number": event.reference_number,
        "notes": event.notes, "created_at": event.created_at,
    } for event in sorted(row.events, key=lambda value: value.id, reverse=True)]


@router.post("/reconciliations/preview")
def activation_preview(data: SerialActivation, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _require(db, user, "update")
    if not has_role(user, "admin"):
        raise HTTPException(403, "Aktivasi serial lintas cabang hanya dapat dilakukan admin")
    item, warehouses, token = _snapshot(db, user, data.item_id)
    if item.tracks_serial:
        raise HTTPException(400, "Pelacakan serial barang sudah aktif")
    blockers = []
    for row in warehouses:
        if abs(row["stock"] - round(row["stock"])) > 1e-9:
            blockers.append(f"{row['warehouse_name']}: stok pecahan {row['stock']:g}")
    return {"item_id": item.id, "item_code": item.code, "warehouses": warehouses,
            "preview_token": token, "can_apply": not blockers, "blockers": blockers}


@router.post("/reconciliations")
@router.post("/activate-item")
def activate_item(data: SerialActivation, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _require(db, user, "update")
    if not has_role(user, "admin"):
        raise HTTPException(403, "Aktivasi serial lintas cabang hanya dapat dilakukan admin")
    item, snapshot, token = _snapshot(db, user, data.item_id)
    if data.preview_token != token:
        raise HTTPException(409, "Stok berubah; jalankan preview aktivasi ulang")
    if item.tracks_serial:
        raise HTTPException(400, "Pelacakan serial barang sudah aktif")
    supplied = {row.warehouse_id: row.serials for row in data.warehouses}
    all_keys = []
    for state in snapshot:
        stock = state["stock"]
        if abs(stock - round(stock)) > 1e-9:
            raise HTTPException(400, f"Stok {item.name} pecahan dan tidak dapat diserialkan")
        serial_values = supplied.get(state["warehouse_id"], [])
        if len(serial_values) != int(round(stock)):
            raise HTTPException(400, f"Serial {state['warehouse_name']} harus tepat {int(round(stock))}")
        all_keys.extend(normalize_serial(value) for value in serial_values)
    if len(all_keys) != len(set(all_keys)):
        raise HTTPException(400, "Serial duplikat pada daftar aktivasi")
    item.tracks_serial = True
    for state in snapshot:
        warehouse = db.query(models.Warehouse).get(state["warehouse_id"])
        values = supplied.get(warehouse.id, [])
        if values:
            receive_serials(db, item=item, qty=state["stock"], serial_values=values,
                            warehouse=warehouse, reference_type="serial_activation",
                            reference_id=item.id, reference_number=item.code, user_id=user.id)
    write_audit(db, user.id, "UPDATE", "items", item.id, "Aktivasi Serial Management")
    db.commit()
    return {"message": "Pelacakan serial berhasil diaktifkan", "item_id": item.id, "serial_count": len(all_keys)}


@router.post("/deactivate-item")
def deactivate_item(data: SerialActivation, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _require(db, user, "update")
    if not has_role(user, "admin"):
        raise HTTPException(403, "Nonaktifkan serial hanya dapat dilakukan admin")
    item = db.query(models.Item).get(data.item_id)
    if not item or not item.tracks_serial:
        raise HTTPException(400, "Pelacakan serial barang tidak aktif")
    active = db.query(models.ItemSerial.id).filter(
        models.ItemSerial.item_id == item.id,
        models.ItemSerial.status.in_(ACTIVE_STOCK_STATUSES | {"in_transit"}),
    ).first()
    if active:
        raise HTTPException(409, "Serial masih berada dalam stok/transit; keluarkan seluruh stok dahulu")
    item.tracks_serial = False
    write_audit(db, user.id, "UPDATE", "items", item.id, "Nonaktifkan Serial Management")
    db.commit()
    return {"message": "Pelacakan serial dinonaktifkan", "item_id": item.id}
