"""Sinkronisasi katalog barang global ke stok lokal setiap gudang."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy.orm import Session

from .. import models


def ensure_item_in_all_warehouses(
    db: Session,
    item_id: int,
    *,
    stock_overrides: Mapping[int, float] | None = None,
) -> int:
    """Tambahkan satu barang ke semua gudang tanpa mengubah stok yang ada."""
    db.flush()
    warehouse_ids = [row[0] for row in db.query(models.Warehouse.id).all()]
    if not warehouse_ids:
        return 0

    existing_ids = {
        row[0]
        for row in db.query(models.WarehouseStock.warehouse_id).filter(
            models.WarehouseStock.item_id == item_id,
            models.WarehouseStock.warehouse_id.in_(warehouse_ids),
        ).all()
    }
    overrides = stock_overrides or {}
    missing_ids = [warehouse_id for warehouse_id in warehouse_ids if warehouse_id not in existing_ids]
    db.add_all([
        models.WarehouseStock(
            warehouse_id=warehouse_id,
            item_id=item_id,
            stock=float(overrides.get(warehouse_id, 0.0)),
        )
        for warehouse_id in missing_ids
    ])
    return len(missing_ids)


def register_new_item_in_all_warehouses(
    db: Session,
    item_id: int,
    *,
    warehouse_ids: Iterable[int] | None = None,
    stock_overrides: Mapping[int, float] | None = None,
) -> int:
    """Daftarkan barang yang baru dibuat ke semua gudang secara efisien."""
    db.flush()
    target_ids = (
        list(warehouse_ids)
        if warehouse_ids is not None
        else [row[0] for row in db.query(models.Warehouse.id).all()]
    )
    overrides = stock_overrides or {}
    db.add_all([
        models.WarehouseStock(
            warehouse_id=warehouse_id,
            item_id=item_id,
            stock=float(overrides.get(warehouse_id, 0.0)),
        )
        for warehouse_id in target_ids
    ])
    return len(target_ids)


def ensure_all_items_in_warehouse(db: Session, warehouse_id: int) -> int:
    """Tambahkan seluruh master barang ke satu gudang dengan stok awal nol."""
    db.flush()
    item_ids = [row[0] for row in db.query(models.Item.id).all()]
    if not item_ids:
        return 0

    existing_ids = {
        row[0]
        for row in db.query(models.WarehouseStock.item_id).filter(
            models.WarehouseStock.warehouse_id == warehouse_id,
            models.WarehouseStock.item_id.in_(item_ids),
        ).all()
    }
    missing_ids = [item_id for item_id in item_ids if item_id not in existing_ids]
    db.add_all([
        models.WarehouseStock(warehouse_id=warehouse_id, item_id=item_id, stock=0.0)
        for item_id in missing_ids
    ])
    return len(missing_ids)


def backfill_catalog_stock_rows(db: Session) -> int:
    """Perbaiki semua pasangan gudang/barang yang hilang secara idempoten."""
    db.flush()
    warehouse_ids = [row[0] for row in db.query(models.Warehouse.id).all()]
    return sum(ensure_all_items_in_warehouse(db, warehouse_id) for warehouse_id in warehouse_ids)
