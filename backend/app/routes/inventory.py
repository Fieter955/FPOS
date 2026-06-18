from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from typing import Optional
from datetime import date
import pytz
from datetime import datetime, date
from ..database import get_db
from .. import models
from ..auth import get_current_user, get_query 
from ..schemas import AdjustmentCreate
from sqlalchemy import func 
from ..services.virtual_units import (
    get_effective_stock_from_source,
    get_stock_source_item,
    is_virtual_variant,
)

router = APIRouter()

WITA = pytz.timezone("Asia/Makassar")
def get_local_date(): return datetime.now(WITA).date()
def get_local_datetime(): return datetime.now(WITA)


# ─── 0. DIAGNOSTIK FIFO ─────────────────────────────────────────────────────
@router.get("/fifo-drift")
def get_fifo_drift(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Daftar (gudang, item) di mana Σ batch.qty_remaining != stok gudang.
    Idealnya KOSONG. Memantau kesehatan lapisan FIFO (invarian Σ batch == stok)."""
    from ..services.inventory_fifo import reconcile_report
    b_id = current_user.active_branch_id
    drift = []
    if b_id:
        wh_ids = [w.id for w in db.query(models.Warehouse.id).filter(
            models.Warehouse.branch_id == b_id).all()]
        for wid in wh_ids:
            drift.extend(reconcile_report(db, warehouse_id=wid))
    else:
        drift = reconcile_report(db)
    return {"count": len(drift), "items": drift}

# ─── 1. DAFTAR MUTASI STOK ──────────────────────────────────────────────────
@router.get("/movements")
def get_movements(
    item_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    type: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user) 
):
    q = get_query(db, models.StockMovement, current_user)
    
    if item_id: q = q.filter(models.StockMovement.item_id == item_id)
    if start_date: q = q.filter(models.StockMovement.date >= start_date)
    if end_date: q = q.filter(models.StockMovement.date <= end_date)
    if type: q = q.filter(models.StockMovement.type == type)
    
    # Eager-load item agar tidak N+1 (sebelumnya 1 query per baris mutasi).
    movements = (q.options(joinedload(models.StockMovement.item))
                  .order_by(models.StockMovement.id.desc())
                  .offset(skip).limit(limit).all())

    result = []
    for m in movements:
        item = m.item
        result.append({
            "id": m.id, 
            "date": str(m.date),
            "item": {
                "id": m.item_id,
                "name": item.name if item else "Item Dihapus",
                "code": item.code if item else "-"
            },
            "type": m.type, 
            "qty": m.qty,
            "qty_before": m.qty_before, 
            "qty_after": m.qty_after,
            "reference": m.reference, 
            "notes": m.notes
        })
    return result


# ─── 2. STOK MENIPIS (VERSI MULTI-CABANG) ──────────────────────────────────
@router.get("/low-stock")
def get_low_stock(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    gudang_cabang = db.query(models.Warehouse.id).filter(
        models.Warehouse.branch_id == current_user.active_branch_id
    ).all()
    warehouse_ids = [g[0] for g in gudang_cabang]

    items = db.query(models.Item).filter(models.Item.is_active == True).all()
    item_map = {item.id: item for item in items}
    parent_ids = {
        item.parent_item_id
        for item in items
        if is_virtual_variant(item) and item.parent_item_id and item.parent_item_id not in item_map
    }
    if parent_ids:
        for parent in db.query(models.Item).filter(models.Item.id.in_(parent_ids)).all():
            item_map[parent.id] = parent

    if warehouse_ids:
        rows = db.query(
            models.WarehouseStock.item_id,
            func.sum(models.WarehouseStock.stock),
        ).filter(
            models.WarehouseStock.warehouse_id.in_(warehouse_ids)
        ).group_by(models.WarehouseStock.item_id).all()
        stock_map = {item_id: float(stock or 0) for item_id, stock in rows}
    else:
        stock_map = {
            item_id: float(item.stock or 0)
            for item_id, item in item_map.items()
        }
    
    results = []
    for item in items:
        source_item = get_stock_source_item(db, item, item_map=item_map)
        source_stock = stock_map.get(source_item.id, 0.0)
        stok_lokal = round(get_effective_stock_from_source(item, source_stock), 4)

        if stok_lokal <= item.min_stock:
            results.append({
                "id": item.id, 
                "code": item.code, 
                "name": item.name, 
                "stock": stok_lokal, 
                "min_stock": item.min_stock
            })
            
    return results


# ─── 3. PENYESUAIAN STOK MANUAL / OPNAME BARU DENGAN JURNAL ─────────────────
# ─── 3. PENYESUAIAN STOK MANUAL / OPNAME BARU DENGAN JURNAL ─────────────────
@router.post("/adjust")
def stock_adjustment(
    data: AdjustmentCreate,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user) 
):
    item = db.query(models.Item).with_for_update().get(data.item_id) 
    if not item: 
        raise HTTPException(404, "Item tidak ditemukan")
    if is_virtual_variant(item):
        raise HTTPException(
            400,
            f"Barang multi-satuan {item.name} tidak bisa diopname langsung. Sesuaikan stok barang induknya.",
        )
        
    global_before = item.stock
    diff = 0
    
    # Cari gudang default dari cabang ini untuk memotong stoknya
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id,
        models.Warehouse.is_default == True
    ).first()
    
    if not gudang_aktif:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == current_user.active_branch_id
        ).first()
    
    from .warehouse import get_warehouse_stock, adjust_warehouse_stock, get_total_branch_stock
    
    local_before = 0.0
    if gudang_aktif:
        local_before = get_warehouse_stock(db, gudang_aktif.id, item.id)

    # Ambil Total Aset Fisik Cabang LOKAL
    branch_before = get_total_branch_stock(db, current_user.active_branch_id, item.id)

    # Logika mencari selisih (diff) berdasarkan tipe
    if data.type == 'in':
        diff = data.qty
    elif data.type == 'out':
        diff = -data.qty
    elif data.type == 'adjust':
        if gudang_aktif:
            diff = data.qty - local_before
        else:
            diff = data.qty - global_before
    else:
        raise HTTPException(400, "Tipe penyesuaian tidak dikenali")

    # Terapkan perubahan fisik
    item.stock += diff
    
    if gudang_aktif:
        adjust_warehouse_stock(db, gudang_aktif.id, item.id, diff)

    # 1. 🛡️ GENERATE REFERENSI TUNGGAL 🛡️
    ref_jurnal = f"OPN-{get_local_datetime().strftime('%Y%m%d%H%M%S')}"

    branch_id_untuk_simpan = (
        gudang_aktif.branch_id
        if gudang_aktif
        else (current_user.active_branch_id or 1)
    )

    db.add(models.StockMovement(
        date=get_local_date(), 
        created_at=get_local_datetime(),
        item_id=item.id, 
        branch_id=branch_id_untuk_simpan,  # ← Pakai ini, bukan current_user.active_branch_id
        type='in' if diff >= 0 else 'out',
        qty=abs(diff), 
        qty_before=branch_before, 
        qty_after=branch_before + diff, 
        reference=ref_jurnal, 
        notes=data.description
    ))

    # 3. Flush dulu StockMovement ke session (SEBELUM coba jurnal)
    db.flush()  # ← Ini mengunci StockMovement di session, tapi belum commit ke DB

    opname_mode = (data.opname_mode or "running").strip().lower()
    is_opening = opname_mode in ("opening", "setup", "awal", "saldo_awal", "initial")
    if is_opening:
        # Pastikan akun Modal Transisi tersedia agar jurnal tidak gagal
        acc = db.query(models.Account).filter(models.Account.code == "3-1999").first()
        if not acc:
            db.add(
                models.Account(
                    code="3-1999",
                    name="Modal Transisi (Setup Awal Stok)",
                    type="equity",
                    subtype="capital",
                    normal_balance="credit",
                    is_active=True,
                )
            )
            db.flush()

    # 4. Jurnal akuntansi pakai SAVEPOINT agar rollback-nya terisolasi
    if diff != 0:
        nilai_penyesuaian = abs(diff) * (item.buy_price or 0)
        
        if nilai_penyesuaian > 0:
            try:
                savepoint = db.begin_nested()  # ← Buat savepoint
                from .accounting import create_auto_journal
                
                if diff > 0:
                    credit_code = "3-1999" if is_opening else "4-1300"
                    entries = [
                        {"code": "1-1400", "debit": nilai_penyesuaian, "credit": 0},
                        {"code": credit_code, "debit": 0, "credit": nilai_penyesuaian},
                    ]
                    desc_prefix = "Setup Stok Awal" if is_opening else "Opname Surplus"
                    desc = f"{desc_prefix}: {item.name} (+{abs(diff)}) - {data.description}"
                else:
                    if is_opening:
                        entries = [
                            {"code": "3-1999", "debit": nilai_penyesuaian, "credit": 0},
                            {"code": "1-1400", "debit": 0, "credit": nilai_penyesuaian},
                        ]
                        desc = f"Setup Stok Awal: {item.name} (-{abs(diff)}) - {data.description}"
                    else:
                        entries = [
                            {"code": "5-2700", "debit": nilai_penyesuaian, "credit": 0},
                            {"code": "1-1400", "debit": 0, "credit": nilai_penyesuaian},
                        ]
                        desc = f"Opname Susut: {item.name} (-{abs(diff)}) - {data.description}"

                create_auto_journal(
                    db=db,
                    date_val=get_local_date(),
                    number_ref=ref_jurnal,
                    description=desc,
                    entries=entries,
                    user_id=current_user.id,
                    branch_id=current_user.active_branch_id
                )
                savepoint.commit()  # ← Commit savepoint jurnal saja
                
            except Exception as e:
                savepoint.rollback()  # ← Hanya rollback jurnal, StockMovement AMAN
                print(f"⚠️ Jurnal akuntansi dilewati: {e}")

    # 5. Commit semua (StockMovement pasti tersimpan)
    db.commit()
    
    new_local_stock = get_warehouse_stock(db, gudang_aktif.id, item.id) if gudang_aktif else item.stock
    return {"success": True, "message": "Opname berhasil dicatat di Mutasi Stok!", "new_stock": new_local_stock}
