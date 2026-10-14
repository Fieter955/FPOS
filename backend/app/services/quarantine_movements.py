"""Audit movements for stock that is already outside saleable inventory."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models


def record_quarantine_movement(
    db: Session,
    *,
    quarantine: models.InventoryQuarantine,
    qty: float,
    category: str,
    date_value: date,
    reference: str,
    quarantine_before: float,
    quarantine_after: float,
    notes: str | None = None,
) -> models.StockMovement:
    """Record a quarantine lifecycle event without moving saleable stock."""
    warehouse_stock = db.query(models.WarehouseStock.stock).filter(
        models.WarehouseStock.warehouse_id == quarantine.warehouse_id,
        models.WarehouseStock.item_id == quarantine.item_id,
    ).scalar() or 0.0
    branch_stock = db.query(func.coalesce(func.sum(models.WarehouseStock.stock), 0.0)).join(
        models.Warehouse,
        models.Warehouse.id == models.WarehouseStock.warehouse_id,
    ).filter(
        models.Warehouse.branch_id == quarantine.branch_id,
        models.WarehouseStock.item_id == quarantine.item_id,
    ).scalar() or 0.0
    balance_note = f"Saldo karantina {float(quarantine_before):g} -> {float(quarantine_after):g}; stok jual tetap"
    movement = models.StockMovement(
        date=date_value,
        item_id=quarantine.item_id,
        branch_id=quarantine.branch_id,
        warehouse_id=quarantine.warehouse_id,
        type=category,
        event_category=category,
        direction="neutral",
        source_type="inventory_quarantine",
        source_id=quarantine.id,
        qty=float(qty),
        qty_before=float(branch_stock),
        qty_after=float(branch_stock),
        warehouse_qty_before=float(warehouse_stock),
        warehouse_qty_after=float(warehouse_stock),
        reference=reference,
        notes=f"{notes.strip()}; {balance_note}" if notes and notes.strip() else balance_note,
    )
    db.add(movement)
    return movement
