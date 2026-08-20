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
    source_inventory_line_id: Optional[int] = None,
) -> models.StockBatch:
    """Buat satu lapisan persediaan baru (saat barang diterima)."""
    qty = float(qty or 0)
    batch = models.StockBatch(
        item_id=item_id,
        warehouse_id=warehouse_id,
        supplier_id=supplier_id,
        purchase_item_id=purchase_item_id,
        source_inventory_line_id=source_inventory_line_id,
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


def restore_sale_return(
    db: Session,
    *,
    sale_item_id: int,
    qty: float,
    item_id: int,
    warehouse_id: int,
    fallback_cost: float = 0.0,
    received_date: Optional[date] = None,
) -> float:
    """Pulihkan `qty` (satuan dasar) ke lapisan untuk RETUR JUAL — bisa SEBAGIAN.

    Beda dari restore_allocations (yang memulihkan seluruh baris saat BATAL jual),
    retur sering hanya sebagian qty. Strategi: pulihkan dulu dari alokasi asli
    (SaleItemBatch) TERBARU dulu, agar barang kembali ke batch tempat ia diambil
    (received_date & biaya asli terjaga, urutan FIFO konsisten). Bila alokasi tak
    mencukupi (penjualan PRA-FIFO tanpa alokasi / porsi fallback), sisanya dibuatkan
    satu lapisan baru di `received_date` (default hari ini) bernilai `fallback_cost`
    supaya invarian Σ batch == stok tetap terjaga.

    Mengembalikan TOTAL biaya modal yang benar-benar dipulihkan (Σ qty × unit_cost lapisan
    asal + porsi fallback). Pemanggil memakai angka ini sebagai nilai COGS yang dibalik agar
    GL Persediaan/HPP tetap sama dengan ledger batch — pada retur SEBAGIAN lintas lapisan beda
    harga, rata-rata `buy_price` bisa meleset dari lapisan yang nyatanya dipulihkan (drift).
    """
    remaining = float(qty or 0)
    if remaining <= EPS:
        return 0.0
    biaya_dipulihkan = 0.0
    allocs = (
        db.query(models.SaleItemBatch)
        .filter(models.SaleItemBatch.sale_item_id == sale_item_id)
        .order_by(models.SaleItemBatch.id.desc())
        .all()
    )
    for a in allocs:
        if remaining <= EPS:
            break
        give = min(float(a.qty), remaining)
        batch = db.query(models.StockBatch).with_for_update().get(a.batch_id)
        if batch:
            batch.qty_remaining = float(batch.qty_remaining) + give
        # Biaya asal lapisan saat dikonsumsi (SaleItemBatch.unit_cost); fallback ke unit_cost batch.
        unit = a.unit_cost if a.unit_cost is not None else (float(batch.unit_cost) if batch else 0.0)
        biaya_dipulihkan += give * float(unit or 0)
        a.qty = float(a.qty) - give
        if a.qty <= EPS:
            db.delete(a)
        remaining -= give

    if remaining > EPS:
        add_batch(
            db,
            item_id=item_id,
            warehouse_id=warehouse_id,
            qty=remaining,
            unit_cost=fallback_cost,
            received_date=received_date or date.today(),
        )
        biaya_dipulihkan += remaining * float(fallback_cost or 0)

    return biaya_dipulihkan


def reduce_batches_for_reversal(
    db: Session,
    *,
    item_id: int,
    warehouse_id: int,
    qty: float,
    prefer_purchase_item_id: Optional[int] = None,
) -> Tuple[float, float]:
    """Kurangi lapisan saat barang KELUAR untuk pembalikan (batal beli / retur beli):
    utamakan batch dari pembelian itu, sisanya dari batch TERBARU (kebalikan FIFO).
    Kecukupan stok agregat sudah dicek pemanggil.

    Kembalikan (cost_consumed, leftover):
      • cost_consumed = Σ(qty_dipotong × unit_cost) — BIAYA MODAL nyata barang yg keluar,
        dipakai pemanggil untuk menilai persediaan & menghitung selisih harga retur.
      • leftover = sisa qty yang tak tertutup lapisan (idealnya ~0; >0 hanya bila ada drift)."""
    remaining = float(qty or 0)
    cost_consumed = 0.0

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
            cost_consumed += take * float(b.unit_cost or 0)
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
            cost_consumed += take * float(b.unit_cost or 0)
            remaining -= take

    return cost_consumed, remaining


def transfer_batches(
    db: Session,
    *,
    item_id: int,
    from_warehouse_id: int,
    to_warehouse_id: int,
    qty: float,
) -> None:
    """Pindahkan lapisan biaya antar gudang (TRANSFER stok): konsumsi FIFO di gudang
    asal lalu buat ulang lapisan identik (biaya/tanggal/supplier) di gudang tujuan,
    sehingga biaya & urutan FIFO ikut berpindah bersama barangnya dan invarian
    Σ batch == stok tetap terjaga di KEDUA gudang. Pemanggil tetap meng-update
    WarehouseStock kedua gudang seperti biasa (fungsi ini hanya menyentuh lapisan)."""
    allocs = consume_fifo(db, item_id=item_id, warehouse_id=from_warehouse_id, qty=qty)
    for batch, q, cost in allocs:
        add_batch(
            db,
            item_id=item_id,
            warehouse_id=to_warehouse_id,
            qty=q,
            unit_cost=cost,
            received_date=batch.received_date if batch else date.today(),
            supplier_id=batch.supplier_id if batch else None,
            purchase_item_id=batch.purchase_item_id if batch else None,
            source_inventory_line_id=batch.source_inventory_line_id if batch else None,
        )


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
