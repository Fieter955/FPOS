"""
iPos 5.0 — Multi Gudang
- CRUD gudang
- Stok per gudang
- Transfer antar gudang
- Laporan stok per gudang
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date, datetime
from pydantic import BaseModel, Field
from typing import List
import pytz

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models
from ..services.item_catalog_sync import ensure_all_items_in_warehouse
from ..permissions import has_permission
from ..routes.accounting import assert_books_open, next_journal_number
from ..services.virtual_units import get_conversion_factor, get_stock_source_item, is_virtual_variant
from ..services.serials import reverse_reference_serials, transition_serials

router = APIRouter()
WITA = pytz.timezone("Asia/Makassar")

from sqlalchemy import func # 👈 Pastikan ini di-import di paling atas

# 👇 1. TAMBAHKAN FUNGSI HELPER INI DI ATAS (Di bawah def adjust_warehouse_stock)
def get_total_branch_stock(db: Session, branch_id: int, item_id: int) -> float:
    """Menghitung total fisik gabungan dari semua gudang di dalam 1 cabang"""
    total = db.query(func.sum(models.WarehouseStock.stock)).join(models.Warehouse).filter(
        models.Warehouse.branch_id == branch_id,
        models.WarehouseStock.item_id == item_id
    ).scalar()
    return float(total or 0.0)

def next_transfer_number(db: Session, transfer_date: Optional[date] = None) -> str:
    today = (transfer_date or datetime.now(WITA).date()).strftime("%Y%m%d")
    prefix = f"TR{today}"
    last = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.number.like(f"{prefix}%")
    ).order_by(models.WarehouseTransfer.id.desc()).with_for_update().first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"

def get_warehouse_stock(db: Session, warehouse_id: int, item_id: int) -> float:
    ws = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id
    ).with_for_update().first()
    return ws.stock if ws else 0.0

def adjust_warehouse_stock(db: Session, warehouse_id: int, item_id: int,
                            delta: float, commit: bool = False):
    ws = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id
    ).with_for_update().first()
    if ws:
        ws.stock += delta
    else:
        ws = models.WarehouseStock(
            warehouse_id=warehouse_id, item_id=item_id, stock=delta
        )
        db.add(ws)
    if commit:
        db.commit()

# ─── Warehouses CRUD ──────────────────────────────────────────────────────────
# =====================================================================
# 1. TIMPA FUNGSI get_warehouses (Fix Bug Transfer Lintas Cabang)
# =====================================================================
# warehouse.py

@router.get("/")
def get_warehouses(all_branches: bool = False, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    q = db.query(models.Warehouse).filter(models.Warehouse.is_active == True)
    
    if not all_branches and current_user.active_branch_id:
        q = q.filter(models.Warehouse.branch_id == current_user.active_branch_id)
        
    warehouses = q.order_by(models.Warehouse.id).all()
    
    return [{
        "id": w.id, 
        "code": w.code, 
        "name": w.name,
        "branch_id": w.branch_id,  # <--- TAMBAHKAN INI agar frontend bisa memfilter
        "address": w.address, 
        "is_default": w.is_default,
        "is_active": w.is_active,
        "branch_name": w.branch.name if w.branch else "-",
        "total_items": len(w.stock_items),
        "total_stock_value": sum(
            (ws.stock * (ws.item.buy_price if ws.item else 0))
            for ws in w.stock_items
        )
    } for w in warehouses]


# 👇 TAMBAHKAN KODE INI TEPAT DI SINI (Di atas fungsi create_warehouse atau update_warehouse) 👇
# =====================================================================
# ENDPOINT BARU: REKAP TOTAL FISIK BARANG DI SELURUH GUDANG CABANG INI
# =====================================================================
@router.get("/branch-inventory-summary")
def get_branch_inventory_summary(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    b_id = current_user.active_branch_id
    if not b_id:
        return [] # Jika tidak ada cabang aktif

    # Ambil semua barang yang masih aktif di master data
    items = db.query(models.Item).filter(models.Item.is_active == True).all()
    
    results = []
    for item in items:
        # Gunakan helper penghitung gabungan rak yang sudah kita buat sebelumnya
        total_stok = get_total_branch_stock(db, b_id, item.id)
        
        # Hanya tampilkan barang yang stoknya ada (lebih dari 0 atau minus)
        if total_stok != 0:
            results.append({
                "code": item.code or "-",
                "name": item.name,
                "total_stock": total_stok
            })
            
    # Urutkan berdasarkan nama barang sesuai abjad
    results.sort(key=lambda x: x["name"])
    
    return results


# =====================================================================
# 2. TIMPA FUNGSI create_warehouse (Fix Bug Salah Cabang)
# =====================================================================
@router.post("/")
def create_warehouse(data: dict, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Nama gudang wajib diisi")

    # 🛡️ FIX BUG 1: Ambil Cabang dari Pilihan Dropdown Form dulu!
    b_id = data.get("branch_id")
    
    # Jika dropdown kosong, baru pakai cabang aktif user
    if not b_id:
        b_id = current_user.active_branch_id
        if not b_id:
            pusat = db.query(models.Branch).filter(models.Branch.code == "HQ-01").first()
            if not pusat:
                raise HTTPException(400, "Sistem belum memiliki Cabang Pusat.")
            b_id = pusat.id

    auto_code = f"WH-{uuid.uuid4().hex[:4].upper()}"
    while db.query(models.Warehouse).filter(models.Warehouse.code == auto_code).first():
        auto_code = f"WH-{uuid.uuid4().hex[:4].upper()}"

    is_first = db.query(models.Warehouse).filter(models.Warehouse.branch_id == b_id).count() == 0

    w = models.Warehouse(
        code=auto_code, 
        name=name,
        address=data.get("address"),
        is_default=data.get("is_default", is_first) or is_first,
        branch_id=b_id  # SEKARANG SUDAH AKURAT SESUAI PILIHAN
    )
    db.add(w)
    db.flush()
    ensure_all_items_in_warehouse(db, w.id)
    db.commit()
    db.refresh(w)
    return {"id": w.id, "message": f"Gudang {name} berhasil dibuat"}

@router.put("/{wid}")
def update_warehouse(wid: int, data: dict, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")
    
    for k in ["name", "address", "is_active"]:
        if k in data: setattr(w, k, data[k])
        
    if data.get("is_default"):
        db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == w.branch_id, # Hanya unset default di cabang yang sama
            models.Warehouse.id != wid
        ).update({"is_default": False})
        w.is_default = True
        
    db.commit()
    return {"message": "Gudang diperbarui"}

@router.delete("/{wid}")
def delete_warehouse(wid: int, db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")
    if w.is_default: raise HTTPException(400, "Gudang default tidak bisa dihapus")
    has_stock = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == wid,
        models.WarehouseStock.stock > 0
    ).first()
    if has_stock: raise HTTPException(400, "Masih ada stok di gudang ini. Transfer dulu sebelum hapus.")
    w.is_active = False
    db.commit()
    return {"message": "Gudang dinonaktifkan"}

# ─── Stock per Warehouse ──────────────────────────────────────────────────────

@router.get("/{wid}/stock")
def get_warehouse_stock_list(
    wid: int, search: Optional[str] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")

    q = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == wid
    ).join(models.Item)

    if search:
        q = q.filter(
            models.Item.name.ilike(f"%{search}%") |
            models.Item.code.ilike(f"%{search}%")
        )

    stocks = q.all()
    fifo_values = dict(
        db.query(
            models.StockBatch.item_id,
            func.sum(models.StockBatch.qty_remaining * models.StockBatch.unit_cost),
        ).filter(
            models.StockBatch.warehouse_id == wid,
            models.StockBatch.qty_remaining > 0,
        ).group_by(models.StockBatch.item_id).all()
    )
    return {
        "warehouse": {"id": w.id, "name": w.name, "code": w.code},
        "items": [{
            "item_id": s.item_id,
            "item_code": s.item.code if s.item else "-",
            "item_name": s.item.name if s.item else "-",
            "unit": s.item.unit.abbreviation if s.item and s.item.unit else "pcs",
            "stock": s.stock,
            "min_stock": s.item.min_stock if s.item else 0,
            "low_stock": s.stock <= (s.item.min_stock if s.item else 0),
            "stock_value": float(fifo_values.get(s.item_id, 0) or 0)
        } for s in stocks],
        "total_items": len(stocks),
        "total_value": sum(float(fifo_values.get(s.item_id, 0) or 0) for s in stocks)
    }

# ─── Warehouse Transfer ───────────────────────────────────────────────────────

class TransferItemIn(BaseModel):
    item_id: int
    qty: float = Field(gt=0, allow_inf_nan=False)
    serials: List[str] = Field(default_factory=list)

class TransferCreate(BaseModel):
    date: date
    from_warehouse_id: int
    to_warehouse_id: int
    notes: Optional[str] = None
    items: List[TransferItemIn] = Field(min_length=1)

@router.get("/transfers/all")
def get_transfers(skip: int = 0, limit: int = 50,
                  db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    q = db.query(models.WarehouseTransfer)
    
    # 🛡️ FILTER VISIBILITAS TRANSFER: Hanya lihat transfer yang melibatkan cabang user ini
    if current_user.active_branch_id:
        q = q.join(models.Warehouse, models.WarehouseTransfer.from_warehouse_id == models.Warehouse.id)\
             .filter((models.Warehouse.branch_id == current_user.active_branch_id) | 
                     (models.WarehouseTransfer.to_warehouse.has(branch_id=current_user.active_branch_id)))

    transfers = q.order_by(models.WarehouseTransfer.id.desc()).offset(skip).limit(limit).all()

    return [{
        "id": t.id, "number": t.number, "date": str(t.date),
        "from_warehouse": t.from_warehouse.name if t.from_warehouse else "-",
        "to_warehouse": t.to_warehouse.name if t.to_warehouse else "-",
        "status": t.status, "notes": t.notes,
        "can_cancel": bool(t.can_cancel and t.status == "confirmed"),
        "cancelled_at": t.cancelled_at.isoformat() if t.cancelled_at else None,
        "cancellation_reason": t.cancellation_reason,
        "items_count": len(t.items),
        "creator": t.creator.username if t.creator else "-"
    } for t in transfers]

# =====================================================================
# 3. TIMPA FUNGSI create_transfer (Fix Bug Mutasi Stok Aneh)
# =====================================================================
@router.post("/transfers/legacy-disabled", include_in_schema=False)
def create_transfer(data: TransferCreate, db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    raise HTTPException(410, "Endpoint transfer lama dinonaktifkan")
    if data.from_warehouse_id == data.to_warehouse_id:
        raise HTTPException(400, "Gudang asal dan tujuan tidak boleh sama")

    from_w = db.query(models.Warehouse).get(data.from_warehouse_id)
    to_w = db.query(models.Warehouse).get(data.to_warehouse_id)
    if not from_w: raise HTTPException(404, "Gudang asal tidak ditemukan")
    if not to_w: raise HTTPException(404, "Gudang tujuan tidak ditemukan")

    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        if not item: raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        if is_virtual_variant(item):
            raise HTTPException(
                400,
                f"Barang multi-satuan {item.name} tidak boleh dipindah gudang langsung. Pindahkan stok barang induknya.",
            )
        avail = get_warehouse_stock(db, data.from_warehouse_id, it.item_id)
        if avail < it.qty:
            raise HTTPException(400, f"Stok {item.name} di {from_w.name} tidak cukup")

    number = next_transfer_number(db)
    local_datetime = datetime.now(WITA)

    is_internal_transfer = (from_w.branch_id == to_w.branch_id)

    transfer = models.WarehouseTransfer(
        number=number, date=data.date,
        from_warehouse_id=data.from_warehouse_id,
        to_warehouse_id=data.to_warehouse_id,
        status="confirmed", notes=data.notes,
        created_by=current_user.id
    )
    db.add(transfer)
    db.flush()

    total_nilai_transfer = 0.0

    for it in data.items:
        item = db.query(models.Item).get(it.item_id)

        db.add(models.WarehouseTransferItem(
            transfer_id=transfer.id,
            item_id=it.item_id,
            qty=it.qty
        ))

        if is_internal_transfer:
            # 🏢 KASUS 1: SATU CABANG (Hanya Pindah Rak)
            # Snapshot stok cabang SEBELUM dipindah
            stok_cabang = get_total_branch_stock(db, from_w.branch_id, item.id)

            # Baru pindah fisik
            adjust_warehouse_stock(db, from_w.id, it.item_id, -it.qty)
            adjust_warehouse_stock(db, to_w.id, it.item_id, it.qty)
            # 🧱 FIFO: pindahkan lapisan biaya ikut barangnya (asal → tujuan)
            from ..services.inventory_fifo import transfer_batches
            total_nilai_transfer += transfer_batches(
                db, item_id=it.item_id, from_warehouse_id=from_w.id,
                to_warehouse_id=to_w.id, qty=it.qty,
            )

            db.add(models.StockMovement(
                date=data.date, created_at=local_datetime,
                item_id=item.id, branch_id=from_w.branch_id,
                type="internal_transfer", qty=it.qty,
                qty_before=stok_cabang,
                qty_after=stok_cabang,  # Total cabang tidak berubah, hanya pindah rak
                reference=number,
                notes=f"Pindah Rak: {from_w.name} ➔ {to_w.name}"
            ))

        else:
            # 🚚 KASUS 2: LINTAS CABANG (Kirim ke Luar Kota)
            # ✅ FIX UTAMA: Snapshot stok kedua cabang SEBELUM fisik dipindah
            stok_cabang_asal_before   = get_total_branch_stock(db, from_w.branch_id, item.id)
            stok_cabang_tujuan_before = get_total_branch_stock(db, to_w.branch_id, item.id)

            # Baru pindah fisik
            adjust_warehouse_stock(db, from_w.id, it.item_id, -it.qty)
            adjust_warehouse_stock(db, to_w.id, it.item_id, it.qty)
            # 🧱 FIFO: pindahkan lapisan biaya ikut barangnya (asal → tujuan)
            from ..services.inventory_fifo import transfer_batches
            total_nilai_transfer += transfer_batches(
                db, item_id=it.item_id, from_warehouse_id=from_w.id,
                to_warehouse_id=to_w.id, qty=it.qty,
            )

            # Mutasi KELUAR dari Cabang Asal
            db.add(models.StockMovement(
                date=data.date, created_at=local_datetime,
                item_id=item.id, branch_id=from_w.branch_id,
                type="transfer_out", qty=it.qty,
                qty_before=stok_cabang_asal_before,
                qty_after=stok_cabang_asal_before - it.qty,
                reference=number,
                notes=f"Dikirim ke Cabang {to_w.branch.name} ({to_w.name})"
            ))

            # Mutasi MASUK ke Cabang Tujuan
            db.add(models.StockMovement(
                date=data.date, created_at=local_datetime,
                item_id=item.id, branch_id=to_w.branch_id,
                type="transfer_in", qty=it.qty,
                qty_before=stok_cabang_tujuan_before,
                qty_after=stok_cabang_tujuan_before + it.qty,
                reference=number,
                notes=f"Diterima dari Cabang {from_w.branch.name} ({from_w.name})"
            ))

    # Jurnal Akuntansi (hanya untuk transfer lintas cabang)
    if not is_internal_transfer and total_nilai_transfer > 0:
        from ..services.journal_service import create_transfer_journal

        create_transfer_journal(
            db=db,
            date_val=data.date,
            number_ref=number,
            total=total_nilai_transfer,
            direction="out",
            description=f"Transfer Keluar ke {to_w.branch.name} ({to_w.name})",
            user_id=current_user.id,
            branch_id=from_w.branch_id,
        )

        create_transfer_journal(
            db=db,
            date_val=data.date,
            number_ref=number,
            total=total_nilai_transfer,
            direction="in",
            description=f"Transfer Masuk dari {from_w.branch.name} ({from_w.name})",
            user_id=current_user.id,
            branch_id=to_w.branch_id,
        )

    db.commit()
    return {"id": transfer.id, "number": number, "message": "Transfer stok & jurnal berhasil dicatat!"}


@router.post("/transfers")
def create_transfer_secure(data: TransferCreate, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    """Atomic, branch-scoped transfer with exact FIFO lineage."""
    try:
        if not current_user.active_branch_id:
            raise HTTPException(400, "Pilih cabang aktif terlebih dahulu")
        if data.from_warehouse_id == data.to_warehouse_id:
            raise HTTPException(400, "Gudang asal dan tujuan tidak boleh sama")
        from_w = db.query(models.Warehouse).filter(models.Warehouse.id == data.from_warehouse_id).with_for_update().first()
        to_w = db.query(models.Warehouse).filter(models.Warehouse.id == data.to_warehouse_id).with_for_update().first()
        if not from_w or not from_w.is_active:
            raise HTTPException(404, "Gudang asal tidak ditemukan atau nonaktif")
        if not to_w or not to_w.is_active:
            raise HTTPException(404, "Gudang tujuan tidak ditemukan atau nonaktif")
        if from_w.branch_id != current_user.active_branch_id:
            raise HTTPException(403, "Gudang asal harus berada pada cabang aktif")
        if not has_permission(db, current_user, "inventory.transfer", "create"):
            raise HTTPException(403, "Akses membuat transfer ditolak")
        internal = from_w.branch_id == to_w.branch_id
        if not internal and not has_permission(db, current_user, "inventory.branch_transfer", "create"):
            raise HTTPException(403, "Transfer beda cabang memerlukan izin khusus")
        if data.date != datetime.now(WITA).date() and not has_permission(db, current_user, "inventory.transfer", "lock_date"):
            raise HTTPException(403, "Tanggal transfer dikunci; gunakan tanggal hari ini")
        assert_books_open(db, from_w.branch_id, data.date, "Transfer stok")
        assert_books_open(db, to_w.branch_id, data.date, "Transfer stok")

        resolved, source_ids = [], set()
        for payload in data.items:
            entered = db.query(models.Item).get(payload.item_id)
            if not entered or not entered.is_active:
                raise HTTPException(404, f"Item {payload.item_id} tidak ditemukan atau nonaktif")
            item = get_stock_source_item(db, entered)
            factor = get_conversion_factor(entered) if is_virtual_variant(entered) else 1.0
            base_qty = float(payload.qty) * factor
            if item.id in source_ids:
                raise HTTPException(400, f"{item.name} dicatat lebih dari sekali setelah konversi satuan")
            source_ids.add(item.id)
            available = get_warehouse_stock(db, from_w.id, item.id)
            if available + 1e-9 < base_qty:
                raise HTTPException(400, f"Stok {item.name} di {from_w.name} tidak cukup")
            resolved.append((payload, entered, item, factor, base_qty))

        number = next_transfer_number(db, data.date)
        now = datetime.now(WITA)
        transfer = models.WarehouseTransfer(
            number=number, date=data.date, from_warehouse_id=from_w.id,
            to_warehouse_id=to_w.id, status="confirmed", notes=data.notes,
            created_by=current_user.id, can_cancel=True,
        )
        db.add(transfer)
        db.flush()
        total_value = 0.0
        from ..services.inventory_fifo import transfer_batches_with_lineage

        for payload, entered, item, factor, base_qty in resolved:
            row = models.WarehouseTransferItem(
                transfer_id=transfer.id, item_id=item.id, entered_item_id=entered.id,
                entered_qty=payload.qty, conversion_factor=factor, qty=base_qty,
                qty_received=base_qty,
            )
            db.add(row)
            db.flush()
            from_before = get_total_branch_stock(db, from_w.branch_id, item.id)
            to_before = get_total_branch_stock(db, to_w.branch_id, item.id)
            adjust_warehouse_stock(db, from_w.id, item.id, -base_qty)
            adjust_warehouse_stock(db, to_w.id, item.id, base_qty)
            allocations = transfer_batches_with_lineage(
                db, item_id=item.id, from_warehouse_id=from_w.id,
                to_warehouse_id=to_w.id, qty=base_qty,
            )
            line_value = 0.0
            for source, destination, moved_qty, cost in allocations:
                line_value += moved_qty * cost
                db.add(models.WarehouseTransferBatchAllocation(
                    transfer_item_id=row.id, source_batch_id=source.id,
                    destination_batch_id=destination.id, qty=moved_qty, unit_cost=cost,
                ))
            row.total_cost = line_value
            row.unit_cost = line_value / base_qty
            total_value += line_value
            transition_serials(
                db, item=item, qty=base_qty, serial_values=payload.serials,
                from_statuses={"in_stock", "quarantine"}, to_status="in_stock",
                from_warehouse_id=from_w.id, to_warehouse=to_w,
                reference_type="warehouse_transfer", reference_id=transfer.id,
                reference_number=number, user_id=current_user.id, event_type="transfer",
            )
            if internal:
                db.add(models.StockMovement(
                    date=data.date, created_at=now, item_id=item.id, branch_id=from_w.branch_id,
                    warehouse_id=from_w.id, counterparty_warehouse_id=to_w.id,
                    type="internal_transfer", qty=base_qty, qty_before=from_before,
                    qty_after=from_before, reference=number,
                    notes=f"Pindah Rak: {from_w.name} -> {to_w.name}",
                ))
            else:
                db.add(models.StockMovement(
                    date=data.date, created_at=now, item_id=item.id, branch_id=from_w.branch_id,
                    warehouse_id=from_w.id, counterparty_warehouse_id=to_w.id,
                    type="transfer_out", qty=base_qty, qty_before=from_before,
                    qty_after=from_before - base_qty, reference=number, notes=f"Dikirim ke {to_w.name}",
                ))
                db.add(models.StockMovement(
                    date=data.date, created_at=now, item_id=item.id, branch_id=to_w.branch_id,
                    warehouse_id=to_w.id, counterparty_warehouse_id=from_w.id,
                    type="transfer_in", qty=base_qty, qty_before=to_before,
                    qty_after=to_before + base_qty, reference=number, notes=f"Diterima dari {from_w.name}",
                ))

        if not internal and total_value > 0:
            from ..services.journal_service import create_transfer_journal
            create_transfer_journal(db=db, date_val=data.date, number_ref=number,
                total=total_value, direction="out", description=f"Transfer Keluar ke {to_w.name}",
                user_id=current_user.id, branch_id=from_w.branch_id)
            create_transfer_journal(db=db, date_val=data.date, number_ref=number,
                total=total_value, direction="in", description=f"Transfer Masuk dari {from_w.name}",
                user_id=current_user.id, branch_id=to_w.branch_id)
        write_audit(db, current_user.id, "CREATE", "warehouse_transfers", transfer.id, number)
        db.commit()
        return {"id": transfer.id, "number": number, "message": "Transfer stok & jurnal berhasil dicatat!"}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


class TransferCancel(BaseModel):
    reason: str = Field(min_length=3)


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: int, data: TransferCancel, db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    transfer = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.id == transfer_id
    ).with_for_update().first()
    if not transfer:
        raise HTTPException(404, "Transfer tidak ditemukan")
    if transfer.from_warehouse.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Transfer bukan milik cabang aktif")
    if not has_permission(db, current_user, "inventory.transfer", "delete"):
        raise HTTPException(403, "Akses membatalkan transfer ditolak")
    if transfer.status != "confirmed":
        raise HTTPException(400, "Transfer tidak berstatus confirmed")
    if not transfer.can_cancel:
        raise HTTPException(409, "Transfer lama tidak memiliki alokasi FIFO presisi dan tidak aman dibatalkan")
    today = datetime.now(WITA).date()
    assert_books_open(db, transfer.from_warehouse.branch_id, today, "Pembatalan transfer")
    assert_books_open(db, transfer.to_warehouse.branch_id, today, "Pembatalan transfer")
    try:
        # Validate every destination layer before changing any quantity.
        for row in transfer.items:
            if not row.batch_allocations:
                raise HTTPException(409, "Alokasi batch transfer tidak lengkap")
            for allocation in row.batch_allocations:
                batch = db.query(models.StockBatch).filter(
                    models.StockBatch.id == allocation.destination_batch_id
                ).with_for_update().first()
                if not batch or float(batch.qty_remaining or 0) + 1e-9 < float(allocation.qty):
                    raise HTTPException(409, f"Stok tujuan {row.item.name} sudah dipakai; transfer tidak dapat dibatalkan")
        reverse_reference_serials(
            db, reference_type="warehouse_transfer", reference_id=transfer.id,
            reversal_reference_number=f"REV-{transfer.number}", user_id=current_user.id,
        )
        now = datetime.now(WITA)
        for row in transfer.items:
            from_before = get_total_branch_stock(db, transfer.from_warehouse.branch_id, row.item_id)
            to_before = get_total_branch_stock(db, transfer.to_warehouse.branch_id, row.item_id)
            for allocation in row.batch_allocations:
                source = db.query(models.StockBatch).filter(models.StockBatch.id == allocation.source_batch_id).with_for_update().first()
                destination = db.query(models.StockBatch).filter(models.StockBatch.id == allocation.destination_batch_id).with_for_update().first()
                destination.qty_remaining = float(destination.qty_remaining or 0) - float(allocation.qty)
                source.qty_remaining = float(source.qty_remaining or 0) + float(allocation.qty)
            adjust_warehouse_stock(db, transfer.to_warehouse_id, row.item_id, -float(row.qty))
            adjust_warehouse_stock(db, transfer.from_warehouse_id, row.item_id, float(row.qty))
            db.add(models.StockMovement(
                date=today, created_at=now, item_id=row.item_id,
                branch_id=transfer.from_warehouse.branch_id, warehouse_id=transfer.from_warehouse_id,
                counterparty_warehouse_id=transfer.to_warehouse_id, type="transfer_reversal_in",
                qty=row.qty, qty_before=from_before, qty_after=from_before + row.qty,
                reference=f"REV-{transfer.number}", notes=data.reason,
            ))
            if transfer.from_warehouse.branch_id != transfer.to_warehouse.branch_id:
                db.add(models.StockMovement(
                    date=today, created_at=now, item_id=row.item_id,
                    branch_id=transfer.to_warehouse.branch_id, warehouse_id=transfer.to_warehouse_id,
                    counterparty_warehouse_id=transfer.from_warehouse_id, type="transfer_reversal_out",
                    qty=row.qty, qty_before=to_before, qty_after=to_before - row.qty,
                    reference=f"REV-{transfer.number}", notes=data.reason,
                ))
        journals = db.query(models.Journal).filter(models.Journal.reference == transfer.number).all()
        for original in journals:
            reversal = models.Journal(
                number=next_journal_number(db, original.branch_id), date=today,
                description=f"REVERSAL {transfer.number}: {data.reason}", reference=transfer.number,
                source="auto", created_by=current_user.id, branch_id=original.branch_id,
            )
            db.add(reversal)
            db.flush()
            for line in original.lines:
                db.add(models.JournalEntryLine(
                    journal_id=reversal.id, debit_account_id=line.credit_account_id,
                    credit_account_id=line.debit_account_id, amount=line.amount,
                    description=reversal.description,
                ))
            if original.branch_id == transfer.from_warehouse.branch_id:
                transfer.reversal_journal_id = reversal.id
        transfer.status = "cancelled"
        transfer.cancelled_at = now
        transfer.cancelled_by = current_user.id
        transfer.cancellation_reason = data.reason.strip()
        write_audit(db, current_user.id, "CANCEL", "warehouse_transfers", transfer.id,
                    f"{transfer.number}: {data.reason.strip()}")
        db.commit()
        return {"id": transfer.id, "number": transfer.number, "status": transfer.status}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


#ambil semua history barang masuk ke gudang tertentu (baik dari transfer antar gudang, pembelian, atau penyesuaian stok masuk)
@router.get("/{wid}/incoming-history")
def get_incoming_history(
    wid: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Cek apakah gudang ada
    w = db.query(models.Warehouse).get(wid)
    if not w: raise HTTPException(404, "Gudang tidak ditemukan")

    # Query item transfer yang menuju ke gudang ini (to_warehouse_id)
    # dan pastikan status transfernya sudah 'confirmed'
    q = db.query(models.WarehouseTransferItem).join(
        models.WarehouseTransfer,
        models.WarehouseTransfer.id == models.WarehouseTransferItem.transfer_id
    ).filter(
        models.WarehouseTransfer.to_warehouse_id == wid,
        models.WarehouseTransfer.status == "confirmed"
    )

    # Filter berdasarkan rentang tanggal jika diberikan
    if start_date:
        q = q.filter(models.WarehouseTransfer.date >= start_date)
    if end_date:
        q = q.filter(models.WarehouseTransfer.date <= end_date)

    # Urutkan dari yang terbaru (Descending)
    q = q.order_by(models.WarehouseTransfer.date.desc(), models.WarehouseTransfer.id.desc())

    items = q.all()
    
    return [{
        "transfer_date": str(it.transfer.date),
        "transfer_number": it.transfer.number,
        "from_warehouse": it.transfer.from_warehouse.name if it.transfer.from_warehouse else "Sistem",
        "item_code": it.item.code if it.item else "-",
        "item_name": it.item.name if it.item else "-",
        "qty": it.qty
    } for it in items]


from sqlalchemy.orm import aliased
from typing import Optional

# ... (kode lain yang sudah ada) ...

# =====================================================================
# ENDPOINT BARU: LAPORAN MUTASI MULTI-LEVEL (CABANG & GUDANG)
# =====================================================================
@router.get("/transfers/detailed")
def get_detailed_transfers(
    from_branch_id: Optional[int] = None,
    from_warehouse_id: Optional[int] = None,
    to_branch_id: Optional[int] = None,
    to_warehouse_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Buat alias karena kita join tabel Warehouse dua kali (Asal & Tujuan)
    FromWarehouse = aliased(models.Warehouse)
    ToWarehouse = aliased(models.Warehouse)

    q = db.query(models.WarehouseTransferItem).join(
        models.WarehouseTransfer,
        models.WarehouseTransfer.id == models.WarehouseTransferItem.transfer_id
    ).outerjoin(
        FromWarehouse, models.WarehouseTransfer.from_warehouse_id == FromWarehouse.id
    ).outerjoin(
        ToWarehouse, models.WarehouseTransfer.to_warehouse_id == ToWarehouse.id
    ).filter(models.WarehouseTransfer.status.in_(["shipping", "confirmed"]))

    if current_user.active_branch_id and current_user.active_branch_id != 1:
        q = q.filter(
            (FromWarehouse.branch_id == current_user.active_branch_id)
            | (ToWarehouse.branch_id == current_user.active_branch_id)
        )

    # Filter Bagian Asal (Sumber)
    if from_warehouse_id:
        q = q.filter(models.WarehouseTransfer.from_warehouse_id == from_warehouse_id)
    elif from_branch_id:
        q = q.filter(FromWarehouse.branch_id == from_branch_id)

    # Filter Bagian Tujuan (Penerima)
    if to_warehouse_id:
        q = q.filter(models.WarehouseTransfer.to_warehouse_id == to_warehouse_id)
    elif to_branch_id:
        q = q.filter(ToWarehouse.branch_id == to_branch_id)

    # Filter Tanggal (WITA)
    if start_date:
        q = q.filter(models.WarehouseTransfer.date >= start_date)
    if end_date:
        q = q.filter(models.WarehouseTransfer.date <= end_date)

    q = q.order_by(models.WarehouseTransfer.date.desc(), models.WarehouseTransfer.id.desc())
    items = q.all()

    return [{
        "date": str(it.transfer.date),
        "number": it.transfer.number,
        "from_branch": it.transfer.from_warehouse.branch.name if it.transfer.from_warehouse and it.transfer.from_warehouse.branch else "Eksternal/Sistem",
        "from_warehouse": it.transfer.from_warehouse.name if it.transfer.from_warehouse else "-",
        "to_branch": it.transfer.to_warehouse.branch.name if it.transfer.to_warehouse and it.transfer.to_warehouse.branch else "Eksternal/Sistem",
        "to_warehouse": it.transfer.to_warehouse.name if it.transfer.to_warehouse else "-",
        "item_code": it.item.code if it.item else "-",
        "item_name": it.item.name if it.item else "-",
        "qty": it.qty_received if it.transfer.status == "confirmed" and it.qty_received else it.qty,
        "status": it.transfer.status,
        "transfer_kind": it.transfer.transfer_kind,
    } for it in items]
