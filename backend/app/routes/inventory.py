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
from ..schemas import (
    AdjustmentCreate,
    InventoryDocumentCancel,
    InventoryDocumentCreate,
    InventoryDocumentLineCreate,
)
from sqlalchemy import func 
from ..services.low_stock import get_low_stock_items
from ..services.virtual_units import is_virtual_variant
from ..services import inventory_documents as inventory_document_service
from ..permissions import has_permission

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
@router.get("/document-warehouses")
def get_document_warehouses(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not any(
        has_permission(db, current_user, key, "view")
        for key in inventory_document_service.TYPE_PERMISSION.values()
    ):
        raise HTTPException(403, "Akses dokumen persediaan ditolak")
    rows = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id,
        models.Warehouse.is_active == True,
    ).order_by(models.Warehouse.is_default.desc(), models.Warehouse.name).all()
    return [
        {"id": row.id, "name": row.name, "code": row.code, "is_default": row.is_default}
        for row in rows
    ]


@router.get("/item-snapshot")
def get_item_snapshot(
    warehouse_id: int,
    item_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not any(
        has_permission(db, current_user, key, "view")
        for key in inventory_document_service.TYPE_PERMISSION.values()
    ):
        raise HTTPException(403, "Akses dokumen persediaan ditolak")
    warehouse = db.query(models.Warehouse).filter(models.Warehouse.id == warehouse_id).first()
    if not warehouse or warehouse.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Gudang tidak tersedia pada cabang aktif")
    q = db.query(models.Item).filter(
        models.Item.is_active == True,
        models.Item.is_virtual_variant == False,
        models.Item.parent_item_id.is_(None),
    )
    if item_id:
        q = q.filter(models.Item.id == item_id)
    if search:
        pattern = f"%{search.strip()}%"
        q = q.filter(
            models.Item.name.ilike(pattern)
            | models.Item.code.ilike(pattern)
            | models.Item.barcode.ilike(pattern)
        )
    show_stock = has_permission(db, current_user, "inventory.opname_show_stock", "view")
    show_cost = has_permission(db, current_user, "inventory.show_cost_in", "view") or has_permission(
        db, current_user, "inventory.show_cost_out", "view"
    )
    result = []
    for item in q.order_by(models.Item.name).limit(min(max(limit, 1), 200)).all():
        qty = inventory_document_service.warehouse_stock(db, warehouse.id, item.id)
        result.append({
            "id": item.id,
            "code": item.code,
            "barcode": item.barcode,
            "name": item.name,
            "unit": item.unit.abbreviation if item.unit else None,
            "stock": qty if show_stock else None,
            "buy_price": float(item.buy_price or 0) if show_cost else None,
            "snapshot_token": inventory_document_service.snapshot_token(warehouse.id, item.id, qty),
        })
    return result


@router.get("/document-accounts")
def get_document_accounts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not any(
        has_permission(db, current_user, key, "view")
        for key in inventory_document_service.TYPE_PERMISSION.values()
    ):
        raise HTTPException(403, "Akses dokumen persediaan ditolak")
    can_override = has_permission(db, current_user, "inventory.account_override", "view")
    accounts = db.query(models.Account).filter(
        models.Account.is_active == True,
        models.Account.type.in_(["revenue", "equity", "expense"]),
    ).order_by(models.Account.code).all()
    return {
        "can_override": can_override,
        "defaults": {
            key: {"surplus": plus, "shortage": minus}
            for key, (plus, minus) in inventory_document_service.DEFAULT_ACCOUNT.items()
        },
        "accounts": [
            {"id": account.id, "code": account.code, "name": account.name, "type": account.type}
            for account in accounts
        ] if can_override else [],
    }


@router.get("/documents")
def list_inventory_documents(
    type: Optional[str] = None,
    status: Optional[str] = None,
    warehouse_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.InventoryDocument).filter(
        models.InventoryDocument.branch_id == current_user.active_branch_id
    )
    if type:
        if type not in inventory_document_service.TYPE_PERMISSION:
            raise HTTPException(400, "Tipe dokumen tidak dikenal")
        if not has_permission(db, current_user, inventory_document_service.TYPE_PERMISSION[type], "view"):
            raise HTTPException(403, "Akses daftar dokumen ditolak")
        q = q.filter(models.InventoryDocument.type == type)
    else:
        allowed_types = [
            doc_type
            for doc_type, key in inventory_document_service.TYPE_PERMISSION.items()
            if has_permission(db, current_user, key, "view")
        ]
        if not allowed_types:
            raise HTTPException(403, "Akses daftar dokumen ditolak")
        q = q.filter(models.InventoryDocument.type.in_(allowed_types))
    if status:
        q = q.filter(models.InventoryDocument.status == status)
    if warehouse_id:
        q = q.filter(models.InventoryDocument.warehouse_id == warehouse_id)
    if start_date:
        q = q.filter(models.InventoryDocument.date >= start_date)
    if end_date:
        q = q.filter(models.InventoryDocument.date <= end_date)
    if search:
        pattern = f"%{search.strip()}%"
        q = q.outerjoin(models.InventoryDocumentLine).outerjoin(models.Item).filter(
            models.InventoryDocument.number.ilike(pattern)
            | models.InventoryDocument.notes.ilike(pattern)
            | models.Item.name.ilike(pattern)
            | models.Item.code.ilike(pattern)
        ).distinct()
    rows = q.order_by(models.InventoryDocument.id.desc()).offset(skip).limit(min(limit, 500)).all()
    return [inventory_document_service.serialize_document(db, row, current_user) for row in rows]


@router.get("/documents/{document_id}")
def get_inventory_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    document = db.query(models.InventoryDocument).filter(
        models.InventoryDocument.id == document_id,
        models.InventoryDocument.branch_id == current_user.active_branch_id,
    ).first()
    if not document:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    if not has_permission(db, current_user, inventory_document_service.TYPE_PERMISSION[document.type], "view"):
        raise HTTPException(403, "Akses detail dokumen ditolak")
    return inventory_document_service.serialize_document(db, document, current_user, detail=True)


@router.post("/documents")
def post_inventory_document(
    data: InventoryDocumentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        document = inventory_document_service.create_document(db, data, current_user)
        db.commit()
        db.refresh(document)
        return inventory_document_service.serialize_document(db, document, current_user, detail=True)
    except Exception:
        db.rollback()
        raise


@router.post("/documents/{document_id}/cancel")
def cancel_inventory_document(
    document_id: int,
    data: InventoryDocumentCancel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    document = db.query(models.InventoryDocument).filter(
        models.InventoryDocument.id == document_id,
        models.InventoryDocument.branch_id == current_user.active_branch_id,
    ).with_for_update().first()
    if not document:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    try:
        inventory_document_service.cancel_document(db, document, data.reason, current_user)
        db.commit()
        db.refresh(document)
        return inventory_document_service.serialize_document(db, document, current_user, detail=True)
    except Exception:
        db.rollback()
        raise


@router.post("/adjust", deprecated=True)
def legacy_stock_adjustment_adapter(
    data: AdjustmentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Adapter satu baris untuk build lama; UI baru menggunakan /documents."""
    warehouse = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id,
        models.Warehouse.is_default == True,
    ).first() or db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id
    ).first()
    if not warehouse:
        raise HTTPException(400, "Cabang aktif belum memiliki gudang")

    if (data.opname_mode or "").lower() in {"opening", "setup", "awal", "saldo_awal", "initial"}:
        doc_type = "opening_stock"
        line = InventoryDocumentLineCreate(item_id=data.item_id, qty=data.qty, notes=data.description)
    elif data.type == "in":
        doc_type = "item_in"
        line = InventoryDocumentLineCreate(item_id=data.item_id, qty=data.qty, notes=data.description)
    elif data.type == "out":
        doc_type = "item_out"
        line = InventoryDocumentLineCreate(item_id=data.item_id, qty=data.qty, notes=data.description)
    elif data.type == "adjust":
        doc_type = "stock_opname"
        current = inventory_document_service.warehouse_stock(db, warehouse.id, data.item_id)
        line = InventoryDocumentLineCreate(
            item_id=data.item_id,
            physical_qty=data.qty,
            snapshot_token=inventory_document_service.snapshot_token(warehouse.id, data.item_id, current),
            notes=data.description,
        )
    else:
        raise HTTPException(400, "Tipe penyesuaian tidak dikenali")

    payload = InventoryDocumentCreate(
        type=doc_type,
        date=get_local_date(),
        warehouse_id=warehouse.id,
        notes=data.description,
        lines=[line],
    )
    try:
        document = inventory_document_service.create_document(db, payload, current_user)
        db.commit()
        return {
            "success": True,
            "message": f"Dokumen {document.number} berhasil dicatat",
            "document_id": document.id,
            "new_stock": inventory_document_service.warehouse_stock(db, warehouse.id, data.item_id),
        }
    except Exception:
        db.rollback()
        raise


@router.post("/adjust-direct-legacy", include_in_schema=False)
def stock_adjustment(
    data: AdjustmentCreate,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user) 
):
    raise HTTPException(410, "Gunakan dokumen persediaan; penyesuaian langsung sudah dinonaktifkan")
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
