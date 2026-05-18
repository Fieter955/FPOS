from datetime import date, datetime
from typing import Optional

import pytz
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from .journal_service import (
    create_branch_fulfillment_journal,
    create_branch_fulfillment_reversal_journal,
    create_purchase_journal,
    create_purchase_reversal_journal,
)
from .virtual_units import is_virtual_variant


PUSAT_BRANCH_ID = 1
WITA = pytz.timezone("Asia/Makassar")


def get_local_date() -> date:
    return datetime.now(WITA).date()


def get_local_datetime() -> datetime:
    return datetime.now(WITA)


def calculate_purchase_totals(data: schemas.PurchaseCreate, *, received: bool = False) -> dict:
    subtotal = 0.0
    for line in data.items:
        qty = line.qty_received if received and line.qty_received > 0 else line.qty
        net_price = (line.buy_price * (1 - (line.disc1 / 100))) * (1 - (line.disc2 / 100))
        subtotal += net_price * qty

    discount = subtotal * ((data.discount or 0) / 100)
    tax = (subtotal - discount) * ((data.tax or 0) / 100)
    total = subtotal - discount + tax
    return {"subtotal": subtotal, "discount": discount, "tax": tax, "total": total}


def resolve_purchase_status(data: schemas.PurchaseCreate, total: float) -> str:
    if data.status == "draft":
        return "draft"
    paid = float(data.paid or 0)
    return "paid" if paid >= total else ("partial" if paid > 0 else "unpaid")


def get_total_branch_stock(db: Session, branch_id: int, item_id: int) -> float:
    total = db.query(func.sum(models.WarehouseStock.stock)).join(models.Warehouse).filter(
        models.Warehouse.branch_id == branch_id,
        models.WarehouseStock.item_id == item_id,
    ).scalar()
    return float(total or 0.0)


def get_warehouse_stock(db: Session, warehouse_id: int, item_id: int) -> float:
    stock = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id,
    ).first()
    return float(stock.stock if stock else 0.0)


def adjust_warehouse_stock(db: Session, warehouse_id: int, item_id: int, delta: float):
    stock = db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id == warehouse_id,
        models.WarehouseStock.item_id == item_id,
    ).with_for_update().first()
    if stock:
        stock.stock += delta
        return stock

    stock = models.WarehouseStock(warehouse_id=warehouse_id, item_id=item_id, stock=delta)
    db.add(stock)
    return stock


def get_or_create_default_warehouse(db: Session, branch_id: int) -> models.Warehouse:
    warehouse = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == branch_id,
        models.Warehouse.is_active == True,
        models.Warehouse.is_default == True,
    ).first()
    if warehouse:
        return warehouse

    warehouse = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == branch_id,
        models.Warehouse.is_active == True,
    ).order_by(models.Warehouse.id).first()
    if warehouse:
        return warehouse

    branch = db.query(models.Branch).get(branch_id)
    if not branch:
        raise HTTPException(404, f"Cabang ID {branch_id} tidak ditemukan")

    warehouse = models.Warehouse(
        code=f"WH-B{branch_id}-AUTO",
        name=f"Gudang Default {branch.name}",
        branch_id=branch_id,
        is_active=True,
        is_default=True,
    )
    db.add(warehouse)
    db.flush()
    return warehouse


def validate_purchase_items(db: Session, data: schemas.PurchaseCreate):
    for line in data.items:
        item = db.query(models.Item).get(line.item_id)
        if not item:
            raise HTTPException(404, f"Barang ID {line.item_id} tidak ada")
        if is_virtual_variant(item):
            raise HTTPException(
                400,
                f"Barang multi-satuan {item.name} tidak boleh dibeli langsung. Beli barang induknya.",
            )


def add_purchase_items(db: Session, purchase: models.Purchase, data: schemas.PurchaseCreate,
                       *, received: bool):
    for line in data.items:
        qty = line.qty_received if received and line.qty_received > 0 else line.qty
        net_price = (line.buy_price * (1 - (line.disc1 / 100))) * (1 - (line.disc2 / 100))
        db.add(models.PurchaseItem(
            purchase_id=purchase.id,
            item_id=line.item_id,
            qty=qty,
            qty_ordered=line.qty_ordered or line.qty,
            qty_received=(line.qty_received if received else 0) or (qty if received else 0),
            buy_price=line.buy_price,
            disc1=line.disc1,
            disc2=line.disc2,
            discount=line.buy_price - net_price,
            total=net_price * qty,
        ))


def update_supplier_item_master(db: Session, data: schemas.PurchaseCreate):
    for line in data.items:
        item = db.query(models.Item).with_for_update().get(line.item_id)
        spec = db.query(models.ItemSupplier).filter(
            models.ItemSupplier.item_id == item.id,
            models.ItemSupplier.supplier_id == data.supplier_id,
        ).first()
        if spec:
            spec.buy_price = line.buy_price
        else:
            db.add(models.ItemSupplier(
                item_id=item.id,
                supplier_id=data.supplier_id,
                buy_price=line.buy_price,
                barcode=item.barcode,
            ))

        item.buy_price = line.buy_price
        if line.sell_price and line.sell_price > 0:
            item.sell_price = line.sell_price
        if line.profit_margin and line.profit_margin > 0:
            item.profit_margin = line.profit_margin


def receive_branch_stock(db: Session, *, purchase: models.Purchase,
                         target_branch_id: int, local_datetime: datetime, local_date: date):
    warehouse = get_or_create_default_warehouse(db, target_branch_id)
    is_pusat_stock = target_branch_id == PUSAT_BRANCH_ID

    for purchase_item in purchase.items:
        item = db.query(models.Item).with_for_update().get(purchase_item.item_id)
        qty_before = get_total_branch_stock(db, target_branch_id, item.id)

        if is_pusat_stock:
            item.stock += purchase_item.qty

        adjust_warehouse_stock(db, warehouse.id, item.id, purchase_item.qty)
        db.add(models.StockMovement(
            date=local_date,
            created_at=local_datetime,
            item_id=item.id,
            branch_id=target_branch_id,
            type="in",
            qty=purchase_item.qty,
            qty_before=qty_before,
            qty_after=qty_before + purchase_item.qty,
            reference=purchase.number,
            notes=(
                "Penerimaan stok cabang dari fulfillment pusat"
                if purchase.branch_id == PUSAT_BRANCH_ID and target_branch_id != PUSAT_BRANCH_ID
                else "Pembelian dari Supplier"
            ),
        ))


def reverse_received_stock(db: Session, *, purchase: models.Purchase,
                           stock_branch_id: int, local_date: date,
                           local_datetime: datetime):
    warehouse = get_or_create_default_warehouse(db, stock_branch_id)
    is_pusat_stock = stock_branch_id == PUSAT_BRANCH_ID

    for purchase_item in purchase.items:
        item = db.query(models.Item).with_for_update().get(purchase_item.item_id)
        warehouse_stock = get_warehouse_stock(db, warehouse.id, item.id)
        if warehouse_stock < purchase_item.qty:
            raise HTTPException(
                400,
                f"Gagal dibatalkan! Stok {item.name} di gudang tujuan tidak cukup (Sisa: {warehouse_stock}).",
            )

        qty_before = get_total_branch_stock(db, stock_branch_id, item.id)
        adjust_warehouse_stock(db, warehouse.id, item.id, -purchase_item.qty)
        if is_pusat_stock:
            if item.stock < purchase_item.qty:
                raise HTTPException(400, f"Gagal dibatalkan! Stok pusat {item.name} tidak cukup.")
            item.stock -= purchase_item.qty

        db.add(models.StockMovement(
            date=local_date,
            created_at=local_datetime,
            item_id=item.id,
            branch_id=stock_branch_id,
            type="out",
            qty=purchase_item.qty,
            qty_before=qty_before,
            qty_after=qty_before - purchase_item.qty,
            reference=purchase.number,
            notes=f"Pembatalan Pembelian {purchase.number}",
        ))


def create_branch_request(db: Session, *, data: schemas.PurchaseCreate,
                          current_user: models.User, number: str) -> models.Purchase:
    if current_user.active_branch_id == PUSAT_BRANCH_ID:
        raise HTTPException(400, "Request cabang hanya boleh dibuat dari cabang, bukan Toko Pusat.")

    validate_purchase_items(db, data)
    tanggal = data.date if data.date else get_local_date()
    totals = calculate_purchase_totals(data, received=False)
    request = models.Purchase(
        number=number,
        date=tanggal,
        branch_id=current_user.active_branch_id,
        created_at=get_local_datetime(),
        supplier_id=data.supplier_id,
        subtotal=totals["subtotal"],
        discount=totals["discount"],
        tax=totals["tax"],
        total=totals["total"],
        paid=0,
        status="pending",
        notes=data.notes,
        created_by=current_user.id,
        is_branch_request=True,
        target_branch_id=data.target_branch_id or PUSAT_BRANCH_ID,
    )
    db.add(request)
    db.flush()
    add_purchase_items(db, request, data, received=False)
    return request


def create_supplier_purchase(db: Session, *, data: schemas.PurchaseCreate,
                             current_user: models.User, number: str,
                             branch_id: int, target_branch_id: Optional[int] = None,
                             source_request: Optional[models.Purchase] = None) -> models.Purchase:
    validate_purchase_items(db, data)
    tanggal = data.date if data.date else get_local_date()
    local_datetime = get_local_datetime()
    totals = calculate_purchase_totals(data, received=True)
    status = resolve_purchase_status(data, totals["total"])

    purchase = models.Purchase(
        number=number,
        date=tanggal,
        branch_id=branch_id,
        created_at=local_datetime,
        supplier_id=data.supplier_id,
        subtotal=totals["subtotal"],
        discount=totals["discount"],
        tax=totals["tax"],
        total=totals["total"],
        paid=data.paid or 0,
        status=status,
        notes=data.notes,
        created_by=current_user.id,
        is_branch_request=False,
        target_branch_id=target_branch_id,
        from_po_id=source_request.id if source_request else data.from_po_id,
    )
    db.add(purchase)
    db.flush()
    add_purchase_items(db, purchase, data, received=True)
    db.flush()

    if status != "draft":
        from .journal_service import create_pusat_fulfillment_journal

        # 🛡️ NEW LOGIC: If Pusat fulfills for another branch, DEFER stock and branch journal
        is_fulfillment = (branch_id == PUSAT_BRANCH_ID and 
                          target_branch_id and 
                          target_branch_id != PUSAT_BRANCH_ID)

        if not is_fulfillment:
            # Normal Flow: Stock increases immediately
            stock_branch_id = target_branch_id or branch_id
            receive_branch_stock(db, purchase=purchase, target_branch_id=stock_branch_id,
                                 local_datetime=local_datetime)
        
        update_supplier_item_master(db, data)

        supplier = db.query(models.Supplier).get(data.supplier_id)
        supplier_name = supplier.name if supplier else "Supplier"

        if is_fulfillment:
            create_pusat_fulfillment_journal(
                db,
                date_val=tanggal,
                number_ref=number,
                supplier_name=supplier_name,
                target_branch_id=target_branch_id,
                total=totals["total"],
                paid=data.paid or 0,
                user_id=current_user.id,
                pusat_branch_id=branch_id,
            )
        else:
            create_purchase_journal(
                db,
                date_val=tanggal,
                number_ref=number,
                supplier_name=supplier_name,
                total=totals["total"],
                paid=data.paid or 0,
                user_id=current_user.id,
                branch_id=branch_id,
            )

    if source_request is not None:
        source_request.status = "completed"
        db.add(source_request)

    return purchase


def finalize_request_to_purchase(db: Session, *, source_request: models.Purchase,
                                 data: schemas.PurchaseCreate,
                                 current_user: models.User, number: str) -> models.Purchase:
    if current_user.active_branch_id != PUSAT_BRANCH_ID:
        raise HTTPException(403, "Hanya Toko Pusat yang bisa fulfill request cabang.")
    if not source_request.is_branch_request:
        raise HTTPException(400, "Dokumen sumber bukan request cabang.")
    if source_request.status != "pending":
        raise HTTPException(400, f"Request {source_request.number} sudah berstatus {source_request.status}.")

    return create_supplier_purchase(
        db,
        data=data,
        current_user=current_user,
        number=number,
        branch_id=PUSAT_BRANCH_ID,
        target_branch_id=source_request.branch_id,
        source_request=source_request,
    )


def update_draft_purchase(db: Session, *, purchase: models.Purchase,
                          data: schemas.PurchaseCreate,
                          current_user: models.User) -> models.Purchase:
    if purchase.is_branch_request:
        raise HTTPException(400, "Request cabang tidak boleh diubah menjadi purchase final.")
    if purchase.status != "draft":
        raise HTTPException(400, f"Faktur dengan status '{purchase.status}' tidak dapat diubah.")

    validate_purchase_items(db, data)
    tanggal = data.date if data.date else get_local_date()
    local_datetime = get_local_datetime()
    totals = calculate_purchase_totals(data, received=True)
    status = resolve_purchase_status(data, totals["total"])

    purchase.date = tanggal
    purchase.supplier_id = data.supplier_id
    purchase.subtotal = totals["subtotal"]
    purchase.discount = totals["discount"]
    purchase.tax = totals["tax"]
    purchase.total = totals["total"]
    purchase.paid = data.paid or 0
    purchase.status = status
    purchase.notes = data.notes
    purchase.is_branch_request = False
    
    # 🛡️ FIX: Preserve target_branch_id if not explicitly provided (prevents losing branch link when finalizing drafts)
    final_target_branch_id = data.target_branch_id or purchase.target_branch_id
    purchase.target_branch_id = final_target_branch_id

    db.query(models.PurchaseItem).filter(models.PurchaseItem.purchase_id == purchase.id).delete()
    add_purchase_items(db, purchase, data, received=True)
    db.flush()

    if status != "draft":
        from .journal_service import create_pusat_fulfillment_journal

        # 🛡️ NEW LOGIC: If Pusat fulfills for another branch, DEFER stock and branch journal
        is_fulfillment = (purchase.branch_id == PUSAT_BRANCH_ID and 
                          final_target_branch_id and 
                          final_target_branch_id != PUSAT_BRANCH_ID)

        if not is_fulfillment:
            # Normal Flow: Stock increases immediately
            stock_branch_id = final_target_branch_id or purchase.branch_id
            receive_branch_stock(db, purchase=purchase, target_branch_id=stock_branch_id,
                                 local_datetime=local_datetime)
        
        update_supplier_item_master(db, data)
        
        supplier = db.query(models.Supplier).get(data.supplier_id)
        supplier_name = supplier.name if supplier else "Supplier"

        if is_fulfillment:
            # Only record the outgoing journal for Pusat. 
            # Branch stock and journal will be handled in 'receive-branch' route.
            create_pusat_fulfillment_journal(
                db,
                date_val=tanggal,
                number_ref=purchase.number,
                supplier_name=supplier_name,
                target_branch_id=final_target_branch_id,
                total=totals["total"],
                paid=data.paid or 0,
                user_id=current_user.id,
                pusat_branch_id=purchase.branch_id,
            )
        else:
            create_purchase_journal(
                db,
                date_val=tanggal,
                number_ref=purchase.number,
                supplier_name=supplier_name,
                total=totals["total"],
                paid=data.paid or 0,
                user_id=current_user.id,
                branch_id=purchase.branch_id,
            )


    return purchase


def check_and_update_source_status(db: Session, from_po_id: int):
    """
    Check if all drafts linked to a source PO are finalized.
    If yes, move source PO status to 'shipping'.
    """
    source = db.query(models.Purchase).get(from_po_id)
    if not source or not source.is_branch_request:
        return

    # Count drafts that are still 'draft'
    drafts_count = db.query(models.Purchase).filter(
        models.Purchase.from_po_id == from_po_id,
        models.Purchase.status == 'draft'
    ).count()

    if drafts_count == 0:
        source.status = 'shipping'
        db.commit()


def cancel_purchase_flow(db: Session, *, purchase: models.Purchase,
                         current_user: models.User) -> dict:
    if purchase.status == "cancelled":
        raise HTTPException(400, "Faktur pembelian ini sudah dibatalkan sebelumnya")

    local_date = get_local_date()
    local_datetime = get_local_datetime()

    if purchase.is_branch_request:
        purchase.status = "cancelled"
        return {"message": "Request PO dibatalkan tanpa mutasi stok/jurnal.", "number": purchase.number, "status": purchase.status}

    if current_user.active_branch_id != purchase.branch_id:
        raise HTTPException(403, "Pembayaran/pembatalan supplier hanya boleh dilakukan oleh cabang pemilik faktur.")

    stock_branch_id = purchase.target_branch_id or purchase.branch_id
    reverse_received_stock(
        db,
        purchase=purchase,
        stock_branch_id=stock_branch_id,
        local_date=local_date,
        local_datetime=local_datetime,
    )

    if purchase.branch_id == PUSAT_BRANCH_ID and stock_branch_id != PUSAT_BRANCH_ID:
        create_branch_fulfillment_reversal_journal(
            db,
            date_val=local_date,
            number_ref=purchase.number,
            target_branch_id=stock_branch_id,
            total=purchase.total,
            paid=purchase.paid,
            user_id=current_user.id,
            pusat_branch_id=purchase.branch_id,
        )
    else:
        create_purchase_reversal_journal(
            db,
            date_val=local_date,
            number_ref=purchase.number,
            total=purchase.total,
            paid=purchase.paid,
            user_id=current_user.id,
            branch_id=purchase.branch_id,
        )

    purchase.status = "cancelled"
    return {
        "message": "Pembelian dibatalkan. Stok dikurangi dan Jurnal Akuntansi telah dibalik.",
        "number": purchase.number,
        "status": purchase.status,
    }
