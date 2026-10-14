"""Perubahan saldo gudang dan cache stok pusat dalam satu operasi."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models


PUSAT_BRANCH_ID = 1


@dataclass(frozen=True)
class HasilPerubahanStok:
    gudang_sebelum: float
    gudang_sesudah: float
    cabang_sebelum: float
    cabang_sesudah: float


def ubah_stok_gudang(
    db: Session,
    *,
    warehouse_id: int,
    item_id: int,
    delta: float,
) -> HasilPerubahanStok:
    """Ubah stok gudang dan sinkronkan cache ``Item.stock`` untuk cabang pusat.

    Fungsi ini sengaja tidak melakukan commit. Pemanggil tetap bertanggung jawab
    atas FIFO, jurnal, serial, dan commit dalam transaksi yang sama.
    """
    warehouse = (
        db.query(models.Warehouse)
        .filter(models.Warehouse.id == warehouse_id)
        .with_for_update()
        .first()
    )
    if not warehouse:
        raise ValueError(f"Gudang {warehouse_id} tidak ditemukan")

    # Session aplikasi memakai autoflush=False. Pastikan perubahan sebelumnya
    # dalam transaksi yang sama ikut terbaca oleh snapshot saldo cabang.
    db.flush()
    cabang_sebelum = float(
        db.query(func.coalesce(func.sum(models.WarehouseStock.stock), 0.0))
        .join(models.Warehouse, models.Warehouse.id == models.WarehouseStock.warehouse_id)
        .filter(
            models.Warehouse.branch_id == warehouse.branch_id,
            models.WarehouseStock.item_id == item_id,
        )
        .scalar()
        or 0.0
    )
    row = (
        db.query(models.WarehouseStock)
        .filter(
            models.WarehouseStock.warehouse_id == warehouse_id,
            models.WarehouseStock.item_id == item_id,
        )
        .with_for_update()
        .first()
    )
    if not row:
        row = models.WarehouseStock(warehouse_id=warehouse_id, item_id=item_id, stock=0.0)
        db.add(row)
        db.flush()

    gudang_sebelum = float(row.stock or 0.0)
    perubahan = float(delta or 0.0)
    gudang_sesudah = gudang_sebelum + perubahan
    row.stock = gudang_sesudah

    if warehouse.branch_id == PUSAT_BRANCH_ID:
        item = (
            db.query(models.Item)
            .filter(models.Item.id == item_id)
            .with_for_update()
            .first()
        )
        if not item:
            raise ValueError(f"Barang {item_id} tidak ditemukan")
        item.stock = float(item.stock or 0.0) + perubahan

    return HasilPerubahanStok(
        gudang_sebelum=gudang_sebelum,
        gudang_sesudah=gudang_sesudah,
        cabang_sebelum=cabang_sebelum,
        cabang_sesudah=cabang_sebelum + perubahan,
    )
