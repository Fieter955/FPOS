from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date
import pytz
from datetime import datetime, date
import math
import uuid
from ..database import get_db
from .. import models
from ..auth import get_current_user, get_query 
from ..schemas import (
    AdjustmentCreate,
    InventoryDocumentCancel,
    InventoryDocumentCreate,
    InventoryDocumentLineCreate,
    InventoryReconciliationApply,
)
from sqlalchemy import func, or_, and_
from ..services.low_stock import get_low_stock_items
from ..services.virtual_units import get_conversion_factor, get_effective_stock_from_source, get_stock_source_item, is_virtual_variant
from ..services import inventory_documents as inventory_document_service
from ..services import inventory_reconciliation as reconciliation_service
from ..services.stok_gudang import ubah_stok_gudang
from ..permissions import has_permission
from .accounting import assert_books_open

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
    if type:
        if type == "sale":
            q = q.filter(or_(
                models.StockMovement.event_category == "sale",
                and_(models.StockMovement.event_category.is_(None),
                     models.StockMovement.type == "out",
                     models.StockMovement.notes.ilike("Penjualan%")),
            ))
        else:
            q = q.filter(or_(
                models.StockMovement.event_category == type,
                and_(models.StockMovement.event_category.is_(None), models.StockMovement.type == type),
            ))
    
    # Eager-load item agar tidak N+1 (sebelumnya 1 query per baris mutasi).
    movements = (q.options(joinedload(models.StockMovement.item))
                  .order_by(models.StockMovement.id.desc())
                  .offset(skip).limit(limit).all())

    result = []
    for m in movements:
        item = m.item
        category = m.event_category
        if not category:
            if m.type == "out" and (m.notes or "").lower().startswith("penjualan"):
                category = "sale"
            elif m.type == "in" and "pembelian" in (m.notes or "").lower():
                category = "purchase_in"
            else:
                category = m.type
        direction = m.direction
        if not direction:
            if category == "internal_transfer":
                direction = "neutral"
            elif category in {"in", "purchase_in", "item_in", "opening_stock", "opname_in", "transfer_in", "return", "reversal_in", "transfer_reversal_in"}:
                direction = "in"
            else:
                direction = "out"
        result.append({
            "id": m.id, 
            "date": str(m.date),
            "item": {
                "id": m.item_id,
                "name": item.name if item else "Item Dihapus",
                "code": item.code if item else "-"
            },
            "type": m.type,
            "category": category,
            "direction": direction,
            "qty": m.qty,
            "qty_before": m.qty_before, 
            "qty_after": m.qty_after,
            "reference": m.reference, 
            "notes": m.notes,
            "warehouse": {"id": m.warehouse_id, "name": m.warehouse.name} if m.warehouse else None,
            "counterparty_warehouse": {"id": m.counterparty_warehouse_id, "name": m.counterparty_warehouse.name} if m.counterparty_warehouse else None,
            "warehouse_qty_before": m.warehouse_qty_before,
            "warehouse_qty_after": m.warehouse_qty_after,
            "counterparty_qty_before": m.counterparty_qty_before,
            "counterparty_qty_after": m.counterparty_qty_after,
        })
    return result


# ─── 2. STOK MENIPIS (VERSI MULTI-CABANG) ──────────────────────────────────
@router.get("/low-stock")
def get_low_stock(warehouse_id: Optional[int] = None, status: Optional[str] = None,
                  category_id: Optional[int] = None,
                  db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if warehouse_id is None:
        default = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == current_user.active_branch_id,
            models.Warehouse.is_active == True,
        ).order_by(models.Warehouse.is_default.desc(), models.Warehouse.name).first()
        warehouse_id = default.id if default else None
    return get_low_stock_items(db, current_user.active_branch_id, warehouse_id=warehouse_id,
                               status=status, category_id=category_id, per_warehouse=True)


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
    supplier_id: Optional[int] = None,
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
    q = db.query(models.Item).filter(models.Item.is_active == True)
    if item_id:
        q = q.filter(models.Item.id == item_id)
    if supplier_id:
        supplier = db.query(models.Supplier).filter(
            models.Supplier.id == supplier_id,
            models.Supplier.is_active == True,
        ).first()
        if not supplier:
            raise HTTPException(404, "Supplier tidak ditemukan atau nonaktif")
        q = q.join(
            models.ItemSupplier,
            models.ItemSupplier.item_id == models.Item.id,
        ).filter(models.ItemSupplier.supplier_id == supplier_id)
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
        source_item = get_stock_source_item(db, item)
        warehouse_stock_row = db.query(models.WarehouseStock).filter(
            models.WarehouseStock.warehouse_id == warehouse.id,
            models.WarehouseStock.item_id == source_item.id,
        ).first()
        source_qty = inventory_document_service.warehouse_stock(db, warehouse.id, source_item.id)
        qty = get_effective_stock_from_source(item, source_qty)
        source_fifo_qty = inventory_document_service.total_remaining(db, source_item.id, warehouse.id)
        fifo_qty = get_effective_stock_from_source(item, source_fifo_qty)
        suggested_cost, cost_source = inventory_document_service.suggested_inbound_cost(
            db, warehouse.id, source_item
        )
        result.append({
            "id": item.id,
            "code": item.code,
            "barcode": item.barcode,
            "name": item.name,
            "unit": item.unit.abbreviation if item.unit else None,
            "stock": qty if show_stock else None,
            "buy_price": float(item.buy_price or 0) if show_cost else None,
            "fifo_qty": fifo_qty,
            "fifo_drift": round(fifo_qty - qty, 4),
            "suggested_cost": suggested_cost if show_cost else None,
            "cost_source": cost_source if show_cost else None,
            "cost_editable": has_permission(db, current_user, "inventory.account_override", "view"),
            "tracks_serial": bool(source_item.tracks_serial),
            "stock_item_id": source_item.id,
            "conversion_factor": get_conversion_factor(item) if is_virtual_variant(item) else 1.0,
            "master_min_stock": float(item.min_stock or 0),
            "warehouse_min_stock": (
                float(warehouse_stock_row.min_stock)
                if warehouse_stock_row and warehouse_stock_row.min_stock is not None
                else None
            ),
            "effective_min_stock": float(
                warehouse_stock_row.min_stock
                if warehouse_stock_row and warehouse_stock_row.min_stock is not None
                else item.min_stock or 0
            ),
            "uses_master_default": not warehouse_stock_row or warehouse_stock_row.min_stock is None,
            "snapshot_token": inventory_document_service.inventory_state_token(
                db, warehouse.id, source_item.id, source_qty
            ),
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


@router.get("/reconciliation-preview")
def reconciliation_preview(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return reconciliation_service.preview(db, warehouse_id, current_user)


@router.post("/reconciliations")
def apply_inventory_reconciliation(
    data: InventoryReconciliationApply,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        result = reconciliation_service.apply(db, data, current_user)
        db.commit()
        db.refresh(result)
        return reconciliation_service.serialize(result)
    except Exception:
        db.rollback()
        raise


@router.get("/reconciliations")
def list_inventory_reconciliations(
    warehouse_id: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    reconciliation_service.assert_access(db, current_user, "view")
    q = db.query(models.InventoryReconciliation).filter(
        models.InventoryReconciliation.branch_id == current_user.active_branch_id
    )
    if warehouse_id:
        q = q.filter(models.InventoryReconciliation.warehouse_id == warehouse_id)
    rows = q.options(
        joinedload(models.InventoryReconciliation.warehouse),
        joinedload(models.InventoryReconciliation.journal),
    ).order_by(models.InventoryReconciliation.id.desc()).limit(min(max(limit, 1), 200)).all()
    return [reconciliation_service.serialize(row) for row in rows]


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
        q = q.outerjoin(models.InventoryDocumentLine).outerjoin(
            models.Item,
            models.Item.id == models.InventoryDocumentLine.item_id,
        ).filter(
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
            snapshot_token=inventory_document_service.inventory_state_token(
                db, warehouse.id, data.item_id, current
            ),
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
    if gudang_aktif:
        adjust_warehouse_stock(db, gudang_aktif.id, item.id, diff)
    else:
        item.stock += diff

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


class QuarantineCreate(BaseModel):
    date: date
    warehouse_id: int
    item_id: int
    qty: float
    source_type: str = "unknown"
    source_id: Optional[int] = None
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    serials: list[str] = Field(default_factory=list)


class QuarantineBatchLine(BaseModel):
    item_id: int
    qty: float
    serials: list[str] = Field(default_factory=list)


class QuarantineBatchCreate(BaseModel):
    date: date
    warehouse_id: int
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    lines: list[QuarantineBatchLine] = Field(min_length=1, max_length=200)


class WarehouseMinimumUpdate(BaseModel):
    warehouse_id: int
    item_id: int
    min_stock: Optional[float] = None


@router.put("/low-stock-settings")
def update_warehouse_minimum(data: WarehouseMinimumUpdate,
                             db: Session = Depends(get_db),
                             current_user: models.User = Depends(get_current_user)):
    if not has_permission(db, current_user, "inventory.item_in", "update"):
        raise HTTPException(403, "Akses mengubah batas stok ditolak")
    warehouse = db.query(models.Warehouse).get(data.warehouse_id)
    item = db.query(models.Item).get(data.item_id)
    if not warehouse or warehouse.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Gudang tidak tersedia pada cabang aktif")
    if not item:
        raise HTTPException(404, "Barang tidak ditemukan")
    if data.min_stock is not None and data.min_stock < 0:
        raise HTTPException(400, "Stok minimum tidak boleh negatif")
    source = get_stock_source_item(db, item)
    row = db.query(models.WarehouseStock).filter_by(
        warehouse_id=warehouse.id, item_id=source.id).with_for_update().first()
    if not row:
        row = models.WarehouseStock(warehouse_id=warehouse.id, item_id=source.id, stock=0)
        db.add(row)
    row.min_stock = data.min_stock
    db.commit()
    return {"warehouse_id": warehouse.id, "item_id": item.id,
            "min_stock": row.min_stock, "uses_master_default": row.min_stock is None}


class QuarantineResolve(BaseModel):
    action: str
    qty: float
    notes: Optional[str] = None
    serials: list[str] = Field(default_factory=list)


def _serialize_quarantine(row: models.InventoryQuarantine):
    return {
        "id": row.id, "number": row.number, "date": str(row.date),
        "warehouse_id": row.warehouse_id,
        "warehouse_name": row.warehouse.name if row.warehouse else "-",
        "item_id": row.item_id, "item_code": row.item.code if row.item else "-",
        "item_name": row.item.name if row.item else "Item dihapus",
        "tracks_serial": bool(row.item.tracks_serial) if row.item else False,
        "unit": row.item.unit.abbreviation if row.item and row.item.unit else None,
        "qty": row.qty, "qty_open": row.qty_open, "unit_cost": row.unit_cost,
        "total_cost": row.total_cost, "source_type": row.source_type,
        "source_id": row.source_id, "supplier_id": row.supplier_id,
        "supplier_name": row.supplier.name if row.supplier else None,
        "status": row.status, "notes": row.notes,
    }


@router.get("/quarantines")
def list_quarantines(status: Optional[str] = "open", db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    if not has_permission(db, current_user, "inventory.item_out", "view"):
        raise HTTPException(403, "Akses karantina barang ditolak")
    q = db.query(models.InventoryQuarantine).filter(
        models.InventoryQuarantine.branch_id == current_user.active_branch_id
    )
    if status:
        q = q.filter(models.InventoryQuarantine.status == status)
    return [_serialize_quarantine(row) for row in q.order_by(models.InventoryQuarantine.id.desc()).all()]


def _quarantine_supplier(db: Session, supplier_id: Optional[int]):
    if not supplier_id:
        return None
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id,
        models.Supplier.is_active == True,
    ).first()
    if not supplier:
        raise HTTPException(404, "Supplier tidak ditemukan atau nonaktif")
    return supplier


def _create_quarantine_row(
    db: Session,
    *,
    date_value: date,
    warehouse: models.Warehouse,
    item_id: int,
    qty_value: float,
    supplier_id: Optional[int],
    notes: Optional[str],
    serials: list[str],
    current_user: models.User,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
):
    if not qty_value or not math.isfinite(float(qty_value)) or qty_value <= 0:
        raise HTTPException(400, "Jumlah karantina harus lebih dari nol")
    entered = db.query(models.Item).filter(
        models.Item.id == item_id,
        models.Item.is_active == True,
    ).first()
    if not entered:
        raise HTTPException(404, "Barang tidak ditemukan")
    if supplier_id and not db.query(models.ItemSupplier).filter(
        models.ItemSupplier.item_id == entered.id,
        models.ItemSupplier.supplier_id == supplier_id,
    ).first():
        raise HTTPException(400, f"{entered.name} tidak terhubung dengan supplier yang dipilih")

    item = get_stock_source_item(db, entered)
    factor = get_conversion_factor(entered) if is_virtual_variant(entered) else 1.0
    qty = float(qty_value) * factor
    before = inventory_document_service.warehouse_stock(db, warehouse.id, item.id)
    if before + 1e-9 < qty:
        raise HTTPException(400, f"Stok {entered.name} tidak cukup")

    from ..services.inventory_fifo import consume_fifo
    from ..services.serials import transition_serials
    from ..services.journal_service import create_quarantine_journal

    allocations = consume_fifo(db, item_id=item.id, warehouse_id=warehouse.id, qty=qty)
    total = sum(float(q) * float(cost or 0) for _, q, cost in allocations)
    number = f"QRT-{date_value:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    row = models.InventoryQuarantine(
        number=number, date=date_value, branch_id=warehouse.branch_id,
        warehouse_id=warehouse.id, item_id=item.id, qty=qty, qty_open=qty,
        unit_cost=total / qty, total_cost=total,
        source_type=(source_type or ("supplier" if supplier_id else "unknown")).strip().lower(),
        source_id=source_id, supplier_id=supplier_id,
        notes=(notes or "").strip() or None, created_by=current_user.id,
    )
    db.add(row)
    db.flush()
    perubahan = ubah_stok_gudang(
        db, warehouse_id=warehouse.id, item_id=item.id, delta=-qty
    )
    db.add(models.StockMovement(
        date=date_value, created_at=get_local_datetime(), item_id=item.id,
        branch_id=warehouse.branch_id, warehouse_id=warehouse.id,
        type="quarantine_out", event_category="quarantine_out", direction="out",
        source_type="inventory_quarantine", source_id=row.id, qty=qty,
        qty_before=perubahan.cabang_sebelum,
        qty_after=perubahan.cabang_sesudah,
        warehouse_qty_before=perubahan.gudang_sebelum,
        warehouse_qty_after=perubahan.gudang_sesudah,
        reference=number, notes=row.notes or "Barang ditemukan rusak",
    ))
    transition_serials(db, item=item, qty=qty, serial_values=serials,
        from_statuses={"in_stock"}, to_status="quarantine",
        from_warehouse_id=warehouse.id, to_warehouse=warehouse,
        reference_type="inventory_quarantine", reference_id=row.id,
        reference_number=number, user_id=current_user.id, event_type="quarantined")
    create_quarantine_journal(db, date_val=date_value, number_ref=number,
                              total=total, user_id=current_user.id,
                              branch_id=warehouse.branch_id)
    return row


@router.post("/quarantines")
def create_quarantine(data: QuarantineCreate, db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    if not has_permission(db, current_user, "inventory.item_out", "create"):
        raise HTTPException(403, "Akses karantina barang ditolak")
    warehouse = db.query(models.Warehouse).filter(models.Warehouse.id == data.warehouse_id).first()
    if not warehouse or warehouse.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Gudang tidak tersedia pada cabang aktif")
    _quarantine_supplier(db, data.supplier_id)
    assert_books_open(db, warehouse.branch_id, data.date, "Karantina barang")
    try:
        row = _create_quarantine_row(
            db, date_value=data.date, warehouse=warehouse, item_id=data.item_id,
            qty_value=data.qty, supplier_id=data.supplier_id, notes=data.notes,
            serials=data.serials, current_user=current_user,
            source_type=data.source_type, source_id=data.source_id,
        )
        db.commit()
        db.refresh(row)
        return _serialize_quarantine(row)
    except Exception:
        db.rollback()
        raise


@router.post("/quarantines/batch")
def create_quarantine_batch(data: QuarantineBatchCreate, db: Session = Depends(get_db),
                            current_user: models.User = Depends(get_current_user)):
    if not has_permission(db, current_user, "inventory.item_out", "create"):
        raise HTTPException(403, "Akses karantina barang ditolak")
    item_ids = [line.item_id for line in data.lines]
    if len(item_ids) != len(set(item_ids)):
        raise HTTPException(400, "Barang karantina tidak boleh duplikat")
    warehouse = db.query(models.Warehouse).filter(models.Warehouse.id == data.warehouse_id).first()
    if not warehouse or warehouse.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Gudang tidak tersedia pada cabang aktif")
    _quarantine_supplier(db, data.supplier_id)
    assert_books_open(db, warehouse.branch_id, data.date, "Karantina barang")
    try:
        rows = [
            _create_quarantine_row(
                db, date_value=data.date, warehouse=warehouse, item_id=line.item_id,
                qty_value=line.qty, supplier_id=data.supplier_id, notes=data.notes,
                serials=line.serials, current_user=current_user,
            )
            for line in data.lines
        ]
        db.commit()
        for row in rows:
            db.refresh(row)
        return {"count": len(rows), "items": [_serialize_quarantine(row) for row in rows]}
    except Exception:
        db.rollback()
        raise


@router.post("/quarantines/{quarantine_id}/resolve")
def resolve_quarantine(quarantine_id: int, data: QuarantineResolve,
                       db: Session = Depends(get_db),
                       current_user: models.User = Depends(get_current_user)):
    if not has_permission(db, current_user, "inventory.item_out", "update"):
        raise HTTPException(403, "Akses menyelesaikan karantina ditolak")
    row = db.query(models.InventoryQuarantine).filter(
        models.InventoryQuarantine.id == quarantine_id
    ).with_for_update().first()
    if not row or row.branch_id != current_user.active_branch_id:
        raise HTTPException(404, "Karantina tidak ditemukan")
    action = (data.action or "").strip().lower()
    if action == "supplier_return":
        raise HTTPException(409, "Retur supplier wajib dibuat melalui menu Retur")
    if action not in {"restore", "writeoff"}:
        raise HTTPException(400, "Tindakan karantina tidak valid")
    if data.qty <= 0 or data.qty > float(row.qty_open or 0) + 1e-9:
        raise HTTPException(400, "Jumlah penyelesaian melebihi saldo karantina")
    today = get_local_date()
    assert_books_open(db, row.branch_id, today, "Penyelesaian karantina")
    try:
        from ..services.inventory_fifo import add_batch
        from ..services.serials import assert_quarantine_serials, transition_serials
        from ..services.journal_service import create_quarantine_resolution_journal
        from ..services.quarantine_movements import record_quarantine_movement
        qty = float(data.qty)
        total = qty * float(row.unit_cost or 0)
        quarantine_before = float(row.qty_open or 0)
        quarantine_after = quarantine_before - qty
        assert_quarantine_serials(
            db, quarantine=row, qty=qty, serial_values=data.serials,
        )
        if action == "restore":
            perubahan = ubah_stok_gudang(
                db, warehouse_id=row.warehouse_id, item_id=row.item_id, delta=qty
            )
            add_batch(db, item_id=row.item_id, warehouse_id=row.warehouse_id,
                      qty=qty, unit_cost=row.unit_cost, received_date=today,
                      supplier_id=row.supplier_id)
            transition_serials(db, item=row.item, qty=qty, serial_values=data.serials,
                from_statuses={"quarantine"}, to_status="in_stock",
                from_warehouse_id=row.warehouse_id, to_warehouse=row.warehouse,
                reference_type="inventory_quarantine", reference_id=row.id,
                reference_number=f"RES-{row.number}", user_id=current_user.id,
                event_type="quarantine_restored")
            db.add(models.StockMovement(
                date=today, created_at=get_local_datetime(), item_id=row.item_id,
                branch_id=row.branch_id, warehouse_id=row.warehouse_id,
                type="quarantine_restore", event_category="quarantine_restore", direction="in",
                source_type="inventory_quarantine", source_id=row.id, qty=qty,
                qty_before=perubahan.cabang_sebelum,
                qty_after=perubahan.cabang_sesudah,
                warehouse_qty_before=perubahan.gudang_sebelum,
                warehouse_qty_after=perubahan.gudang_sesudah,
                reference=f"RES-{row.number}", notes=data.notes,
            ))
        else:
            transition_serials(db, item=row.item, qty=qty, serial_values=data.serials,
                from_statuses={"quarantine"},
                to_status="removed",
                from_warehouse_id=row.warehouse_id, to_warehouse=None,
                reference_type="inventory_quarantine", reference_id=row.id,
                reference_number=f"RES-{row.number}", user_id=current_user.id,
                event_type=action)
            record_quarantine_movement(
                db, quarantine=row, qty=qty, category="quarantine_writeoff",
                date_value=today, reference=f"RES-{row.number}",
                quarantine_before=quarantine_before, quarantine_after=quarantine_after,
                notes=data.notes or "Barang karantina dihapuskan",
            )
        create_quarantine_resolution_journal(db, date_val=today,
            number_ref=f"RES-{row.number}", total=total, action=action,
            user_id=current_user.id, branch_id=row.branch_id)
        row.qty_open = quarantine_after
        row.status = "closed" if row.qty_open <= 1e-9 else "partial"
        if data.notes:
            row.notes = f"{row.notes or ''}\n{action}: {data.notes}".strip()
        db.commit()
        return _serialize_quarantine(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
