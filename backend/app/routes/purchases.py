from datetime import date
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user, get_query
from ..database import get_db
from ..services.journal_service import create_purchase_payment_journal, create_branch_receiving_journal, create_transfer_receipt_journal
from ..services.purchase_flow import (
    PUSAT_BRANCH_ID,
    cancel_purchase_flow,
    create_branch_request as service_create_branch_request,
    create_supplier_purchase,
    finalize_request_to_purchase,
    get_local_date,
    get_local_datetime,
    update_draft_purchase,
    receive_branch_stock,
    refresh_source_request_status,
)

router = APIRouter()


class BranchReceiptLine(BaseModel):
    item_id: int
    good_qty: float = Field(ge=0)
    damaged_qty: float = Field(default=0, ge=0)
    missing_qty: float = Field(default=0, ge=0)
    notes: Optional[str] = None
    good_serials: list[str] = Field(default_factory=list)
    damaged_serials: list[str] = Field(default_factory=list)


class BranchReceiptPayload(BaseModel):
    items: list[BranchReceiptLine] = Field(default_factory=list)
    notes: Optional[str] = None


def _next_number(db: Session, prefix: str, model, current_user: models.User) -> str:
    today = get_local_date()
    branch_id = current_user.active_branch_id or 0
    prefix_full = f"{prefix}-C{branch_id}-{today.strftime('%Y%m%d')}"
    last = db.query(model).filter(model.number.like(f"{prefix_full}%")).order_by(model.id.desc()).first()

    if last and len(last.number) >= 4:
        try:
            seq = int(last.number[-4:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1

    return f"{prefix_full}{seq:04d}"


# Mengambil daftar transaksi pembelian dengan filter (tanggal, supplier, status, dll)
@router.get("/", response_model=list[schemas.PurchaseOut])
def get_purchases(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    supplier_id: Optional[int] = None,
    status: Optional[str] = None,
    document_type: Optional[str] = None,
    is_branch_request: Optional[bool] = None,
    target_branch_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.Purchase)
    branch_id = current_user.active_branch_id

    if branch_id == PUSAT_BRANCH_ID:
        q = q.filter(
            (models.Purchase.branch_id == PUSAT_BRANCH_ID)
            | (models.Purchase.target_branch_id == PUSAT_BRANCH_ID)
        )
    else:
        # 🛡️ FIX: Cabang hanya boleh melihat faktur yang mereka buat sendiri (branch_id).
        # Faktur kiriman dari Pusat (target_branch_id) hanya muncul di po.html (Riwayat Request).
        q = q.filter(models.Purchase.branch_id == branch_id)

    if start_date:
        q = q.filter(models.Purchase.date >= start_date)
    if end_date:
        q = q.filter(models.Purchase.date <= end_date)
    if supplier_id:
        q = q.filter(models.Purchase.supplier_id == supplier_id)
    if document_type:
        q = q.filter(models.Purchase.document_type == document_type)
    if target_branch_id:
        q = q.filter(models.Purchase.target_branch_id == target_branch_id)

    if is_branch_request is None:
        q = q.filter(models.Purchase.is_branch_request == False)
        if status:
            q = q.filter(models.Purchase.status == status)
        else:
            q = q.filter(models.Purchase.status != "pending")
    else:
        q = q.filter(models.Purchase.is_branch_request == is_branch_request)
        if status:
            q = q.filter(models.Purchase.status == status)

    return q.order_by(models.Purchase.id.desc()).offset(skip).limit(limit).all()


# Mengambil daftar barang yang tersedia untuk dibeli, opsional difilter berdasarkan supplier
@router.get("/items/")
def get_items_for_purchase(
    supplier_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.virtual_units import get_conversion_factor, get_effective_buy_price, is_virtual_variant
    from sqlalchemy.orm import joinedload

    query = db.query(models.Item).options(
        joinedload(models.Item.suppliers),
        joinedload(models.Item.category),
        joinedload(models.Item.unit),
    ).filter(models.Item.is_active == True)

    supplier_default_ppn = 0.0
    if supplier_id:
        supplier = db.get(models.Supplier, supplier_id)
        supplier_default_ppn = max(0.0, float(getattr(supplier, "PpnSupplier", 0) or 0)) if supplier else 0.0

    results = []
    for item in query.all():
        virtual = is_virtual_variant(item)
        parent = db.get(models.Item, item.parent_item_id) if virtual else None
        supplier_details = item.supplier_details or (parent.supplier_details if parent else [])
        visible_suppliers = item.suppliers or (parent.suppliers if parent else [])
        if supplier_id and not any(detail.supplier_id == supplier_id for detail in supplier_details):
            continue
        item_data = {
            "id": item.id,
            "code": item.code,
            "barcode": item.barcode,
            "name": item.name,
            "buy_price": get_effective_buy_price(db, item) if virtual else item.buy_price,
            "sell_price": item.sell_price,
            "profit_margin": item.profit_margin,
            "tracks_serial": bool(item.tracks_serial),
            "is_virtual_variant": virtual,
            "conversion_factor_to_parent": get_conversion_factor(item) if virtual else 1,
            "unit_name": item.unit.name if item.unit else "pcs",
            "category_name": item.category.name if item.category else "-",
            "suppliers": [{"id": s.id, "name": s.name} for s in visible_suppliers],
            "supplier_details": [
                {
                    "supplier_id": s.supplier_id,
                    "buy_price": s.buy_price,
                    "ppn_type": s.ppn_type or "included",
                    "ppn_percent": s.ppn_percent or 0,
                }
                for s in supplier_details
            ],
            # Default PPN (dipakai grid bila supplier difilter & punya setelan khusus)
            "ppn_type": "included",
            "ppn_percent": 0,
        }

        if supplier_id:
            spec = next((s for s in supplier_details if s.supplier_id == supplier_id), None)
            if spec:
                # buy_price 0/None pada baris supplier dianggap "belum di-set" → tetap pakai
                # harga beli umum item, jangan ditimpa 0 (banyak baris lama berisi 0).
                if spec.buy_price:
                    item_data["buy_price"] = spec.buy_price
                if spec.barcode:
                    item_data["barcode"] = spec.barcode
                item_data["ppn_type"] = spec.ppn_type or "included"
                if (spec.ppn_type or "").lower() == "none":
                    # Tanpa PPN adalah override eksplisit, bukan tarif yang belum diisi.
                    item_data["ppn_type"] = "none"
                    item_data["ppn_percent"] = 0
                elif spec.ppn_percent and float(spec.ppn_percent) > 0:
                    item_data["ppn_percent"] = float(spec.ppn_percent)
                elif item.ppn_percent is not None:
                    # Ikuti override tarif pada master barang, termasuk 0% eksplisit.
                    item_data["ppn_percent"] = float(item.ppn_percent)
                else:
                    # Baris supplier lama sering masih 0 karena belum pernah diisi.
                    # Dalam kasus ini gunakan default PPN supplier, bukan 0% palsu.
                    item_data["ppn_percent"] = supplier_default_ppn
            elif item.ppn_percent is not None:
                item_data["ppn_percent"] = float(item.ppn_percent)
            else:
                item_data["ppn_percent"] = supplier_default_ppn

        results.append(item_data)

    return results


# Mengambil riwayat harga beli sebuah item (opsional difilter per supplier)
# Dipakai oleh halaman detail_item.html untuk tabel "History Harga Beli untuk Supplier".
@router.get("/item-history/")
def get_item_purchase_history(
    item_id: int,
    supplier_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from sqlalchemy.orm import joinedload

    query = (
        db.query(models.PurchaseItem)
        .join(models.Purchase, models.PurchaseItem.purchase_id == models.Purchase.id)
        .options(
            joinedload(models.PurchaseItem.item).joinedload(models.Item.unit),
            joinedload(models.PurchaseItem.purchase).joinedload(models.Purchase.supplier),
        )
        .filter(models.PurchaseItem.item_id == item_id)
    )

    if supplier_id:
        query = query.filter(models.Purchase.supplier_id == supplier_id)

    rows = query.order_by(models.Purchase.date.desc(), models.PurchaseItem.id.desc()).limit(20).all()

    hasil = []
    for pi in rows:
        harga = pi.buy_price or 0
        disc1 = pi.disc1 or 0
        disc2 = pi.disc2 or 0
        disc3 = pi.disc3 or 0
        disc4 = pi.disc4 or 0
        # Potongan bertingkat: tiap potongan memotong harga hasil potongan sebelumnya.
        harga_setelah_potongan = (
            harga
            * (1 - disc1 / 100)
            * (1 - disc2 / 100)
            * (1 - disc3 / 100)
            * (1 - disc4 / 100)
        )
        hasil.append({
            "tanggal": pi.purchase.date.isoformat() if pi.purchase and pi.purchase.date else None,
            "supplier": pi.purchase.supplier.name if pi.purchase and pi.purchase.supplier else "-",
            "jumlah": pi.qty,
            "satuan": pi.item.unit.name if pi.item and pi.item.unit else "pcs",
            "harga": harga,
            "potongan": disc1,
            "harga_setelah_potongan": round(harga_setelah_potongan, 2),
        })

    return hasil


# Mengambil detail lengkap satu transaksi pembelian berdasarkan ID
@router.get("/{pid}", response_model=schemas.PurchaseOut)
def get_purchase(
    pid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from sqlalchemy.orm import joinedload
    obj = get_query(db, models.Purchase, current_user).options(
        joinedload(models.Purchase.items).joinedload(models.PurchaseItem.item).joinedload(models.Item.suppliers),
        joinedload(models.Purchase.items).joinedload(models.PurchaseItem.item).joinedload(models.Item.supplier_details),
        joinedload(models.Purchase.items).joinedload(models.PurchaseItem.item).joinedload(models.Item.category),
        joinedload(models.Purchase.items).joinedload(models.PurchaseItem.item).joinedload(models.Item.unit),
    ).filter(models.Purchase.id == pid).first()
    
    if not obj:
        raise HTTPException(404, "Pembelian tidak ditemukan")
    
    # If it's a branch request, also fetch its fulfillment drafts
    if obj.is_branch_request:
        obj.fulfillment_drafts = db.query(models.Purchase).filter(models.Purchase.from_po_id == obj.id).all()
        reserved = {}
        for child in obj.fulfillment_drafts:
            if child.status == "cancelled":
                continue
            for line in child.items:
                if not line.source_request_item_id:
                    continue
                amount = line.qty_ordered if child.status == "draft" else (line.qty_received or line.qty)
                reserved[line.source_request_item_id] = float(reserved.get(line.source_request_item_id, 0)) + float(amount or 0)
        for line in obj.items:
            line.qty_outstanding = max(
                0.0,
                float(line.qty_ordered or line.qty or 0) - float(reserved.get(line.id, 0) or 0),
            )
        
    return obj


# Membuat transaksi pembelian baru (Bisa berupa Request Cabang, Fulfillment PO, Draft, atau Pembelian Langsung)
@router.post("/")
def create_purchase(
    data: schemas.PurchaseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from .accounting import assert_books_open

    tanggal = data.date if data.date else get_local_date()

    if data.is_branch_request:
        number = data.number or _next_number(db, "REQ", models.Purchase, current_user)
        purchase = service_create_branch_request(db, data=data, current_user=current_user, number=number)
        db.commit()
        db.refresh(purchase)
        return purchase

    if data.from_po_id:
        source_request = db.query(models.Purchase).filter(models.Purchase.id == data.from_po_id).with_for_update().first()
        if not source_request:
            raise HTTPException(404, "Request PO sumber tidak ditemukan")

        if source_request.is_branch_request:
            # INTER-BRANCH PO: Must be fulfilled by Toko Pusat
            assert_books_open(db, PUSAT_BRANCH_ID, tanggal, "Fulfillment request cabang")
            number = _next_number(db, "PUR", models.Purchase, current_user)
            purchase = finalize_request_to_purchase(
                db,
                source_request=source_request,
                data=data,
                current_user=current_user,
                number=number,
            )
        else:
            # NORMAL SUPPLIER PO: Fulfilled by the requesting branch itself
            allowed_branch = source_request.target_branch_id or source_request.branch_id
            if current_user.active_branch_id != allowed_branch:
                raise HTTPException(403, f"Hanya cabang {allowed_branch} yang bisa menerima barang dari PO ini.")
            
            assert_books_open(db, current_user.active_branch_id, tanggal, "Fulfillment PO Supplier")
            number = _next_number(db, "PUR", models.Purchase, current_user)
            purchase = create_supplier_purchase(
                db,
                data=data,
                current_user=current_user,
                number=number,
                branch_id=current_user.active_branch_id,
                target_branch_id=current_user.active_branch_id,
                source_request=source_request,
            )

        db.commit()
        db.refresh(purchase)
        return purchase

    assert_books_open(db, current_user.active_branch_id, tanggal, "Pembelian")

    number = data.number or _next_number(db, "PUR", models.Purchase, current_user)

    # Jika frontend mengirim status 'draft', simpan draft TANPA jurnal/stok.
    if data.status == "draft":
        from ..services.purchase_flow import (
            calculate_purchase_totals,
            add_purchase_items,
            validate_purchase_items,
        )
        from ..services.tax_context import normalize_purchase_tax_type

        validate_purchase_items(db, data)
        totals = calculate_purchase_totals(data, received=False)
        tax_type = normalize_purchase_tax_type(
            data.tax_type, is_tax_included=data.is_tax_included
        )
        purchase = models.Purchase(
            number=number,
            date=tanggal,
            due_date=data.due_date,
            branch_id=current_user.active_branch_id,
            created_at=get_local_datetime(),
            supplier_id=data.supplier_id,
            subtotal=totals["subtotal"],
            discount=totals["discount"],
            tax=totals["tax"],
            tax_percent=data.tax_percent or 0,
            is_tax_included=(tax_type == "include"),
            tax_type=tax_type,
            total=totals["total"],
            paid=0,
            status="draft",
            document_type=data.document_type,
            notes=data.notes,
            created_by=current_user.id,
            is_branch_request=False,
            target_branch_id=data.target_branch_id,
        )
        db.add(purchase)
        db.flush()
        add_purchase_items(db, purchase, data, received=False)
        db.commit()
        db.refresh(purchase)
        return purchase

    purchase = create_supplier_purchase(
        db,
        data=data,
        current_user=current_user,
        number=number,
        branch_id=current_user.active_branch_id,
        target_branch_id=None,
    )
    db.commit()
    db.refresh(purchase)
    
    # Trigger shipping transition if all drafts finalized
    if purchase.from_po_id:
        from ..services.purchase_flow import check_and_update_source_status
        check_and_update_source_status(db, purchase.from_po_id)
        
    return purchase


# Memecah request cabang menjadi beberapa draft pembelian berdasarkan supplier masing-masing item
@router.post("/{pid}/split-fulfill")
def split_fulfill_request(
    pid: int,
    data: schemas.SplitFulfillRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    source = db.query(models.Purchase).filter(models.Purchase.id == pid).with_for_update().first()
    if not source:
        raise HTTPException(404, "Request cabang tidak ditemukan")
    if not source.is_branch_request:
        raise HTTPException(400, "Dokumen bukan request cabang")
    if source.status not in ("pending", "processing", "partially_received", "shipping"):
        raise HTTPException(400, f"Request sudah berstatus {source.status}")

    source_lines = {line.id: line for line in source.items}
    source_by_item = {line.item_id: line for line in source.items}
    reserved = {}
    children = db.query(models.Purchase).filter(
        models.Purchase.from_po_id == source.id,
        models.Purchase.status != "cancelled",
    ).all()
    for child in children:
        for line in child.items:
            if not line.source_request_item_id:
                continue
            amount = line.qty_ordered if child.status == "draft" else (line.qty_received or line.qty)
            reserved[line.source_request_item_id] = float(reserved.get(line.source_request_item_id, 0)) + float(amount or 0)
    for it in data.items:
        src = source_lines.get(it.source_request_item_id) if it.source_request_item_id else source_by_item.get(it.item_id)
        if not src or src.item_id != it.item_id:
            raise HTTPException(400, f"Barang {it.item_id} tidak berasal dari request ini")
        outstanding = float(src.qty_ordered or src.qty or 0) - float(reserved.get(src.id, 0) or 0)
        if it.qty <= 0 or it.qty > outstanding + 1e-9:
            raise HTTPException(400, f"Qty fulfillment melebihi sisa request ({max(0, outstanding)})")
        it.source_request_item_id = src.id
        reserved[src.id] = float(reserved.get(src.id, 0) or 0) + float(it.qty)

    # Group items by supplier_id
    from collections import defaultdict
    groups = defaultdict(list)
    for it in data.items:
        groups[it.supplier_id].append(it)

    created_purchases = []
    tanggal = get_local_date()
    
    for supplier_id, items in groups.items():
        number = _next_number(db, "PUR", models.Purchase, current_user)
        
        # Calculate subtotal for this group
        subtotal = sum(it.qty * it.buy_price for it in items)
        purchase = models.Purchase(
            number=number,
            date=tanggal,
            branch_id=PUSAT_BRANCH_ID,
            created_at=get_local_datetime(),
            supplier_id=supplier_id,
            subtotal=subtotal,
            discount=0,
            tax=0,
            tax_percent=0,
            is_tax_included=True,
            tax_type=source.tax_type,
            total=subtotal,
            paid=0,
            status="draft",
            notes=data.notes or f"Fulfillment draft untuk {source.number}",
            created_by=current_user.id,
            is_branch_request=False,
            target_branch_id=source.branch_id,
            from_po_id=source.id,
            fulfillment_status="draft",
        )
        db.add(purchase)
        db.flush()
        
        for it in items:
            db.add(models.PurchaseItem(
                purchase_id=purchase.id,
                item_id=it.item_id,
                qty=it.qty,
                qty_ordered=it.qty,
                qty_received=0,
                buy_price=it.buy_price,
                total=it.qty * it.buy_price,
                source_request_item_id=it.source_request_item_id,
            ))
        created_purchases.append(purchase)

    refresh_source_request_status(db, source)
    db.commit()
    return {"message": f"Berhasil membuat {len(created_purchases)} draft pembelian", "ids": [p.id for p in created_purchases]}


# Menandai request cabang (PO antar cabang) telah diterima oleh cabang peminta
@router.post("/{pid}/receive-branch")
def receive_branch_request(
    pid: int,
    data: Optional[BranchReceiptPayload] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    purchase = db.query(models.Purchase).filter(models.Purchase.id == pid).with_for_update().first()
    if not purchase or not purchase.from_po_id:
        raise HTTPException(404, "Kiriman fulfillment tidak ditemukan")
    source = db.query(models.Purchase).filter(models.Purchase.id == purchase.from_po_id).with_for_update().first()
    if not source or not source.is_branch_request:
        raise HTTPException(400, "Dokumen sumber bukan request cabang")
    if purchase.target_branch_id != current_user.active_branch_id or source.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Hanya cabang peminta yang dapat melakukan ACC penerimaan barang")
    if purchase.fulfillment_status == "received" or purchase.is_received_by_branch:
        raise HTTPException(409, "Kiriman ini sudah pernah di-ACC")
    if purchase.fulfillment_status not in {"shipping", "disputed"}:
        raise HTTPException(400, f"Status kiriman saat ini '{purchase.fulfillment_status}'")
    transfer = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.source_purchase_id == purchase.id,
        models.WarehouseTransfer.transfer_kind == "po_fulfillment",
    ).with_for_update().first()
    if not transfer or transfer.status not in {"shipping", "disputed"}:
        raise HTTPException(409, "Dokumen mutasi kiriman tidak tersedia atau sudah diterima")
    
    # 2. Add only physically good stock for Branch. Backwards-compatible
    # clients without a body still accept the whole shipment as good.
    # Menggunakan source items karena cabang mungkin telah melakukan clone barang atau mengubah qty
    local_datetime = get_local_datetime()
    local_date = get_local_date()
    
    supplied = {row.item_id: row for row in (data.items if data else [])}
    if len(supplied) != len(data.items if data else []):
        raise HTTPException(400, "Barang penerimaan tidak boleh ganda")
    originals = []
    inventory_value = quarantine_value = 0.0
    any_missing = False
    for transfer_line in transfer.items:
        receipt = supplied.get(transfer_line.item_id) if supplied else None
        remaining = float(transfer_line.qty or 0) - float(transfer_line.qty_received or 0) - float(transfer_line.qty_damaged or 0)
        good = float(receipt.good_qty if receipt else remaining)
        damaged = float(receipt.damaged_qty if receipt else 0)
        missing = float(receipt.missing_qty if receipt else 0)
        if abs(good + damaged + missing - remaining) > 1e-6:
            raise HTTPException(400, f"Total kondisi {transfer_line.item.name} harus {remaining:g}")
        purchase_line = transfer_line.purchase_item
        originals.append((purchase_line, purchase_line.qty, purchase_line.serials_json))
        purchase_line.qty = good
        if receipt and receipt.good_serials:
            import json
            purchase_line.serials_json = json.dumps(receipt.good_serials)
        transfer_line.qty_received = float(transfer_line.qty_received or 0) + good
        transfer_line.qty_damaged = float(transfer_line.qty_damaged or 0) + damaged
        transfer_line.qty_missing = missing
        transfer_line.receipt_notes = receipt.notes if receipt else None
        inventory_value += good * float(transfer_line.unit_cost or 0)
        quarantine_value += damaged * float(transfer_line.unit_cost or 0)
        any_missing = any_missing or missing > 1e-9 or damaged > 1e-9
    try:
        receive_branch_stock(db, purchase=purchase, target_branch_id=source.branch_id,
                             local_datetime=local_datetime, local_date=local_date)
    finally:
        for purchase_line, qty, serials_json in originals:
            purchase_line.qty = qty
            purchase_line.serials_json = serials_json
    if quarantine_value > 0:
        from ..services.purchase_flow import get_or_create_default_warehouse
        from ..services.serials import receive_serials, transition_serials
        warehouse = get_or_create_default_warehouse(db, source.branch_id)
        for transfer_line in transfer.items:
            receipt = supplied.get(transfer_line.item_id) if supplied else None
            damaged_now = float(receipt.damaged_qty if receipt else 0)
            if damaged_now <= 0:
                continue
            qrow = models.InventoryQuarantine(
                number=f"QRT-{transfer.number}-{transfer_line.id}-{uuid.uuid4().hex[:4].upper()}", date=local_date,
                branch_id=source.branch_id, warehouse_id=warehouse.id,
                item_id=transfer_line.item_id, qty=damaged_now,
                qty_open=damaged_now, unit_cost=transfer_line.unit_cost,
                total_cost=damaged_now * transfer_line.unit_cost,
                source_type="po_fulfillment", source_id=purchase.id,
                supplier_id=purchase.supplier_id, status="open",
                notes=receipt.notes, created_by=current_user.id,
            )
            db.add(qrow)
            db.flush()
            receive_serials(db, item=transfer_line.item, qty=damaged_now,
                serial_values=receipt.damaged_serials, warehouse=warehouse,
                reference_type="inventory_quarantine", reference_id=qrow.id,
                reference_number=qrow.number, user_id=current_user.id)
            transition_serials(db, item=transfer_line.item, qty=damaged_now,
                serial_values=receipt.damaged_serials, from_statuses={"in_stock"},
                to_status="quarantine", from_warehouse_id=warehouse.id,
                to_warehouse=warehouse, reference_type="inventory_quarantine",
                reference_id=qrow.id, reference_number=qrow.number,
                user_id=current_user.id, event_type="quarantined")

    create_transfer_receipt_journal(db, date_val=local_date, number_ref=transfer.number,
        inventory_total=inventory_value, quarantine_total=quarantine_value,
        user_id=current_user.id, branch_id=source.branch_id, credit_code="3-2100")

    transfer.status = "confirmed" if data is None else ("disputed" if any_missing else "received")
    transfer.received_at = local_datetime
    transfer.received_by = current_user.id
    transfer.dispute_notes = (data.notes if data else None) if any_missing else None
    purchase.fulfillment_status = "disputed" if any_missing else "received"
    purchase.is_received_by_branch = not any_missing
    refresh_source_request_status(db, source)
    db.commit()
    return {"message": "Penerimaan dicatat sesuai kondisi aktual.",
            "status": source.status, "fulfillment_status": purchase.fulfillment_status}


# Mencatat pembayaran cicilan atau pelunasan hutang supplier (Account Payable)
@router.post("/{pid}/pay")
def pay_purchase(
    pid: int,
    payment: schemas.PurchasePayment,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).with_for_update().first()
    if not obj:
        raise HTTPException(404, "Pembelian tidak ditemukan")
    if obj.is_branch_request:
        raise HTTPException(400, "Request PO bukan hutang supplier dan tidak boleh dibayar.")
    if obj.branch_id != current_user.active_branch_id:
        raise HTTPException(403, "Hutang supplier hanya boleh dibayar oleh cabang pemilik faktur.")
    if obj.status == "cancelled":
        raise HTTPException(400, "DITOLAK: Tidak bisa membayar faktur yang sudah dibatalkan!")
    if obj.status == "paid":
        raise HTTPException(400, "DITOLAK: Faktur ini sudah lunas sepenuhnya!")

    cash_amount = float(payment.cash_amount or 0)
    bank_amount = float(payment.bank_amount or 0)
    deposit_amount = float(payment.deposit_amount or 0)
    legacy_amount = float(payment.amount or 0)

    if cash_amount < 0 or bank_amount < 0 or deposit_amount < 0 or legacy_amount < 0:
        raise HTTPException(400, "DITOLAK: Nominal pembayaran tidak boleh negatif!")
    if cash_amount <= 0 and bank_amount <= 0 and deposit_amount <= 0 and legacy_amount > 0:
        cash_amount = legacy_amount

    total_payment = cash_amount + bank_amount + deposit_amount
    if total_payment <= 0:
        raise HTTPException(400, "DITOLAK: Isi nominal pembayaran kas, bank, deposit supplier, atau gabungannya.")
    if legacy_amount > 0 and abs(legacy_amount - total_payment) > 0.01:
        raise HTTPException(400, "DITOLAK: Total pembayaran tidak cocok dengan rincian kas/bank/deposit.")

    remaining = obj.total - obj.paid
    if total_payment > remaining:
        raise HTTPException(
            400,
            f"DITOLAK: Jumlah bayar (Rp {total_payment:,.0f}) melebihi sisa hutang (Rp {remaining:,.0f})!",
        )

    from .accounting import get_account_balance

    cash_acc = db.query(models.Account).filter(models.Account.code == "1-1100").first()
    bank_acc = db.query(models.Account).filter(models.Account.code == "1-1200").first()
    branch_id = obj.branch_id

    supplier = None
    if deposit_amount > 0:
        if not obj.supplier_id:
            raise HTTPException(400, "DITOLAK: Faktur ini tidak memiliki supplier untuk penggunaan deposit.")
        supplier = (
            db.query(models.Supplier)
            .filter(models.Supplier.id == obj.supplier_id)
            .with_for_update()
            .first()
        )
        if not supplier:
            raise HTTPException(400, "DITOLAK: Supplier faktur tidak ditemukan.")
        deposit_balance = float(supplier.deposit_balance or 0)
        if deposit_amount - deposit_balance > 0.01:
            raise HTTPException(
                400,
                f"DITOLAK: Deposit supplier tidak cukup. Tersedia Rp {deposit_balance:,.0f}, diminta Rp {deposit_amount:,.0f}.",
            )

    if cash_amount > 0:
        cash_balance = get_account_balance(db, cash_acc.id, branch_id=branch_id) if cash_acc else 0.0
        if cash_amount - cash_balance > 0.01:
            raise HTTPException(400, f"DITOLAK: Saldo kas tidak cukup. Tersedia Rp {cash_balance:,.0f}, diminta Rp {cash_amount:,.0f}.")

    if bank_amount > 0:
        bank_balance = get_account_balance(db, bank_acc.id, branch_id=branch_id) if bank_acc else 0.0
        if bank_amount - bank_balance > 0.01:
            raise HTTPException(400, f"DITOLAK: Saldo bank tidak cukup. Tersedia Rp {bank_balance:,.0f}, diminta Rp {bank_amount:,.0f}.")

    if supplier is not None:
        supplier.deposit_balance = float(supplier.deposit_balance or 0) - deposit_amount

    obj.paid += total_payment
    obj.deposit_paid = float(obj.deposit_paid or 0) + deposit_amount
    obj.status = "paid" if obj.paid >= obj.total else "partial"

    notes = payment.notes or f"Cicilan Hutang untuk {obj.number}"
    create_purchase_payment_journal(
        db,
        date_val=get_local_date(),
        number_ref=obj.number,
        description=f"PAY: {obj.number} - {notes}",
        cash_amount=cash_amount,
        bank_amount=bank_amount,
        deposit_amount=deposit_amount,
        user_id=current_user.id,
        branch_id=branch_id,
    )

    db.commit()
    return {
        "message": "Pembayaran berhasil dicatat",
        "current_paid": obj.paid,
        "remaining": obj.total - obj.paid,
        "status": obj.status,
        "amount": total_payment,
        "cash_amount": cash_amount,
        "bank_amount": bank_amount,
        "deposit_amount": deposit_amount,
        "supplier_deposit_balance": float(supplier.deposit_balance or 0) if supplier else None,
    }


# Membatalkan transaksi pembelian atau PO yang belum diproses final
@router.post("/{pid}/cancel")
def cancel_purchase(
    pid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    obj = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).with_for_update().first()
    if not obj:
        raise HTTPException(404, "Pembelian tidak ditemukan")

    from .accounting import get_books_locked_until

    locked_until = get_books_locked_until(db, obj.branch_id)
    if locked_until and obj.date and obj.date <= locked_until:
        raise HTTPException(
            400,
            f"DITOLAK: Pembelian tanggal {obj.date} berada di periode tutup buku (sampai {locked_until}). Tidak bisa dibatalkan.",
        )

    result = cancel_purchase_flow(db, purchase=obj, current_user=current_user)

    try:
        from ..auth import write_audit

        write_audit(db, current_user.id, "CANCEL", "purchases", obj.id, f"Membatalkan Pembelian/PO {obj.number} (Total: {obj.total})")
    except Exception:
        pass

    db.commit()
    return result


# Memperbarui data transaksi pembelian atau memproses fulfillment request cabang
@router.put("/{pid}")
def update_purchase(
    pid: int,
    data: schemas.PurchaseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    purchase = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).with_for_update().first()
    if not purchase:
        raise HTTPException(404, "Pembelian tidak ditemukan")

    from .accounting import assert_books_open

    tanggal = data.date if data.date else get_local_date()

    if purchase.is_branch_request:
        if current_user.active_branch_id != PUSAT_BRANCH_ID:
            if purchase.status == "shipping" and purchase.branch_id == current_user.active_branch_id:
                from ..services.purchase_flow import add_purchase_items, calculate_purchase_totals
                db.query(models.PurchaseItem).filter(models.PurchaseItem.purchase_id == purchase.id).delete()
                add_purchase_items(db, purchase, data, received=True)
                totals = calculate_purchase_totals(data, received=True)
                purchase.subtotal = totals["subtotal"]
                purchase.discount = totals["discount"]
                purchase.tax = totals["tax"]
                purchase.total = totals["total"]
                db.commit()
                db.refresh(purchase)
                return purchase
            raise HTTPException(403, "Hanya Toko Pusat yang bisa fulfill request cabang.")

        assert_books_open(db, PUSAT_BRANCH_ID, tanggal, "Fulfillment request cabang")
        number = _next_number(db, "PUR", models.Purchase, current_user)
        final_purchase = finalize_request_to_purchase(
            db,
            source_request=purchase,
            data=data,
            current_user=current_user,
            number=number,
        )
        db.commit()
        db.refresh(final_purchase)
        return final_purchase

    assert_books_open(db, purchase.branch_id, tanggal, "Pembelian")
    if purchase.document_type == "purchase" and purchase.status in ("unpaid", "partial"):
        if float(purchase.paid or 0) > 0 or purchase.status == "partial":
            raise HTTPException(400, "Pembelian yang sudah dibayar sebagian tidak dapat diedit.")
        cancel_purchase_flow(db, purchase=purchase, current_user=current_user)
        purchase.status = "draft"
        purchase.paid = 0
    updated = update_draft_purchase(db, purchase=purchase, data=data, current_user=current_user)
    db.commit()
    db.refresh(updated)

    # Trigger shipping transition if all drafts finalized
    if updated.from_po_id:
        from ..services.purchase_flow import check_and_update_source_status
        check_and_update_source_status(db, updated.from_po_id)

    return updated


# Membuat draft pembelian baru untuk item yang kurang (qty ordered > qty received) dari transaksi sebelumnya
@router.post("/{pid}/reorder-missing")
def reorder_missing_items(
    pid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    source = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).first()
    if not source:
        raise HTTPException(404, "Pembelian tidak ditemukan")
    if source.is_branch_request:
        raise HTTPException(400, "Request cabang tidak bisa dipakai untuk reorder supplier.")

    missing_items = [line for line in source.items if (line.qty_ordered or 0) > (line.qty_received or 0)]
    if not missing_items:
        raise HTTPException(400, "Tidak ada item yang kurang (Mismatch tidak ditemukan)")

    new_number = _next_number(db, "PUR", models.Purchase, current_user)
    new_purchase = models.Purchase(
        number=new_number,
        date=get_local_date(),
        branch_id=source.branch_id,
        supplier_id=source.supplier_id,
        status="draft",
        is_tax_included=source.is_tax_included,
        tax_type=source.tax_type,
        tax_percent=source.tax_percent,
        notes=f"Pesanan kekurangan dari {source.number}",
        created_by=current_user.id,
        is_branch_request=False,
        target_branch_id=source.target_branch_id,
    )
    db.add(new_purchase)
    db.flush()

    subtotal = 0.0
    for line in missing_items:
        qty_missing = (line.qty_ordered or 0) - (line.qty_received or 0)
        line_total = qty_missing * line.buy_price
        subtotal += line_total
        db.add(models.PurchaseItem(
            purchase_id=new_purchase.id,
            item_id=line.item_id,
            qty=qty_missing,
            qty_ordered=qty_missing,
            qty_received=0,
            buy_price=line.buy_price,
            total=line_total,
        ))

    new_purchase.subtotal = subtotal
    new_purchase.total = subtotal

    db.commit()
    db.refresh(new_purchase)
    return new_purchase

