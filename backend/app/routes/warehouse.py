"""
iPos 5.0 — Multi Gudang
- CRUD gudang
- Stok per gudang
- Transfer antar gudang
- Laporan stok per gudang
"""
import uuid
import json
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
from ..services.stok_gudang import ubah_stok_gudang

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
    hasil = ubah_stok_gudang(
        db, warehouse_id=warehouse_id, item_id=item_id, delta=delta
    )
    if commit:
        db.commit()
    return hasil

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


class TransferReceiptItem(BaseModel):
    item_id: int
    good_qty: float = Field(ge=0, allow_inf_nan=False)
    damaged_qty: float = Field(default=0, ge=0, allow_inf_nan=False)
    missing_qty: float = Field(default=0, ge=0, allow_inf_nan=False)
    notes: Optional[str] = None
    good_serials: List[str] = Field(default_factory=list)
    damaged_serials: List[str] = Field(default_factory=list)


class TransferReceipt(BaseModel):
    items: List[TransferReceiptItem] = Field(min_length=1)
    notes: Optional[str] = None

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
        "from_branch": t.from_warehouse.branch.name if t.from_warehouse and t.from_warehouse.branch else "-",
        "to_warehouse": t.to_warehouse.name if t.to_warehouse else "-",
        "to_branch": t.to_warehouse.branch.name if t.to_warehouse and t.to_warehouse.branch else "-",
        "status": t.status, "notes": t.notes,
        "can_cancel": bool(
            t.can_cancel
            and t.status in {"completed", "shipping", "confirmed"}
            and t.from_warehouse
            and t.from_warehouse.branch_id == current_user.active_branch_id
            and has_permission(db, current_user, "inventory.transfer", "delete")
        ),
        "cancelled_at": t.cancelled_at.isoformat() if t.cancelled_at else None,
        "cancellation_reason": t.cancellation_reason,
        "items_count": len(t.items),
        "creator": t.creator.username if t.creator else "-"
    } for t in transfers]


@router.get("/transfers/detail/{transfer_id}")
def get_transfer_detail(transfer_id: int, db: Session = Depends(get_db),
                        current_user: models.User = Depends(get_current_user)):
    transfer = db.query(models.WarehouseTransfer).get(transfer_id)
    if not transfer:
        raise HTTPException(404, "Transfer tidak ditemukan")
    involved = {transfer.from_warehouse.branch_id, transfer.to_warehouse.branch_id}
    if current_user.active_branch_id not in involved:
        raise HTTPException(403, "Transfer tidak melibatkan cabang aktif")
    return {
        "id": transfer.id, "number": transfer.number, "date": str(transfer.date),
        "status": transfer.status, "transfer_kind": transfer.transfer_kind,
        "from_warehouse": transfer.from_warehouse.name,
        "from_branch": transfer.from_warehouse.branch.name,
        "to_warehouse": transfer.to_warehouse.name,
        "to_branch": transfer.to_warehouse.branch.name,
        "notes": transfer.notes, "dispute_notes": transfer.dispute_notes,
        "received_at": transfer.received_at.isoformat() if transfer.received_at else None,
        "can_receive": transfer.status in {"shipping", "disputed", "partially_received"}
            and transfer.transfer_kind == "standard"
            and transfer.to_warehouse.branch_id == current_user.active_branch_id
            and has_permission(db, current_user, "inventory.transfer", "update"),
        "items": [{
            "item_id": row.item_id,
            "code": row.item.code if row.item else "-",
            "name": row.item.name if row.item else "Item dihapus",
            "unit": row.item.unit.abbreviation if row.item and row.item.unit else None,
            "qty_sent": float(row.qty or 0),
            "qty_received": float(row.qty_received or 0),
            "qty_damaged": float(row.qty_damaged or 0),
            "qty_missing": float(row.qty_missing or 0),
            "unit_cost": float(row.unit_cost or 0),
            "total_cost": float(row.total_cost or 0),
            "receipt_notes": row.receipt_notes,
        } for row in transfer.items],
    }


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(transfer_id: int, data: TransferReceipt,
                     db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    transfer = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.id == transfer_id
    ).with_for_update().first()
    if not transfer or transfer.transfer_kind != "standard":
        raise HTTPException(404, "Transfer gudang tidak ditemukan")
    if transfer.to_warehouse.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Hanya cabang tujuan yang dapat menerima transfer")
    if transfer.status not in {"shipping", "disputed", "partially_received"}:
        raise HTTPException(409, f"Transfer berstatus {transfer.status}")
    if not has_permission(db, current_user, "inventory.transfer", "update"):
        raise HTTPException(403, "Akses menerima transfer ditolak")
    payloads = {row.item_id: row for row in data.items}
    if len(payloads) != len(data.items):
        raise HTTPException(400, "Barang penerimaan tidak boleh ganda")
    now = datetime.now(WITA)
    today = now.date()
    assert_books_open(db, transfer.to_warehouse.branch_id, today, "Penerimaan transfer")
    inventory_value = quarantine_value = 0.0
    any_missing = False
    try:
        for row in transfer.items:
            payload = payloads.get(row.item_id)
            if not payload:
                raise HTTPException(400, f"Hasil penerimaan {row.item.name} belum diisi")
            remaining = float(row.qty or 0) - float(row.qty_received or 0) - float(row.qty_damaged or 0)
            declared = float(payload.good_qty) + float(payload.damaged_qty) + float(payload.missing_qty)
            if abs(declared - remaining) > 1e-6:
                raise HTTPException(400, f"Total kondisi {row.item.name} harus {remaining:g}")
            if row.item.tracks_serial:
                if len(payload.good_serials) != round(payload.good_qty) or len(payload.damaged_serials) != round(payload.damaged_qty):
                    raise HTTPException(400, f"Serial penerimaan {row.item.name} belum lengkap")
            destination_before = get_warehouse_stock(db, transfer.to_warehouse_id, row.item_id)
            branch_before = get_total_branch_stock(db, transfer.to_warehouse.branch_id, row.item_id)
            good_left, damaged_left = float(payload.good_qty), float(payload.damaged_qty)
            damaged_value = 0.0
            for allocation in row.batch_allocations:
                available = float(allocation.qty) - float(allocation.qty_received or 0) - float(allocation.qty_damaged or 0) - float(allocation.qty_resolved or 0)
                good_take = min(available, good_left)
                if good_take > 0:
                    allocation.destination_batch.qty_remaining += good_take
                    allocation.qty_received += good_take
                    good_left -= good_take
                    available -= good_take
                    inventory_value += good_take * float(allocation.unit_cost or 0)
                damaged_take = min(available, damaged_left)
                if damaged_take > 0:
                    allocation.qty_damaged += damaged_take
                    damaged_left -= damaged_take
                    damaged_value += damaged_take * float(allocation.unit_cost or 0)
            if good_left > 1e-6 or damaged_left > 1e-6:
                raise HTTPException(409, f"Lapisan transfer {row.item.name} tidak lengkap")
            if payload.good_qty > 0:
                adjust_warehouse_stock(db, transfer.to_warehouse_id, row.item_id, payload.good_qty)
                db.add(models.StockMovement(
                    date=today, created_at=now, item_id=row.item_id,
                    branch_id=transfer.to_warehouse.branch_id,
                    warehouse_id=transfer.to_warehouse_id,
                    counterparty_warehouse_id=transfer.from_warehouse_id,
                    type="transfer_in", event_category="transfer_in", direction="in",
                    source_type="warehouse_transfer", source_id=transfer.id,
                    qty=payload.good_qty, qty_before=branch_before,
                    qty_after=branch_before + payload.good_qty,
                    warehouse_qty_before=destination_before,
                    warehouse_qty_after=destination_before + payload.good_qty,
                    reference=transfer.number, notes=payload.notes or f"Diterima dari {transfer.from_warehouse.name}",
                ))
                transition_serials(db, item=row.item, qty=payload.good_qty,
                    serial_values=payload.good_serials, from_statuses={"in_transit"},
                    to_status="in_stock", from_warehouse_id=transfer.to_warehouse_id,
                    to_warehouse=transfer.to_warehouse, reference_type="warehouse_transfer",
                    reference_id=transfer.id, reference_number=transfer.number,
                    user_id=current_user.id, event_type="transfer_received")
            if payload.damaged_qty > 0:
                quarantine_value += damaged_value
                db.add(models.InventoryQuarantine(
                    number=f"QRT-{transfer.number}-{row.id}-{uuid.uuid4().hex[:4].upper()}", date=today,
                    branch_id=transfer.to_warehouse.branch_id,
                    warehouse_id=transfer.to_warehouse_id, item_id=row.item_id,
                    qty=payload.damaged_qty, qty_open=payload.damaged_qty,
                    unit_cost=damaged_value / payload.damaged_qty,
                    total_cost=damaged_value, source_type="warehouse_transfer",
                    source_id=transfer.id, status="open", notes=payload.notes,
                    created_by=current_user.id,
                ))
                transition_serials(db, item=row.item, qty=payload.damaged_qty,
                    serial_values=payload.damaged_serials, from_statuses={"in_transit"},
                    to_status="quarantine", from_warehouse_id=transfer.to_warehouse_id,
                    to_warehouse=transfer.to_warehouse, reference_type="inventory_quarantine",
                    reference_id=transfer.id, reference_number=transfer.number,
                    user_id=current_user.id, event_type="quarantined")
            row.qty_received = float(row.qty_received or 0) + payload.good_qty
            row.qty_damaged = float(row.qty_damaged or 0) + payload.damaged_qty
            row.qty_missing = payload.missing_qty
            row.receipt_notes = (payload.notes or "").strip() or None
            any_missing = any_missing or payload.missing_qty > 1e-9
        from ..services.journal_service import create_transfer_receipt_journal
        create_transfer_receipt_journal(db, date_val=today, number_ref=transfer.number,
            inventory_total=inventory_value, quarantine_total=quarantine_value,
            user_id=current_user.id, branch_id=transfer.to_warehouse.branch_id)
        transfer.status = "disputed" if any_missing else "received"
        transfer.received_at = now
        transfer.received_by = current_user.id
        transfer.dispute_notes = (data.notes or "").strip() or None if any_missing else None
        write_audit(db, current_user.id, "RECEIVE", "warehouse_transfers", transfer.id, transfer.status)
        db.commit()
        return {"id": transfer.id, "number": transfer.number, "status": transfer.status}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

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
            to_warehouse_id=to_w.id, status="completed" if internal else "shipping", notes=data.notes,
            shipped_at=now,
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
                qty_received=base_qty if internal else 0,
                serials_json=json.dumps(payload.serials or []),
            )
            db.add(row)
            db.flush()
            from_before = get_total_branch_stock(db, from_w.branch_id, item.id)
            to_before = get_total_branch_stock(db, to_w.branch_id, item.id)
            from_location_before = get_warehouse_stock(db, from_w.id, item.id)
            to_location_before = get_warehouse_stock(db, to_w.id, item.id)
            adjust_warehouse_stock(db, from_w.id, item.id, -base_qty)
            if internal:
                adjust_warehouse_stock(db, to_w.id, item.id, base_qty)
            allocations = transfer_batches_with_lineage(
                db, item_id=item.id, from_warehouse_id=from_w.id,
                to_warehouse_id=to_w.id, qty=base_qty,
                destination_available=internal,
            )
            line_value = 0.0
            for source, destination, moved_qty, cost in allocations:
                line_value += moved_qty * cost
                db.add(models.WarehouseTransferBatchAllocation(
                    transfer_item_id=row.id, source_batch_id=source.id,
                    destination_batch_id=destination.id, qty=moved_qty, unit_cost=cost,
                    qty_received=moved_qty if internal else 0,
                ))
            row.total_cost = line_value
            row.unit_cost = line_value / base_qty
            total_value += line_value
            transition_serials(
                db, item=item, qty=base_qty, serial_values=payload.serials,
                from_statuses={"in_stock", "quarantine"}, to_status="in_stock" if internal else "in_transit",
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
                    event_category="internal_transfer", direction="neutral",
                    source_type="warehouse_transfer", source_id=transfer.id,
                    warehouse_qty_before=from_location_before,
                    warehouse_qty_after=from_location_before - base_qty,
                    counterparty_qty_before=to_location_before,
                    counterparty_qty_after=to_location_before + base_qty,
                    notes=f"Pindah Rak: {from_w.name} -> {to_w.name}",
                ))
            else:
                db.add(models.StockMovement(
                    date=data.date, created_at=now, item_id=item.id, branch_id=from_w.branch_id,
                    warehouse_id=from_w.id, counterparty_warehouse_id=to_w.id,
                    type="transfer_out", qty=base_qty, qty_before=from_before,
                    qty_after=from_before - base_qty, reference=number, notes=f"Dikirim ke {to_w.name}",
                    event_category="transfer_out", direction="out",
                    source_type="warehouse_transfer", source_id=transfer.id,
                    warehouse_qty_before=from_location_before,
                    warehouse_qty_after=from_location_before - base_qty,
                ))

        if not internal and total_value > 0:
            from ..services.journal_service import create_transfer_journal
            create_transfer_journal(db=db, date_val=data.date, number_ref=number,
                total=total_value, direction="out", description=f"Transfer Keluar ke {to_w.name}",
                user_id=current_user.id, branch_id=from_w.branch_id)
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
    if transfer.status not in {"confirmed", "completed", "shipping"}:
        raise HTTPException(400, "Transfer yang sudah diterima/dispute tidak dapat dibatalkan")
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
                required_at_destination = float(allocation.qty or 0) if transfer.status in {"confirmed", "completed"} else 0.0
                if not batch or float(batch.qty_remaining or 0) + 1e-9 < required_at_destination:
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
                destination.qty_remaining = float(destination.qty_remaining or 0) - (
                    float(allocation.qty) if transfer.status in {"confirmed", "completed"} else 0.0
                )
                source.qty_remaining = float(source.qty_remaining or 0) + float(allocation.qty)
            if transfer.status in {"confirmed", "completed"}:
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


# Compatibility URL used by the current warehouse page. It is intentionally
# registered after every static /transfers/* route so "detailed" and "all"
# can never be swallowed by the integer path parameter.
@router.get("/transfers/{transfer_id}")
def get_transfer_detail_compat(transfer_id: int, db: Session = Depends(get_db),
                               current_user: models.User = Depends(get_current_user)):
    return get_transfer_detail(transfer_id, db, current_user)


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
    ).filter(models.WarehouseTransfer.status.in_(["shipping", "confirmed", "completed", "received", "disputed", "partially_received"]))

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
        "qty": it.qty_received if it.transfer.status in {"confirmed", "completed", "received", "disputed"} and it.qty_received else it.qty,
        "status": it.transfer.status,
        "transfer_kind": it.transfer.transfer_kind,
    } for it in items]
