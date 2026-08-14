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
from ..services.low_stock import get_low_stock_items
from ..services.virtual_units import is_virtual_variant

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


@router.get("/value-drift")
def get_value_drift(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Selisih NILAI persediaan: saldo GL Persediaan (1-1400) vs Σ(qty_remaining×unit_cost)
    lapisan batch, untuk cabang aktif. Beda dari /fifo-drift yang cek KUANTITAS. Idealnya ~0;
    jika tidak, ada jalur yang menilai batch beda dari yang masuk ke pembukuan."""
    from .accounting import get_account_balance
    b_id = current_user.active_branch_id
    akun = db.query(models.Account).filter(models.Account.code == "1-1400").first()
    gl = get_account_balance(db, akun.id, branch_id=b_id) if akun else 0.0
    if b_id:
        wh_ids = [w.id for w in db.query(models.Warehouse.id).filter(
            models.Warehouse.branch_id == b_id).all()]
    else:
        wh_ids = [w.id for w in db.query(models.Warehouse.id).all()]
    nilai_batch = 0.0
    if wh_ids:
        nilai_batch = float(
            db.query(func.coalesce(func.sum(models.StockBatch.qty_remaining * models.StockBatch.unit_cost), 0.0))
            .filter(models.StockBatch.warehouse_id.in_(wh_ids)).scalar() or 0.0
        )
    return {
        "branch_id": b_id,
        "gl_persediaan": round(float(gl), 2),
        "nilai_batch": round(nilai_batch, 2),
        "selisih": round(float(gl) - nilai_batch, 2),
        "seimbang": abs(float(gl) - nilai_batch) < 1,
    }

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
    return get_low_stock_items(db, current_user.active_branch_id)


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

    # 🧱 FIFO: jaga lapisan persediaan agar Σ batch == stok tetap terjaga.
    #   • Surplus (diff>0): buat lapisan baru senilai harga beli (selaras nilai jurnal).
    #   • Susut  (diff<0): konsumsi FIFO; nilai jurnal dipakai dari biaya batch yang
    #     benar-benar keluar (lebih presisi daripada harga beli "umum").
    # (item opname dijamin BUKAN multi-satuan — sudah ditolak di atas, jadi item.id
    #  adalah item stok nyata dan diff sudah dalam satuan dasar.)
    fifo_nilai_keluar = None
    if gudang_aktif and diff != 0:
        from ..services.inventory_fifo import add_batch, consume_fifo
        if diff > 0:
            add_batch(
                db, item_id=item.id, warehouse_id=gudang_aktif.id,
                qty=diff, unit_cost=item.buy_price or 0,
                received_date=get_local_date(),
            )
        else:
            _allocs = consume_fifo(
                db, item_id=item.id, warehouse_id=gudang_aktif.id, qty=-diff,
            )
            fifo_nilai_keluar = sum(q * c for (_b, q, c) in _allocs)

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

    # 4. Jurnal akuntansi diposting DI DALAM transaksi yang sama dengan StockMovement (atomic):
    #    bila jurnal gagal, SELURUH opname ikut rollback — tidak ada lagi stok berubah tanpa jurnal.
    if diff != 0:
        # Susut berbatch → pakai biaya FIFO yang benar-benar keluar; selain itu
        # (surplus, atau mode tanpa gudang) → harga beli seperti semula.
        nilai_penyesuaian = (
            fifo_nilai_keluar
            if fifo_nilai_keluar is not None
            else abs(diff) * (item.buy_price or 0)
        )

        if nilai_penyesuaian > 0:
            from .accounting import create_auto_journal, pastikan_akun_ada

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

            # 🔒 Pastikan akun yang dipakai ada SEBELUM posting → jurnal tidak gagal lalu
            # meninggalkan StockMovement tanpa pasangan GL. Karena diposting di transaksi
            # utama, kalau toh gagal (mis. tanggal masuk periode tutup buku) SELURUH opname
            # rollback — bukan stok berubah diam-diam tanpa jurnal.
            pastikan_akun_ada(db, [e["code"] for e in entries])
            create_auto_journal(
                db=db,
                date_val=get_local_date(),
                number_ref=ref_jurnal,
                description=desc,
                entries=entries,
                user_id=current_user.id,
                branch_id=current_user.active_branch_id
            )

    # 5. Commit semua (StockMovement + jurnal sekaligus, atomic)
    db.commit()
    
    new_local_stock = get_warehouse_stock(db, gudang_aktif.id, item.id) if gudang_aktif else item.stock
    return {"success": True, "message": "Opname berhasil dicatat di Mutasi Stok!", "new_stock": new_local_stock}
