"""Central serial-number invariants and append-only ledger operations."""
from __future__ import annotations

import math
import unicodedata
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models

ACTIVE_STOCK_STATUSES = {"in_stock", "quarantine"}
VALID_STATUSES = {
    "in_stock", "in_transit", "sold", "quarantine", "removed",
    "returned_supplier", "consumed",
}
EPS = 1e-9


def normalize_serial(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    if not normalized:
        raise HTTPException(400, "Nomor serial tidak boleh kosong")
    if len(normalized) > 255:
        raise HTTPException(400, "Nomor serial maksimal 255 karakter")
    return normalized


def assert_serial_quantity(item: models.Item, qty: float, serials: Iterable[str]) -> list[str]:
    values = [str(value or "").strip() for value in (serials or [])]
    if not item.tracks_serial:
        if values:
            raise HTTPException(400, f"{item.name} tidak mengaktifkan pelacakan serial")
        return []
    if not math.isfinite(float(qty)) or abs(float(qty) - round(float(qty))) > EPS:
        raise HTTPException(400, f"Jumlah {item.name} harus bilangan bulat karena memakai serial")
    normalized = [normalize_serial(value) for value in values]
    if len(normalized) != int(round(float(qty))):
        raise HTTPException(400, f"Jumlah serial {item.name} harus tepat {int(round(float(qty)))}")
    if len(normalized) != len(set(normalized)):
        raise HTTPException(400, f"Nomor serial {item.name} duplikat dalam transaksi")
    return normalized


def _append_event(
    db: Session, serial: models.ItemSerial, *, event_type: str,
    from_status: str | None, to_status: str, from_warehouse_id: int | None,
    to_warehouse_id: int | None, reference_type: str, reference_id: int | None,
    reference_number: str | None, user_id: int | None, notes: str | None = None,
):
    db.add(models.ItemSerialEvent(
        serial_id=serial.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
        reference_type=reference_type,
        reference_id=reference_id,
        reference_number=reference_number,
        notes=notes,
        created_by=user_id,
    ))


def receive_serials(
    db: Session, *, item: models.Item, qty: float, serial_values: Iterable[str],
    warehouse: models.Warehouse, reference_type: str, reference_id: int | None,
    reference_number: str | None, user_id: int | None, status: str = "in_stock",
):
    normalized = assert_serial_quantity(item, qty, serial_values)
    records = []
    for display, key in zip(serial_values, normalized):
        existing = db.query(models.ItemSerial).filter(models.ItemSerial.normalized_serial == key).with_for_update().first()
        if existing:
            raise HTTPException(409, f"Serial {display} sudah terdaftar secara global")
        record = models.ItemSerial(
            item_id=item.id, serial=str(display).strip(), normalized_serial=key,
            status=status, warehouse_id=warehouse.id, branch_id=warehouse.branch_id,
        )
        db.add(record)
        db.flush()
        _append_event(
            db, record, event_type="received", from_status=None, to_status=status,
            from_warehouse_id=None, to_warehouse_id=warehouse.id,
            reference_type=reference_type, reference_id=reference_id,
            reference_number=reference_number, user_id=user_id,
        )
        records.append(record)
    return records


def transition_serials(
    db: Session, *, item: models.Item, qty: float, serial_values: Iterable[str],
    from_statuses: set[str], to_status: str, from_warehouse_id: int | None,
    to_warehouse: models.Warehouse | None, reference_type: str,
    reference_id: int | None, reference_number: str | None, user_id: int | None,
    event_type: str,
):
    if to_status not in VALID_STATUSES:
        raise HTTPException(400, "Status serial tidak valid")
    normalized = assert_serial_quantity(item, qty, serial_values)
    records = []
    for display, key in zip(serial_values, normalized):
        record = db.query(models.ItemSerial).filter(models.ItemSerial.normalized_serial == key).with_for_update().first()
        if not record or record.item_id != item.id:
            raise HTTPException(404, f"Serial {display} tidak ditemukan untuk {item.name}")
        if record.status not in from_statuses:
            raise HTTPException(409, f"Serial {display} berstatus {record.status}, tidak dapat diproses")
        if from_warehouse_id is not None and record.warehouse_id != from_warehouse_id:
            raise HTTPException(409, f"Serial {display} tidak berada di gudang asal")
        old_status, old_warehouse = record.status, record.warehouse_id
        record.status = to_status
        record.warehouse_id = to_warehouse.id if to_warehouse else None
        if to_warehouse:
            record.branch_id = to_warehouse.branch_id
        _append_event(
            db, record, event_type=event_type, from_status=old_status, to_status=to_status,
            from_warehouse_id=old_warehouse,
            to_warehouse_id=record.warehouse_id, reference_type=reference_type,
            reference_id=reference_id, reference_number=reference_number, user_id=user_id,
        )
        records.append(record)
    return records


def assert_warehouse_serial_balance(db: Session, item: models.Item, warehouse_id: int, stock: float):
    if not item.tracks_serial:
        return
    if abs(float(stock) - round(float(stock))) > EPS:
        raise HTTPException(409, f"Stok {item.name} pecahan dan tidak dapat memakai serial")
    count = db.query(models.ItemSerial.id).filter(
        models.ItemSerial.item_id == item.id,
        models.ItemSerial.warehouse_id == warehouse_id,
        models.ItemSerial.status.in_(ACTIVE_STOCK_STATUSES),
    ).count()
    if count != int(round(float(stock))):
        raise HTTPException(409, f"Saldo serial {item.name} ({count}) tidak sama dengan stok ({stock:g})")


def reverse_reference_serials(
    db: Session, *, reference_type: str, reference_id: int,
    reversal_reference_number: str, user_id: int | None,
):
    """Reverse serial events for a document after validating no later movement occurred."""
    events = db.query(models.ItemSerialEvent).filter(
        models.ItemSerialEvent.reference_type == reference_type,
        models.ItemSerialEvent.reference_id == reference_id,
    ).order_by(models.ItemSerialEvent.id.desc()).all()
    grouped = {}
    for event in events:
        grouped.setdefault(event.serial_id, []).append(event)
    for serial_id, serial_events in grouped.items():
        serial = db.query(models.ItemSerial).filter(models.ItemSerial.id == serial_id).with_for_update().first()
        latest = db.query(models.ItemSerialEvent.id).filter(
            models.ItemSerialEvent.serial_id == serial_id
        ).order_by(models.ItemSerialEvent.id.desc()).scalar()
        newest = serial_events[0]
        if latest != newest.id or serial.status != newest.to_status or serial.warehouse_id != newest.to_warehouse_id:
            raise HTTPException(409, f"Serial {serial.serial} sudah bergerak setelah transaksi dan tidak dapat dibalik")
        for event in serial_events:
            old_status, old_warehouse = serial.status, serial.warehouse_id
            # Reversing initial receipt retains identity/history but removes it from stock.
            serial.status = event.from_status or "removed"
            serial.warehouse_id = event.from_warehouse_id
            target = db.query(models.Warehouse).get(event.from_warehouse_id) if event.from_warehouse_id else None
            if target:
                serial.branch_id = target.branch_id
            _append_event(
                db, serial, event_type="reversal", from_status=old_status,
                to_status=serial.status, from_warehouse_id=old_warehouse,
                to_warehouse_id=serial.warehouse_id, reference_type=f"{reference_type}_reversal",
                reference_id=reference_id, reference_number=reversal_reference_number,
                user_id=user_id,
            )
