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
