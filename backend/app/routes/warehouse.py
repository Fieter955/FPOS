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
from pydantic import BaseModel
from typing import List
import pytz

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

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

def next_transfer_number(db: Session) -> str:
    today = datetime.now(WITA).strftime("%Y%m%d")
    prefix = f"TR{today}"
    last = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.number.like(f"{prefix}%")
    ).order_by(models.WarehouseTransfer.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"

def get_warehouse_stock(db: Session, warehouse_id: int, item_id: int) -> float:
    ws = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id
    ).first()
    return ws.stock if ws else 0.0

def adjust_warehouse_stock(db: Session, warehouse_id: int, item_id: int,
                            delta: float, commit: bool = False):
    ws = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id
    ).first()
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
            "stock_value": s.stock * (s.item.buy_price if s.item else 0)
        } for s in stocks],
        "total_items": len(stocks),
        "total_value": sum(
            s.stock * (s.item.buy_price if s.item else 0) for s in stocks
        )
    }

# ─── Warehouse Transfer ───────────────────────────────────────────────────────

class TransferItemIn(BaseModel):
    item_id: int
    qty: float

class TransferCreate(BaseModel):
    date: date
    from_warehouse_id: int
    to_warehouse_id: int
    notes: Optional[str] = None
    items: List[TransferItemIn]

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
        "items_count": len(t.items),
        "creator": t.creator.username if t.creator else "-"
    } for t in transfers]

# =====================================================================
# 3. TIMPA FUNGSI create_transfer (Fix Bug Mutasi Stok Aneh)
# =====================================================================
@router.post("/transfers")
def create_transfer(data: TransferCreate, db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    if data.from_warehouse_id == data.to_warehouse_id:
        raise HTTPException(400, "Gudang asal dan tujuan tidak boleh sama")

    from_w = db.query(models.Warehouse).get(data.from_warehouse_id)
    to_w = db.query(models.Warehouse).get(data.to_warehouse_id)
    if not from_w: raise HTTPException(404, "Gudang asal tidak ditemukan")
    if not to_w: raise HTTPException(404, "Gudang tujuan tidak ditemukan")

    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        if not item: raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
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
        total_nilai_transfer += (item.buy_price or 0) * it.qty

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
        from .accounting import create_auto_journal

        create_auto_journal(
            db=db, date_val=data.date, number_ref=number,
            description=f"Transfer Keluar ke {to_w.branch.name} ({to_w.name})",
            entries=[
                {"code": "3-2000", "debit": total_nilai_transfer, "credit": 0},
                {"code": "1-1400", "debit": 0, "credit": total_nilai_transfer}
            ],
            user_id=current_user.id,
            branch_id=from_w.branch_id
        )

        create_auto_journal(
            db=db, date_val=data.date, number_ref=number,
            description=f"Transfer Masuk dari {from_w.branch.name} ({from_w.name})",
            entries=[
                {"code": "1-1400", "debit": total_nilai_transfer, "credit": 0},
                {"code": "3-2000", "debit": 0, "credit": total_nilai_transfer}
            ],
            user_id=current_user.id,
            branch_id=to_w.branch_id
        )

    db.commit()
    return {"id": transfer.id, "number": number, "message": "Transfer stok & jurnal berhasil dicatat!"}


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
    ).filter(
        models.WarehouseTransfer.status == "confirmed"
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
        "qty": it.qty
    } for it in items]