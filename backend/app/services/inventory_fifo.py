"""
Inventory FIFO — pusat costing persediaan berbasis lapisan (StockBatch).

HPP saat jual ditentukan dari batch TERTUA yang masih punya sisa (qty_remaining),
urut received_date lalu id. Ini membuat HPP presisi per-supplier/per-pembelian
tanpa perlu SKU/barcode terpisah di POS, dan menggantikan metode last-purchase-price
lama (item.buy_price yang ditimpa tiap beli).

Invarian yang harus dijaga: untuk tiap (warehouse_id, item_id),
    Σ StockBatch.qty_remaining  ==  WarehouseStock.stock
Lihat assert_batches_reconciled / reconcile_report untuk mendeteksi drift.

CATATAN satuan: batch dilacak pada item STOK NYATA (induk untuk multi-satuan),
dalam SATUAN DASAR. qty yang dikonsumsi harus sudah dikonversi ke satuan dasar
(pakai get_required_stock_qty di pemanggil).
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models

EPS = 1e-9

# (batch | None, qty_diambil, unit_cost) — batch None = sisa tak tertutup lapisan
Allocation = Tuple[Optional[models.StockBatch], float, float]


def add_batch(
    db: Session,
    *,
    item_id: int,
    warehouse_id: int,
    qty: float,
    unit_cost: float,
    received_date: date,
    supplier_id: Optional[int] = None,
    purchase_item_id: Optional[int] = None,
) -> models.StockBatch:
    """Buat satu lapisan persediaan baru (saat barang diterima)."""
    qty = float(qty or 0)
    batch = models.StockBatch(
        item_id=item_id,
        warehouse_id=warehouse_id,
        supplier_id=supplier_id,
        purchase_item_id=purchase_item_id,
        unit_cost=float(unit_cost or 0),
        qty_received=qty,
        qty_remaining=qty,
        received_date=received_date,
    )
    db.add(batch)
    db.flush()
    return batch


def fallback_cost(db: Session, item_id: int) -> float:
    """Biaya cadangan bila ada stok tanpa lapisan (data lama / drift)."""
    item = db.query(models.Item).get(item_id)
    return float(item.buy_price or 0) if item else 0.0


def next_out_cost(db: Session, item_id: int, warehouse_id: int) -> Optional[float]:
    """Harga modal batch TERTUA yang masih ada — yaitu HPP baris jual berikutnya.
    Dipakai sebagai lantai anti-rugi (min_price) di POS. None bila tak ada batch."""
    b = (
        db.query(models.StockBatch)
        .filter(
            models.StockBatch.item_id == item_id,
            models.StockBatch.warehouse_id == warehouse_id,
            models.StockBatch.qty_remaining > EPS,
        )
        .order_by(models.StockBatch.received_date.asc(), models.StockBatch.id.asc())
        .first()
    )
    return float(b.unit_cost) if b else None


def consume_fifo(
    db: Session, *, item_id: int, warehouse_id: int, qty: float
) -> List[Allocation]:
    """Kurangi qty_remaining batch tertua dulu sebanyak `qty` (satuan dasar).
    Kembalikan daftar alokasi [(batch, qty, unit_cost)] untuk dicatat di
    SaleItemBatch dan menghitung COGS = Σ(qty × unit_cost).

    Bila lapisan tak cukup (stok lama belum berbatch / drift), sisa dialokasikan
    ke (None, sisa, fallback_cost) agar transaksi tidak gagal — guard rekonsiliasi
    akan menandai selisihnya.
    """
    qty = float(qty or 0)
    allocations: List[Allocation] = []
    remaining = qty
    if remaining <= EPS:
        return allocations

    batches = (
        db.query(models.StockBatch)
        .filter(
            models.StockBatch.item_id == item_id,
            models.StockBatch.warehouse_id == warehouse_id,
            models.StockBatch.qty_remaining > EPS,
        )
        .order_by(models.StockBatch.received_date.asc(), models.StockBatch.id.asc())
        .with_for_update()
        .all()
    )
    for b in batches:
        if remaining <= EPS:
            break
        take = min(float(b.qty_remaining), remaining)
        b.qty_remaining = float(b.qty_remaining) - take
        allocations.append((b, take, float(b.unit_cost)))
        remaining -= take

    if remaining > EPS:
        allocations.append((None, remaining, fallback_cost(db, item_id)))
    return allocations


def record_allocations(db: Session, *, sale_item_id: int, allocations: List[Allocation]) -> None:
    """Simpan alokasi batch yang dikonsumsi sebuah baris penjualan."""
    for batch, qty, unit_cost in allocations:
        if batch is None:
            continue  # sisa fallback tanpa lapisan — tak ada batch untuk dipulihkan
        db.add(
            models.SaleItemBatch(
                sale_item_id=sale_item_id,
                batch_id=batch.id,
                qty=float(qty),
                unit_cost=float(unit_cost),
            )
        )


def restore_allocations(db: Session, sale_item_id: int) -> None:
    """Kembalikan qty_remaining ke batch yang dulu dikonsumsi baris jual ini
    (untuk batal jual / retur jual), lalu hapus catatan alokasinya."""
    allocs = (
        db.query(models.SaleItemBatch)
        .filter(models.SaleItemBatch.sale_item_id == sale_item_id)
        .all()
    )
    for a in allocs:
        batch = db.query(models.StockBatch).with_for_update().get(a.batch_id)
        if batch:
            batch.qty_remaining = float(batch.qty_remaining) + float(a.qty)
        db.delete(a)


def reduce_batches_for_reversal(
    db: Session,
    *,
    item_id: int,
    warehouse_id: int,
    qty: float,
    prefer_purchase_item_id: Optional[int] = None,
) -> float:
    """Kurangi lapisan saat PEMBELIAN dibatalkan: utamakan batch dari pembelian
    itu, sisanya dari batch TERBARU (kebalikan FIFO). Kecukupan stok agregat sudah
    dicek pemanggil. Kembalikan sisa yang tak tertutup (idealnya ~0)."""
    remaining = float(qty or 0)

    if prefer_purchase_item_id:
        pref = (
            db.query(models.StockBatch)
            .filter(
                models.StockBatch.purchase_item_id == prefer_purchase_item_id,
                models.StockBatch.qty_remaining > EPS,
            )
            .with_for_update()
            .all()
        )
        for b in pref:
            if remaining <= EPS:
                break
            take = min(float(b.qty_remaining), remaining)
            b.qty_remaining -= take
            remaining -= take

    if remaining > EPS:
        others = (
            db.query(models.StockBatch)
            .filter(
                models.StockBatch.item_id == item_id,
                models.StockBatch.warehouse_id == warehouse_id,
                models.StockBatch.qty_remaining > EPS,
            )
            .order_by(models.StockBatch.received_date.desc(), models.StockBatch.id.desc())
            .with_for_update()
            .all()
        )
        for b in others:
            if remaining <= EPS:
                break
            take = min(float(b.qty_remaining), remaining)
            b.qty_remaining -= take
            remaining -= take

    return remaining


def total_remaining(db: Session, item_id: int, warehouse_id: int) -> float:
    return float(
        db.query(func.coalesce(func.sum(models.StockBatch.qty_remaining), 0.0))
        .filter(
            models.StockBatch.item_id == item_id,
            models.StockBatch.warehouse_id == warehouse_id,
        )
        .scalar()
        or 0.0
    )


def assert_batches_reconciled(db: Session, item_id: int, warehouse_id: int, tol: float = 1e-4) -> bool:
    """True bila Σ batch == WarehouseStock untuk (warehouse,item). Non-fatal:
    dipakai untuk laporan/diagnostik, BUKAN di jalur transaksi panas."""
    ws = (
        db.query(models.WarehouseStock.stock)
        .filter(
            models.WarehouseStock.warehouse_id == warehouse_id,
            models.WarehouseStock.item_id == item_id,
        )
        .scalar()
    )
    stock = float(ws or 0)
    return abs(total_remaining(db, item_id, warehouse_id) - stock) <= tol


def reconcile_report(db: Session, warehouse_id: Optional[int] = None, tol: float = 1e-4) -> List[dict]:
    """Daftar (warehouse, item) yang Σ batch.qty_remaining != WarehouseStock.stock
    (drift FIFO). Diagnostik saja — tidak mengubah data. Pakai untuk memantau
    kesehatan lapisan setelah migrasi/transaksi."""
    q = db.query(
        models.WarehouseStock.warehouse_id,
        models.WarehouseStock.item_id,
        models.WarehouseStock.stock,
    )
    if warehouse_id is not None:
        q = q.filter(models.WarehouseStock.warehouse_id == warehouse_id)

    drift: List[dict] = []
    for wid, iid, stock in q.all():
        batched = total_remaining(db, iid, wid)
        s = float(stock or 0)
        if abs(batched - s) > tol:
            drift.append({
                "warehouse_id": wid,
                "item_id": iid,
                "stock": round(s, 4),
                "batched": round(batched, 4),
                "diff": round(batched - s, 4),
            })
    return drift
