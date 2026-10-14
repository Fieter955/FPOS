import math
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import pytz
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import date, datetime
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, write_audit, require_admin
from ..permissions import has_role
from ..services.virtual_units import get_required_stock_qty, get_stock_source_item, is_virtual_variant
from ..services.tax_context import purchase_tax_type
from ..services.serials import normalize_serial, reverse_reference_serials, transition_serials
from ..services.receipt_renderer import (
    build_purchase_return_receipt,
    build_sale_return_receipt,
    settings_from_branch,
)
from .accounting import resolve_active_branch_id
from .print_queue import enqueue_print_job

router = APIRouter()
WITA = pytz.timezone("Asia/Makassar")
PUSAT_BRANCH_ID = 1


def get_local_date() -> date:
    return datetime.now(WITA).date()


def _positive_qty(payload: dict) -> float:
    try:
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Qty retur harus berupa angka")
    if not math.isfinite(qty) or qty <= 0:
        raise HTTPException(400, "Qty retur harus lebih dari 0")
    return qty


def _serial_keys(values) -> set[str]:
    return {normalize_serial(value) for value in (values or []) if str(value).strip()}


class ReturnCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _default_branch_warehouse(db: Session, branch_id: int):
    return db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == branch_id,
        models.Warehouse.is_active == True,
    ).order_by(models.Warehouse.is_default.desc(), models.Warehouse.id.asc()).first()


def _reverse_return_journal(db: Session, *, reference: str, branch_id: int, user_id: int):
    from .accounting import next_journal_number

    original = db.query(models.Journal).filter(
        models.Journal.reference == reference,
        models.Journal.branch_id == branch_id,
    ).order_by(models.Journal.id.desc()).first()
    if not original:
        raise HTTPException(409, "Jurnal retur tidak ditemukan; reversal otomatis diblokir")
    reversal = models.Journal(
        number=next_journal_number(db, branch_id), date=get_local_date(), branch_id=branch_id,
        reference=f"REV-{reference}", source=original.source,
        description=f"REVERSAL {reference}", created_by=user_id,
    )
    db.add(reversal)
    db.flush()
    for line in original.lines:
        db.add(models.JournalEntryLine(
            journal_id=reversal.id,
            debit_account_id=line.credit_account_id,
            credit_account_id=line.debit_account_id,
            amount=line.amount,
            description=reversal.description,
        ))
    return reversal


def _validate_return_serial_origin(
    db: Session,
    *,
    entered_item: models.Item,
    source_line,
    serial_values,
    source_type: str,
) -> None:
    """Pastikan serial retur benar-benar berasal dari baris faktur terkait.

    Data baru memakai snapshot ``serials_json``. Untuk penjualan legacy, ledger
    serial dipakai hanya bila referensi transaksinya tunggal dan dapat dibuktikan.
    """
    stock_item = get_stock_source_item(db, entered_item)
    if not stock_item.tracks_serial:
        if serial_values:
            raise HTTPException(400, f"Barang {entered_item.name} tidak memakai serial")
        return

    supplied = _serial_keys(serial_values)
    expected_count = int(round(get_required_stock_qty(entered_item, float(source_line.qty or 0))))
    raw = getattr(source_line, "serials_json", None)
    allowed = _serial_keys(json.loads(raw or "[]"))

    if not allowed and source_type == "sale":
        rows = (
            db.query(models.ItemSerial.normalized_serial)
            .join(models.ItemSerialEvent, models.ItemSerialEvent.serial_id == models.ItemSerial.id)
            .filter(
                models.ItemSerial.item_id == stock_item.id,
                models.ItemSerialEvent.reference_type == "sale",
                models.ItemSerialEvent.reference_id == source_line.sale_id,
                models.ItemSerialEvent.to_status == "sold",
            )
            .all()
        )
        # Ledger lama mereferensikan header transaksi. Jika item yang sama ada
        # pada beberapa baris, relasi tidak cukup pasti dan tidak boleh ditebak.
        same_item_lines = db.query(models.SaleItem.id).filter(
            models.SaleItem.sale_id == source_line.sale_id,
            models.SaleItem.item_id == source_line.item_id,
        ).count()
        if same_item_lines == 1:
            allowed = {row.normalized_serial for row in rows}

    if not allowed and expected_count:
        raise HTTPException(409, f"Asal serial {entered_item.name} pada faktur lama tidak dapat dipastikan")
    unknown = supplied - allowed
    if unknown:
        raise HTTPException(400, f"Serial {sorted(unknown)[0]} tidak berasal dari faktur yang dipilih")


def _header_discount_ratio(document, line_gross_total: float) -> float:
    """Rasio diskon header yang sudah tersimpan pada faktur asal."""
    gross_snapshot = getattr(document, "invoice_discount_gross", None)
    if gross_snapshot is not None and line_gross_total > 0:
        ratio = float(gross_snapshot or 0) / line_gross_total
    else:
        subtotal = float(getattr(document, "subtotal", 0) or 0)
        ratio = float(getattr(document, "discount", 0) or 0) / subtotal if subtotal > 0 else 0
    return min(max(ratio, 0.0), 1.0)


def _sale_return_unit_price(sale, sale_item) -> float:
    line_gross_total = sum(float(line.total or 0) for line in sale.items)
    ratio = _header_discount_ratio(sale, line_gross_total)
    line_price_after_discount = float(sale_item.sell_price or 0) * (
        1 - float(sale_item.discount or 0) / 100
    )
    return line_price_after_discount * (1 - ratio)


def _purchase_return_unit_price(purchase, purchase_item) -> float:
    qty = float(purchase_item.qty or 0)
    line_net_unit = (
        float(purchase_item.total or 0) / qty
        if qty > 0
        else float(purchase_item.buy_price or 0)
    )
    ratio = _header_discount_ratio(purchase, float(purchase.subtotal or 0))
    return line_net_unit * (1 - ratio)


@router.get("/branch-shipments")
def get_returnable_branch_shipments(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Barang kiriman pusat yang masih dapat dikembalikan oleh cabang."""
    branch_id = resolve_active_branch_id(db, current_user)
    purchases = db.query(models.Purchase).filter(
        models.Purchase.target_branch_id == branch_id,
        models.Purchase.from_po_id.isnot(None),
        models.Purchase.fulfillment_status == "received",
        models.Purchase.status != "cancelled",
    ).order_by(models.Purchase.id.desc()).all()
    result = []
    for purchase in purchases:
        for line in purchase.items:
            returned = db.query(func.sum(models.WarehouseTransferItem.qty)).join(
                models.WarehouseTransfer
            ).filter(
                models.WarehouseTransfer.transfer_kind == "branch_return",
                models.WarehouseTransfer.status != "cancelled",
                models.WarehouseTransferItem.purchase_item_id == line.id,
            ).scalar() or 0
            available = float(line.qty_received or line.qty or 0) - float(returned)
            if available <= 1e-9:
                continue
            result.append({
                "purchase_id": purchase.id,
                "purchase_item_id": line.id,
                "purchase_number": purchase.number,
                "supplier_name": purchase.supplier.name if purchase.supplier else "-",
                "item_id": line.item_id,
                "item_name": line.item.name if line.item else "-",
                "item_code": line.item.code if line.item else "-",
                "qty_received": line.qty_received or line.qty,
                "qty_available": available,
                "unit_cost": (float(line.total or 0) / float(line.qty or 1)),
            })
    return result


@router.post("/branch-to-center")
def create_branch_return_to_center(
    data: schemas.BranchReturnCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Keluarkan stok cabang ke transit retur; stok pusat belum bertambah sampai ACC."""
    from ..services.purchase_flow import (
        adjust_warehouse_stock, get_or_create_default_warehouse,
        get_total_branch_stock, get_warehouse_stock,
    )
    from ..services.inventory_fifo import reduce_batches_for_reversal
    from ..services.journal_service import create_branch_return_dispatch_journal
    from .accounting import assert_books_open

    branch_id = resolve_active_branch_id(db, current_user)
    if branch_id == PUSAT_BRANCH_ID:
        raise HTTPException(400, "Retur kiriman ini hanya dibuat oleh cabang")
    assert_books_open(db, branch_id, data.date, "Retur kiriman ke pusat")
    from_w = get_or_create_default_warehouse(db, branch_id)
    to_w = get_or_create_default_warehouse(db, PUSAT_BRANCH_ID)
    number = _next_number(db, f"RC{branch_id}", models.WarehouseTransfer)
    transfer = models.WarehouseTransfer(
        number=number, date=data.date, from_warehouse_id=from_w.id,
        to_warehouse_id=to_w.id, status="shipping", transfer_kind="branch_return",
        notes=data.notes, created_by=current_user.id,
    )
    db.add(transfer)
    db.flush()
    total_cost = 0.0
    local_datetime = datetime.now(WITA)

    for requested in data.items:
        if requested.qty <= 0:
            raise HTTPException(400, "Qty retur harus lebih dari 0")
        line = db.query(models.PurchaseItem).join(models.Purchase).filter(
            models.PurchaseItem.id == requested.purchase_item_id,
            models.Purchase.target_branch_id == branch_id,
            models.Purchase.fulfillment_status == "received",
        ).with_for_update().first()
        if not line:
            raise HTTPException(404, "Baris kiriman pusat tidak ditemukan")
        already = db.query(func.sum(models.WarehouseTransferItem.qty)).join(
            models.WarehouseTransfer
        ).filter(
            models.WarehouseTransfer.transfer_kind == "branch_return",
            models.WarehouseTransfer.status != "cancelled",
            models.WarehouseTransferItem.purchase_item_id == line.id,
        ).scalar() or 0
        available = float(line.qty_received or line.qty or 0) - float(already)
        if requested.qty > available + 1e-9:
            raise HTTPException(400, f"Qty retur melebihi sisa kiriman ({available})")
        if get_warehouse_stock(db, from_w.id, line.item_id) + 1e-9 < requested.qty:
            raise HTTPException(400, f"Stok {line.item.name} di cabang tidak cukup")

        before = get_total_branch_stock(db, branch_id, line.item_id)
        adjust_warehouse_stock(db, from_w.id, line.item_id, -requested.qty)
        consumed, leftover = reduce_batches_for_reversal(
            db, item_id=line.item_id, warehouse_id=from_w.id, qty=requested.qty,
            prefer_purchase_item_id=line.id,
        )
        fallback = float(line.buy_price or 0)
        line_cost = consumed + leftover * fallback
        unit_cost = line_cost / requested.qty
        total_cost += line_cost
        db.add(models.WarehouseTransferItem(
            transfer_id=transfer.id, item_id=line.item_id, qty=requested.qty,
            qty_received=0, purchase_item_id=line.id,
            source_request_item_id=line.source_request_item_id,
            unit_cost=unit_cost, total_cost=line_cost,
            serials_json=json.dumps(requested.serials, ensure_ascii=False) if requested.serials else None,
        ))
        transition_serials(
            db, item=line.item, qty=requested.qty, serial_values=requested.serials,
            from_statuses={"in_stock", "quarantine"}, to_status="in_transit",
            from_warehouse_id=from_w.id, to_warehouse=None,
            reference_type="branch_return_dispatch", reference_id=transfer.id,
            reference_number=number, user_id=current_user.id, event_type="branch_return_dispatch",
        )
        db.add(models.StockMovement(
            date=data.date, created_at=local_datetime, item_id=line.item_id,
            branch_id=branch_id, type="transfer_out", qty=requested.qty,
            warehouse_id=from_w.id, counterparty_warehouse_id=to_w.id,
            qty_before=before, qty_after=before-requested.qty,
            reference=number, notes="Retur kiriman ke Toko Utama",
        ))

    create_branch_return_dispatch_journal(
        db, date_val=data.date, number_ref=number, total=total_cost,
        user_id=current_user.id, branch_id=branch_id,
    )
    db.commit()
    return {"id": transfer.id, "number": number, "status": transfer.status,
            "message": "Retur dikirim ke Toko Utama dan menunggu ACC pusat"}


@router.get("/branch-to-center")
def list_branch_returns_to_center(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    query = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.transfer_kind == "branch_return"
    )
    if branch_id != PUSAT_BRANCH_ID:
        query = query.filter(models.WarehouseTransfer.from_warehouse.has(branch_id=branch_id))
    if status:
        query = query.filter(models.WarehouseTransfer.status == status)
    transfers = query.order_by(models.WarehouseTransfer.id.desc()).all()
    return [{
        "id": transfer.id,
        "number": transfer.number,
        "date": str(transfer.date),
        "status": transfer.status,
        "from_branch": transfer.from_warehouse.branch.name if transfer.from_warehouse and transfer.from_warehouse.branch else "-",
        "notes": transfer.notes,
        "items": [{
            "item_name": line.item.name if line.item else "-",
            "qty": line.qty,
            "unit_cost": line.unit_cost,
        } for line in transfer.items],
    } for transfer in transfers]


@router.post("/branch-to-center/{transfer_id}/receive")
def receive_branch_return_at_center(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.purchase_flow import (
        adjust_warehouse_stock, get_total_branch_stock,
    )
    from ..services.inventory_fifo import add_batch
    from ..services.journal_service import create_pusat_return_receiving_journal
    from .accounting import assert_books_open

    if resolve_active_branch_id(db, current_user) != PUSAT_BRANCH_ID:
        raise HTTPException(403, "Hanya Toko Utama yang dapat menerima retur cabang")
    transfer = db.query(models.WarehouseTransfer).filter(
        models.WarehouseTransfer.id == transfer_id,
        models.WarehouseTransfer.transfer_kind == "branch_return",
    ).with_for_update().first()
    if not transfer:
        raise HTTPException(404, "Kiriman retur tidak ditemukan")
    if transfer.status == "confirmed":
        raise HTTPException(409, "Kiriman retur sudah pernah di-ACC")
    if transfer.status != "shipping":
        raise HTTPException(400, f"Status retur saat ini {transfer.status}")
    assert_books_open(db, PUSAT_BRANCH_ID, get_local_date(), "Terima retur cabang")
    local_datetime = datetime.now(WITA)
    total_cost = 0.0
    for line in transfer.items:
        item = db.query(models.Item).with_for_update().get(line.item_id)
        before = get_total_branch_stock(db, PUSAT_BRANCH_ID, line.item_id)
        adjust_warehouse_stock(db, transfer.to_warehouse_id, line.item_id, line.qty)
        purchase_item = db.query(models.PurchaseItem).get(line.purchase_item_id)
        purchase = purchase_item.purchase if purchase_item else None
        add_batch(
            db, item_id=line.item_id, warehouse_id=transfer.to_warehouse_id,
            qty=line.qty, unit_cost=line.unit_cost, received_date=get_local_date(),
            supplier_id=purchase.supplier_id if purchase else None,
            purchase_item_id=line.purchase_item_id,
        )
        transition_serials(
            db, item=item, qty=line.qty, serial_values=json.loads(line.serials_json or "[]"),
            from_statuses={"in_transit"}, to_status="in_stock", from_warehouse_id=None,
            to_warehouse=transfer.to_warehouse, reference_type="branch_return_receive",
            reference_id=transfer.id, reference_number=transfer.number,
            user_id=current_user.id, event_type="branch_return_receive",
        )
        line.qty_received = line.qty
        total_cost += float(line.total_cost or 0)
        db.add(models.StockMovement(
            date=get_local_date(), created_at=local_datetime, item_id=line.item_id,
            branch_id=PUSAT_BRANCH_ID, type="transfer_in", qty=line.qty,
            warehouse_id=transfer.to_warehouse_id,
            counterparty_warehouse_id=transfer.from_warehouse_id,
            qty_before=before, qty_after=before+line.qty,
            reference=transfer.number, notes="Terima retur kiriman dari cabang",
        ))
    create_pusat_return_receiving_journal(
        db, date_val=get_local_date(), number_ref=transfer.number,
        total=total_cost, user_id=current_user.id,
    )
    transfer.status = "confirmed"
    transfer.received_at = local_datetime
    db.commit()
    return {"id": transfer.id, "number": transfer.number, "status": transfer.status,
            "message": "Retur diterima di pusat; siap diperiksa atau diretur ke supplier"}


@router.get("/history/purchases")
def get_purchase_history_items(
    supplier_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None, # 'returned', 'not_returned', 'partial'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    query = db.query(models.PurchaseItem).join(models.Purchase).filter(
        models.Purchase.status.notin_(['cancelled', 'draft', 'pending'])
    )
    
    branch_id = resolve_active_branch_id(db, current_user)
    query = query.filter(models.Purchase.branch_id == branch_id)
    
    if supplier_id:
        query = query.filter(models.Purchase.supplier_id == supplier_id)
    if start_date:
        query = query.filter(models.Purchase.date >= start_date)
    if end_date:
        query = query.filter(models.Purchase.date <= end_date)
        
    items = query.order_by(models.Purchase.date.desc()).all()
    
    result = []
    for it in items:
        # Hitung berapa yang sudah diretur untuk baris ini
        # Karena model kita PurchaseReturnItem merujuk ke purchase_id + item_id
        # Kita aggregasi retur yang merujuk ke purchase yang sama dan item yang sama
        returned_qty = db.query(func.sum(models.PurchaseReturnItem.qty)).join(
            models.PurchaseReturn
        ).filter(
            models.PurchaseReturnItem.purchase_item_id == it.id,
            models.PurchaseReturn.status != "cancelled",
        ).scalar() or 0.0
        legacy_returned = db.query(func.sum(models.PurchaseReturnItem.qty)).join(models.PurchaseReturn).filter(
            models.PurchaseReturn.purchase_id == it.purchase_id,
            models.PurchaseReturnItem.item_id == it.item_id,
            models.PurchaseReturnItem.purchase_item_id.is_(None),
            models.PurchaseReturn.status != "cancelled",
        ).scalar() or 0.0
        returned_qty += legacy_returned
        
        available_qty = it.qty - returned_qty
        
        # Filter berdasarkan status retur jika diminta
        item_status = "not_returned"
        if returned_qty >= it.qty: item_status = "returned"
        elif returned_qty > 0: item_status = "partial"
        
        if status and status != item_status:
            continue

        result.append({
            "purchase_item_id": it.id,
            "item_id": it.item_id,
            "item": {
                "id": it.item_id,
                "name": it.item.name,
                "code": it.item.code,
                "barcode": it.item.barcode,
                "tracks_serial": bool(get_stock_source_item(db, it.item).tracks_serial),
            },
            "buy_price": it.buy_price,
            "return_price": _purchase_return_unit_price(it.purchase, it),
            "qty_bought": it.qty,
            "qty_returned": returned_qty,
            "qty_available": available_qty,
            "status": item_status,
            "purchase_date": str(it.purchase.date),
            "purchase_id": it.purchase_id,
            "purchase_number": it.purchase.number,
            "supplier_name": it.purchase.supplier.name if it.purchase.supplier else "-",
            "is_tax_included": it.purchase.is_tax_included,
            "tax_type": purchase_tax_type(it.purchase),
            "tax_percent": (
                it.ppn_percent
                if it.ppn_percent is not None
                else it.purchase.tax_percent
            )
        })
    return result


@router.get("/history/sales")
def get_sale_history_items(
    customer_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    query = db.query(models.SaleItem).join(models.Sale).filter(
        models.Sale.status != 'cancelled'
    )
    
    branch_id = resolve_active_branch_id(db, current_user)
    query = query.filter(models.Sale.branch_id == branch_id)
    
    if customer_id:
        query = query.filter(models.Sale.customer_id == customer_id)
    if start_date:
        query = query.filter(models.Sale.date >= start_date)
    if end_date:
        query = query.filter(models.Sale.date <= end_date)

    items = query.order_by(models.Sale.date.desc()).all()
    
    result = []
    for it in items:
        returned_qty = db.query(func.sum(models.SaleReturnItem.qty)).join(
            models.SaleReturn
        ).filter(
            models.SaleReturnItem.sale_item_id == it.id,
            models.SaleReturn.status != "cancelled",
        ).scalar() or 0.0
        legacy_returned = db.query(func.sum(models.SaleReturnItem.qty)).join(models.SaleReturn).filter(
            models.SaleReturn.sale_id == it.sale_id,
            models.SaleReturnItem.item_id == it.item_id,
            models.SaleReturnItem.sale_item_id.is_(None),
            models.SaleReturn.status != "cancelled",
        ).scalar() or 0.0
        returned_qty += legacy_returned
        
        available_qty = it.qty - returned_qty
        
        item_status = "not_returned"
        if returned_qty >= it.qty: item_status = "returned"
        elif returned_qty > 0: item_status = "partial"
        
        if status and status != item_status:
            continue
            
        result.append({
            "sale_item_id": it.id,
            "item_id": it.item_id,
            "item": {
                "id": it.item_id,
                "name": it.item.name,
                "code": it.item.code,
                "barcode": it.item.barcode,
                "tracks_serial": bool(get_stock_source_item(db, it.item).tracks_serial),
            },
            "sell_price": it.sell_price,
            "return_price": _sale_return_unit_price(it.sale, it),
            "qty_sold": it.qty,
            "qty_returned": returned_qty,
            "qty_available": available_qty,
            "status": item_status,
            "sale_date": str(it.sale.date),
            "sale_id": it.sale_id,
            "sale_number": it.sale.number,
            "customer_name": it.sale.customer.name if it.sale.customer else "Umum",
            "is_tax_included": it.sale.is_tax_included,
            "tax_percent": (
                it.sale.tax_percent
                if bool(it.sale.tax) and not any(float(line.ppn_percent or 0) > 0 for line in it.sale.items)
                else float(it.ppn_percent or 0)
            )
        })
    return result


@router.get("/history/broken")
def get_broken_history_items(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None, # 'returned', 'not_returned', 'partial'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    branch_id = resolve_active_branch_id(db, current_user)
    result = []

    trade_query = db.query(models.TradeInReturnItem).join(models.TradeIn).filter(
        models.TradeInReturnItem.condition == "damaged",
        models.TradeIn.branch_id == branch_id,
        models.TradeIn.status != "cancelled",
    )
    sale_query = db.query(models.SaleReturnItem).join(models.SaleReturn).filter(
        models.SaleReturnItem.condition == "damaged",
        models.SaleReturn.branch_id == branch_id,
        models.SaleReturn.status != "cancelled",
    )
    quarantine_query = db.query(models.InventoryQuarantine).filter(
        models.InventoryQuarantine.branch_id == branch_id,
    )
    if start_date:
        trade_query = trade_query.filter(models.TradeIn.date >= start_date)
        sale_query = sale_query.filter(models.SaleReturn.date >= start_date)
        quarantine_query = quarantine_query.filter(models.InventoryQuarantine.date >= start_date)
    if end_date:
        trade_query = trade_query.filter(models.TradeIn.date <= end_date)
        sale_query = sale_query.filter(models.SaleReturn.date <= end_date)
        quarantine_query = quarantine_query.filter(models.InventoryQuarantine.date <= end_date)

    def append_item(it, source_type, source_date, source_number, customer_name, unit_cost):
        returned_qty = it.returned_qty or 0.0
        available_qty = it.qty - returned_qty
        item_status = "not_returned"
        if returned_qty >= it.qty: item_status = "returned"
        elif returned_qty > 0: item_status = "partial"
        if status and status != item_status:
            return
        result.append({
            "source_type": source_type,
            "source_item_id": it.id,
            "trade_in_return_item_id": it.id if source_type == "trade_in" else None,
            "sale_return_item_id": it.id if source_type == "sale_return" else None,
            "item_id": it.item_id,
            "item": {
                "id": it.item_id,
                "name": it.item.name,
                "code": it.item.code,
                "barcode": it.item.barcode,
                "tracks_serial": bool(get_stock_source_item(db, it.item).tracks_serial),
            },
            "return_price": unit_cost,
            "quarantine_unit_cost": unit_cost,
            "qty": it.qty,
            "qty_returned": returned_qty,
            "qty_available": available_qty,
            "status": item_status,
            "date": str(source_date),
            "trade_in_id": it.trade_in_id if source_type == "trade_in" else None,
            "trade_in_number": source_number,
            "source_number": source_number,
            "customer_name": customer_name,
            "condition": "damaged",
        })

    for it in trade_query.all():
        append_item(
            it, "trade_in", it.trade_in.date, it.trade_in.number,
            it.trade_in.customer.name if it.trade_in.customer else "Umum",
            float(it.return_price or 0),
        )
    for it in sale_query.all():
        sale = it.return_.sale
        append_item(
            it, "sale_return", it.return_.date, it.return_.number,
            sale.customer.name if sale and sale.customer else "Umum",
            float(it.quarantine_unit_cost or 0),
        )
    for it in quarantine_query.all():
        returned_qty = float(db.query(func.coalesce(func.sum(models.PurchaseReturnItem.qty), 0.0)).join(
            models.PurchaseReturn, models.PurchaseReturn.id == models.PurchaseReturnItem.return_id
        ).filter(
            models.PurchaseReturnItem.inventory_quarantine_id == it.id,
            models.PurchaseReturn.status != "cancelled",
        ).scalar() or 0)
        if float(it.qty_open or 0) <= 1e-9 and returned_qty <= 1e-9:
            continue
        item_status = "returned" if returned_qty >= float(it.qty or 0) - 1e-9 else ("partial" if returned_qty > 0 else "not_returned")
        if status and status != item_status:
            continue
        result.append({
            "source_type": "inventory_quarantine", "source_item_id": it.id,
            "inventory_quarantine_id": it.id, "item_id": it.item_id,
            "item": {"id": it.item_id, "name": it.item.name, "code": it.item.code,
                     "barcode": it.item.barcode,
                     "tracks_serial": bool(get_stock_source_item(db, it.item).tracks_serial)},
            "return_price": float(it.unit_cost or 0),
            "quarantine_unit_cost": float(it.unit_cost or 0),
            "qty": float(it.qty or 0), "qty_returned": returned_qty,
            "qty_available": float(it.qty_open or 0), "status": item_status,
            "date": str(it.date), "trade_in_number": it.number, "source_number": it.number,
            "customer_name": it.supplier.name if it.supplier else "Karantina Gudang",
            "condition": "damaged",
        })
    result.sort(key=lambda row: row["date"], reverse=True)
    return result


def _damaged_source(db: Session, source_type: str, source_item_id: int,
                    branch_id: int, for_update: bool = False):
    if source_type == "trade_in":
        query = db.query(models.TradeInReturnItem).join(models.TradeIn).filter(
            models.TradeInReturnItem.id == source_item_id,
            models.TradeInReturnItem.condition == "damaged",
            models.TradeIn.branch_id == branch_id,
            models.TradeIn.status != "cancelled",
        )
    elif source_type == "sale_return":
        query = db.query(models.SaleReturnItem).join(models.SaleReturn).filter(
            models.SaleReturnItem.id == source_item_id,
            models.SaleReturnItem.condition == "damaged",
            models.SaleReturn.branch_id == branch_id,
            models.SaleReturn.status != "cancelled",
        )
    elif source_type == "inventory_quarantine":
        query = db.query(models.InventoryQuarantine).filter(
            models.InventoryQuarantine.id == source_item_id,
            models.InventoryQuarantine.branch_id == branch_id,
            models.InventoryQuarantine.qty_open > 0,
        )
    else:
        raise HTTPException(400, "Sumber barang rusak tidak valid")
    if for_update:
        query = query.with_for_update()
    source = query.first()
    if not source:
        raise HTTPException(404, "Barang rusak tidak ditemukan")
    return source


@router.get("/unlinked-stock-options/{item_id}")
def get_unlinked_stock_options(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Saldo stok yang sah untuk satu baris retur pembelian tanpa faktur."""
    if not has_role(current_user, "admin"):
        raise HTTPException(403, "Retur tanpa faktur hanya dapat dibuat admin")

    branch_id = resolve_active_branch_id(db, current_user)
    item = db.query(models.Item).filter(
        models.Item.id == item_id,
        models.Item.is_active == True,
    ).first()
    if not item:
        raise HTTPException(404, "Barang tidak ditemukan")

    warehouse = _default_branch_warehouse(db, branch_id)
    good_available = 0.0
    if warehouse:
        good_available = float(db.query(models.WarehouseStock.stock).filter(
            models.WarehouseStock.warehouse_id == warehouse.id,
            models.WarehouseStock.item_id == item.id,
        ).scalar() or 0)

    damaged_sources = []

    trade_rows = db.query(models.TradeInReturnItem).join(models.TradeIn).filter(
        models.TradeInReturnItem.item_id == item.id,
        models.TradeInReturnItem.condition == "damaged",
        models.TradeIn.branch_id == branch_id,
    ).all()
    for row in trade_rows:
        available = float(row.qty or 0) - float(row.returned_qty or 0)
        if available > 1e-9:
            damaged_sources.append({
                "source_type": "trade_in", "source_item_id": row.id,
                "source_number": row.trade_in.number, "date": str(row.trade_in.date),
                "qty_available": available,
            })

    sale_rows = db.query(models.SaleReturnItem).join(models.SaleReturn).filter(
        models.SaleReturnItem.item_id == item.id,
        models.SaleReturnItem.condition == "damaged",
        models.SaleReturn.branch_id == branch_id,
        models.SaleReturn.status != "cancelled",
    ).all()
    for row in sale_rows:
        available = float(row.qty or 0) - float(row.returned_qty or 0)
        if available > 1e-9:
            damaged_sources.append({
                "source_type": "sale_return", "source_item_id": row.id,
                "source_number": row.return_.number, "date": str(row.return_.date),
                "qty_available": available,
            })

    quarantine_rows = db.query(models.InventoryQuarantine).filter(
        models.InventoryQuarantine.branch_id == branch_id,
        models.InventoryQuarantine.item_id == item.id,
        models.InventoryQuarantine.qty_open > 1e-9,
    ).all()
    for row in quarantine_rows:
        damaged_sources.append({
            "source_type": "inventory_quarantine", "source_item_id": row.id,
            "source_number": row.number, "date": str(row.date),
            "qty_available": float(row.qty_open or 0),
        })

    damaged_sources.sort(key=lambda row: (row["date"], row["source_item_id"]), reverse=True)
    return {
        "item_id": item.id,
        "good": {
            "warehouse_id": warehouse.id if warehouse else None,
            "warehouse_name": warehouse.name if warehouse else None,
            "qty_available": max(0.0, good_available),
        },
        "damaged_sources": damaged_sources,
    }


@router.get("/history/broken/{source_type}/{source_item_id}/suppliers")
def get_broken_item_suppliers(
    source_type: str,
    source_item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    source = _damaged_source(db, source_type, source_item_id, branch_id)
    suppliers = (
        db.query(models.Supplier)
        .join(models.Purchase, models.Purchase.supplier_id == models.Supplier.id)
        .join(models.PurchaseItem, models.PurchaseItem.purchase_id == models.Purchase.id)
        .filter(
            models.Purchase.branch_id == branch_id,
            models.Purchase.status != "cancelled",
            models.PurchaseItem.item_id == source.item_id,
            models.Supplier.is_active == True,
        )
        .distinct()
        .order_by(models.Supplier.name.asc())
        .all()
    )
    return [{"id": supplier.id, "code": supplier.code, "name": supplier.name} for supplier in suppliers]


def _next_number(db, prefix, model):
    from datetime import date as d
    today = d.today().strftime("%Y%m%d")
    pfx = f"{prefix}{today}"
    last = db.query(model).filter(model.number.like(f"{pfx}%")).order_by(model.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{pfx}{seq:04d}"


# ══════════════════════════════════════════════════════════════════════════════
# RETUR PENJUALAN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales")
def get_sale_returns(skip: int = 0, limit: int = 100,
                     db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    branch_id = resolve_active_branch_id(db, current_user)
    query = db.query(models.SaleReturn).filter(models.SaleReturn.branch_id == branch_id)
        
    returns = query.order_by(models.SaleReturn.id.desc()).offset(skip).limit(limit).all()
    result = []
    for r in returns:
        sale = r.sale
        items_out = []
        for i in r.items:
            item = db.query(models.Item).get(i.item_id)
            items_out.append({
                "id": i.id, "item_id": i.item_id,
                "item_name": item.name if item else "-",
                "qty": i.qty, "price": i.price, "total": i.total,
                "condition": i.condition,
            })
        result.append({
            "id": r.id, "number": r.number, "date": str(r.date),
            "sale_id": r.sale_id,
            "sale_number": sale.number if sale else "-",
            "total": r.total, "reason": r.reason, "notes": r.notes,
            "status": r.status,
            "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
            "cancellation_reason": r.cancellation_reason,
            "items": items_out,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    return result


@router.post("/sales")
def create_sale_return(data: dict, db: Session = Depends(get_db),
                       current_user: models.User = Depends(get_current_user)):
    sale_id = data.get("sale_id")
    branch_id = resolve_active_branch_id(db, current_user)
    sale = db.query(models.Sale).filter(
        models.Sale.id == sale_id,
        models.Sale.branch_id == branch_id,
    ).with_for_update().first()
    if not sale: raise HTTPException(404, "Penjualan tidak ditemukan")
    if sale.status == "cancelled":
        raise HTTPException(400, "Penjualan yang sudah dibatalkan tidak dapat diretur")

    gudang_aktif = None
    if sale.branch_id:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == sale.branch_id,
            models.Warehouse.is_default == True,
        ).first()

    number = _next_number(db, "RS", models.SaleReturn)
    total_sales = 0.0
    total_tax = 0.0
    total_cogs = 0.0
    damaged_cogs = 0.0
    
    # Nilai retur wajib menjadi cermin faktur; mode pajak dari klien tidak dipercaya.
    is_tax_included = bool(getattr(sale, "is_tax_included", True))
    tax_percent = float(getattr(sale, "tax_percent", 0) or 0)

    # 🔒 Pastikan semua akun jurnal retur tersedia SEBELUM stok dimutasi, supaya jurnal tidak
    # gagal di tengah jalan dan meninggalkan stok berpindah tanpa pencatatan GL.
    from .accounting import pastikan_akun_ada
    _akun_wajib = ["2-1300", "4-1200", "1-1400", "5-1100"]
    if any(str(item.get("condition", "good")) == "damaged" for item in data.get("items", [])):
        _akun_wajib.append("1-1450")
    # Pastikan 2-1200 ada bila penjualan asli MEMUNGUT PPN — termasuk faktur tarif-campur yang
    # tax_percent header-nya 0 tetapi ada baris ber-PPN (sale.tax > 0).
    if (tax_percent and tax_percent > 0) or (getattr(sale, "tax", 0) or 0) > 0:
        _akun_wajib.append("2-1200")  # PPN Keluaran dibalik ke Hutang PPN (inklusif & eksklusif)
    pastikan_akun_ada(db, _akun_wajib)

    retur = models.SaleReturn(
        number=number,
        date=get_local_date(),
        sale_id=sale_id,
        branch_id=branch_id,
        created_by=current_user.id,
        tax_percent=tax_percent,
        is_tax_included=is_tax_included,
        reason=data.get("reason"),
        notes=data.get("notes")
    )
    db.add(retur); db.flush()

    return_items = data.get("items") or []
    if not return_items:
        raise HTTPException(400, "Retur harus memiliki minimal satu barang")
    seen_sale_lines = set()
    legacy_header_rate = bool(getattr(sale, "tax", 0)) and not any(
        float(line.ppn_percent or 0) > 0 for line in sale.items
    )

    for it in return_items:
        condition = str(it.get("condition", "good"))
        if condition not in {"good", "damaged"}:
            raise HTTPException(400, "Kondisi retur hanya boleh Bagus atau Rusak")
        qty = _positive_qty(it)
        it["qty"] = qty
        item = db.query(models.Item).with_for_update().get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")

        requested_line_id = it.get("sale_item_id")
        if requested_line_id is not None:
            try:
                requested_line_id = int(requested_line_id)
            except (TypeError, ValueError):
                raise HTTPException(400, "sale_item_id tidak valid")
        candidates = [si for si in sale.items if si.item_id == it["item_id"]]
        sale_item = next(
            (si for si in candidates if requested_line_id and si.id == requested_line_id),
            None,
        ) if requested_line_id else (candidates[0] if len(candidates) == 1 else None)
        if not sale_item:
            if len(candidates) > 1 and not requested_line_id:
                raise HTTPException(400, f"Barang {item.name} memiliki beberapa baris faktur; sale_item_id wajib dikirim")
            raise HTTPException(400, f"Item {item.name} tidak ada di faktur ini")
        if sale_item.id in seen_sale_lines:
            raise HTTPException(400, f"Baris retur {item.name} dikirim lebih dari sekali")
        seen_sale_lines.add(sale_item.id)

        # Cek total retur sebelumnya untuk baris faktur yang tepat. Baris legacy
        # tanpa sale_item_id tetap dihitung konservatif berdasarkan faktur+barang.
        total_prev = db.query(func.coalesce(func.sum(models.SaleReturnItem.qty), 0.0)).join(
            models.SaleReturn
        ).filter(
            models.SaleReturnItem.sale_item_id == sale_item.id,
            models.SaleReturn.status != "cancelled",
        ).scalar() or 0.0
        total_prev += db.query(func.coalesce(func.sum(models.SaleReturnItem.qty), 0.0)).join(
            models.SaleReturn
        ).filter(
            models.SaleReturn.sale_id == sale_id,
            models.SaleReturnItem.item_id == it["item_id"],
            models.SaleReturnItem.sale_item_id.is_(None),
            models.SaleReturn.status != "cancelled",
        ).scalar() or 0.0

        if total_prev + qty > sale_item.qty + 1e-9:
            raise HTTPException(400, f"Qty retur {item.name} melebihi qty terjual ({sale_item.qty - total_prev} tersisa)")

        _validate_return_serial_origin(
            db,
            entered_item=item,
            source_line=sale_item,
            serial_values=it.get("serials", []),
            source_type="sale",
        )

        # Harga retur berasal dari snapshot faktur setelah diskon baris dan
        # alokasi diskon header, bukan dari angka yang dapat diubah klien.
        actual_price = _sale_return_unit_price(sale, sale_item)

        gross_line = it["qty"] * actual_price
        # Pisahkan PPN agar KONSISTEN dgn jurnal JUAL: 4-1200 (Retur Penjualan) menerima nilai
        # EX-PPN & 2-1200 (PPN Keluaran) ikut dibalik. Tarif PER BARIS: utamakan tarif yang
        # TERSIMPAN di baris penjualan (akurat utk faktur tarif-campur); penjualan LAMA (ppn baris
        # 0) → pakai tarif header sbg cadangan. Inklusif → kupas PPN MUNDUR (mirror
        # hitung_total_penjualan di sales.py); eksklusif → PPN di atas. Tarif 0 → tanpa PPN.
        line_tarif = float(sale_item.ppn_percent or 0)
        if legacy_header_rate:
            line_tarif = tax_percent or 0
        if line_tarif and line_tarif > 0:
            if is_tax_included:
                line_sales = gross_line / (1 + line_tarif / 100)
                line_tax = gross_line - line_sales
            else:
                line_sales = gross_line
                line_tax = gross_line * (line_tarif / 100)
        else:
            line_sales = gross_line
            line_tax = 0.0
        total_sales += line_sales
        total_tax += line_tax
        
        # COGS yang dibalik dihitung di bawah (blok "Kembalikan stok"): dari biaya lapisan FIFO
        # yang NYATA dipulihkan (mode gudang) atau rata-rata buy_price (mode tanpa gudang).
        quarantine_unit_cost = float(sale_item.buy_price or 0) if condition == "damaged" else 0.0
        return_line = models.SaleReturnItem(
            return_id=retur.id, sale_item_id=sale_item.id, item_id=it["item_id"],
            qty=it["qty"], price=actual_price, total=line_sales + line_tax,
            condition=condition, quarantine_unit_cost=quarantine_unit_cost,
            serials_json=json.dumps(it.get("serials", []), ensure_ascii=False),
        )
        db.add(return_line)

        if condition == "damaged":
            # Barang rusak adalah aset karantina: tidak menambah stok etalase dan tidak
            # memulihkan lapisan FIFO yang dapat dijual.
            return_line.carrying_cost = float(it["qty"]) * quarantine_unit_cost
            damaged_cogs += return_line.carrying_cost
            stock_item = item
            if is_virtual_variant(item):
                stock_item = db.query(models.Item).get(item.parent_item_id) or item
            transition_serials(
                db, item=stock_item, qty=get_required_stock_qty(item, it["qty"]),
                serial_values=it.get("serials", []), from_statuses={"sold"},
                to_status="quarantine", from_warehouse_id=None, to_warehouse=None,
                reference_type="sale_return", reference_id=retur.id,
                reference_number=number, user_id=current_user.id, event_type="sale_return_damaged",
            )
            continue

        # Kembalikan stok
        stock_item = item
        if is_virtual_variant(item):
            stock_item = db.query(models.Item).with_for_update().get(item.parent_item_id) or item

        required_qty = get_required_stock_qty(item, it["qty"])
        transition_serials(
            db, item=stock_item, qty=required_qty, serial_values=it.get("serials", []),
            from_statuses={"sold"}, to_status="in_stock", from_warehouse_id=None,
            to_warehouse=gudang_aktif, reference_type="sale_return", reference_id=retur.id,
            reference_number=number, user_id=current_user.id, event_type="sale_return",
        )
        if gudang_aktif:
            from .warehouse import get_total_branch_stock
            before = get_total_branch_stock(db, sale.branch_id, stock_item.id)
        else:
            before = float(stock_item.stock or 0)
        if not gudang_aktif:
            stock_item.stock += required_qty
        if gudang_aktif:
            from .warehouse import adjust_warehouse_stock
            adjust_warehouse_stock(db, gudang_aktif.id, stock_item.id, required_qty)
            # 🧱 FIFO: pulihkan lapisan yang dulu dikonsumsi penjualan ini (retur bisa
            # sebagian) agar Σ batch == stok tetap terjaga & barang siap dijual lagi
            # dengan biaya asalnya. Nilai kembalian = biaya modal NYATA lapisan yang
            # dipulihkan → dipakai sebagai COGS yang dibalik supaya GL Persediaan/HPP tetap
            # sama dengan ledger batch (anti-drift pada retur sebagian).
            from ..services.inventory_fifo import restore_sale_return
            line_carrying = restore_sale_return(
                db,
                sale_item_id=sale_item.id,
                qty=required_qty,
                item_id=stock_item.id,
                warehouse_id=gudang_aktif.id,
                fallback_cost=(sale_item.buy_price or 0),
            )
        else:
            # Mode tanpa gudang (tak ada lapisan FIFO) → rata-rata buy_price seperti semula.
            line_carrying = it["qty"] * (sale_item.buy_price or 0)
        return_line.carrying_cost = line_carrying
        total_cogs += line_carrying
        db.add(models.StockMovement(
            date=get_local_date(),
            item_id=stock_item.id,
            branch_id=sale.branch_id,
            warehouse_id=gudang_aktif.id if gudang_aktif else None,
            type="in",
            qty=required_qty,
            qty_before=before,
            qty_after=before + required_qty,
            reference=number,
            notes=(
                f"Retur Penjualan {sale.number} - restore dari {item.name}"
                if stock_item.id != item.id
                else f"Retur Penjualan {sale.number}"
            ),
        ))

    total = total_sales + total_tax
    retur.total = total
    
    # Update Customer Deposit Balance
    if sale.customer_id:
        cust = db.query(models.Customer).with_for_update().get(sale.customer_id)
        if cust:
            from ..services.customer_deposits import apply_customer_deposit_change
            apply_customer_deposit_change(
                db, cust, total, event_type="sale_return", transaction_date=retur.date,
                branch_id=sale.branch_id, source_type="sale_return", source_id=retur.id,
                reference=retur.number, description=f"Retur pembelian {sale.number} masuk ke deposit",
                created_by=current_user.id,
            )
            
    # Buat jurnal DI DALAM transaksi yang sama dengan mutasi stok. Bila jurnal gagal,
    # seluruh retur (stok + saldo deposit) ikut rollback → tidak ada lagi "stok pindah tanpa
    # jurnal". Akun wajib sudah dipastikan ada di awal sehingga ini praktis tidak akan gagal.
    from ..services import journal_service
    journal_service.create_sale_return_journal(
        db,
        date_val=retur.date,
        number_ref=retur.number,
        customer_name=sale.customer.name if sale.customer else "Umum",
        total_sales=total_sales,
        total_tax=total_tax,
        total_cogs=total_cogs,
        damaged_cogs=damaged_cogs,
        is_tax_included=is_tax_included,
        user_id=current_user.id,
        branch_id=sale.branch_id
    )

    write_audit(db, current_user.id, "CREATE", "sale_returns", retur.id,
                f"Retur penjualan {sale.number} sebesar {total}")
    db.commit()
    return {"id": retur.id, "number": retur.number, "total": total, "message": "Retur penjualan berhasil"}


# ══════════════════════════════════════════════════════════════════════════════
# RETUR PEMBELIAN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/purchases")
def get_purchase_returns(skip: int = 0, limit: int = 100,
                          db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    branch_id = resolve_active_branch_id(db, current_user)
    query = db.query(models.PurchaseReturn).filter(models.PurchaseReturn.branch_id == branch_id)
        
    returns = query.order_by(models.PurchaseReturn.id.desc()).offset(skip).limit(limit).all()
    result = []
    for r in returns:
        purchase = r.purchase
        items_out = []
        for i in r.items:
            item = db.query(models.Item).get(i.item_id)
            items_out.append({
                "id": i.id, "item_id": i.item_id,
                "item_name": item.name if item else "-",
                "qty": i.qty, "price": i.price, "total": i.total
            })
        result.append({
            "id": r.id, "number": r.number, "date": str(r.date),
            "purchase_id": r.purchase_id,
            "purchase_number": purchase.number if purchase else "Tanpa Faktur",
            "supplier_name": r.supplier.name if r.supplier else (purchase.supplier.name if purchase and purchase.supplier else "-"),
            "return_mode": r.return_mode or "invoice",
            "evidence_reference": r.evidence_reference,
            "total": r.total,
            "total_carrying": r.total_carrying, "selisih": r.selisih,
            "reason": r.reason, "notes": r.notes,
            "status": r.status,
            "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
            "cancellation_reason": r.cancellation_reason,
            "items": items_out,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return result


def _create_unlinked_purchase_return(data: dict, db: Session, current_user: models.User, branch_id: int):
    """Retur di luar transaksi: klaim supplier terpisah dari arus biaya FIFO aktual."""
    if not has_role(current_user, "admin"):
        raise HTTPException(403, "Retur tanpa faktur hanya dapat dibuat admin")
    reason = str(data.get("reason") or "").strip()
    evidence = str(data.get("evidence_reference") or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan retur tanpa faktur wajib diisi")
    if not evidence:
        raise HTTPException(400, "Referensi bukti retur tanpa faktur wajib diisi")
    supplier_id = data.get("supplier_id")
    supplier = db.query(models.Supplier).with_for_update().get(supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier tidak ditemukan")
    warehouse = _default_branch_warehouse(db, branch_id)
    items = data.get("items") or []
    if not items:
        raise HTTPException(400, "Retur harus memiliki minimal satu barang")

    from .accounting import pastikan_akun_ada
    has_damaged = any(i.get("damaged_source_item_id") or i.get("trade_in_return_item_id") for i in items)
    required_accounts = ["1-1600", "1-1400", "4-2000", "5-1200"]
    if has_damaged:
        required_accounts.append("1-1450")
    pastikan_akun_ada(db, required_accounts)

    number = _next_number(db, "RP", models.PurchaseReturn)
    document = models.PurchaseReturn(
        number=number, date=get_local_date(), purchase_id=None, supplier_id=supplier.id,
        return_mode="unlinked", evidence_reference=evidence, branch_id=branch_id,
        created_by=current_user.id, tax_percent=0, is_tax_included=True,
        reason=reason, notes=data.get("notes"),
    )
    db.add(document)
    db.flush()
    total_claim = 0.0
    total_carrying = 0.0
    damaged_carrying = 0.0
    seen_items = set()

    for payload in items:
        qty = _positive_qty(payload)
        item = db.query(models.Item).with_for_update().get(payload.get("item_id"))
        if not item:
            raise HTTPException(404, "Barang retur tidak ditemukan")
        if item.id in seen_items:
            raise HTTPException(400, f"Barang {item.name} dikirim lebih dari sekali")
        seen_items.add(item.id)
        try:
            price = float(payload.get("price"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Harga klaim {item.name} harus berupa angka")
        if not math.isfinite(price) or price < 0:
            raise HTTPException(400, f"Harga klaim {item.name} tidak valid")

        source_type = payload.get("damaged_source_type")
        source_id = payload.get("damaged_source_item_id") or payload.get("trade_in_return_item_id")
        damaged_source = None
        carrying = 0.0
        if source_id:
            damaged_source = _damaged_source(db, str(source_type or "trade_in"), int(source_id), branch_id, for_update=True)
            if damaged_source.item_id != item.id:
                raise HTTPException(400, "Barang rusak tidak sesuai dengan item retur")
            available = float(damaged_source.qty_open or 0) if source_type == "inventory_quarantine" else float(damaged_source.qty or 0) - float(damaged_source.returned_qty or 0)
            if qty > available + 1e-9:
                raise HTTPException(400, f"Qty barang rusak {item.name} tersisa hanya {available}")
            unit_cost = float(damaged_source.return_price or 0) if source_type == "trade_in" else float(damaged_source.unit_cost or 0) if source_type == "inventory_quarantine" else float(damaged_source.quarantine_unit_cost or 0)
            carrying = qty * unit_cost
            if source_type == "inventory_quarantine":
                from ..services.serials import assert_quarantine_serials
                from ..services.quarantine_movements import record_quarantine_movement
                assert_quarantine_serials(
                    db, quarantine=damaged_source, qty=qty,
                    serial_values=payload.get("serials", []),
                )
                damaged_source.qty_open = available - qty
                damaged_source.status = "closed" if damaged_source.qty_open <= 1e-9 else "partial"
                record_quarantine_movement(
                    db, quarantine=damaged_source, qty=qty,
                    category="quarantine_supplier_return", date_value=document.date,
                    reference=number, quarantine_before=available,
                    quarantine_after=damaged_source.qty_open,
                    notes=f"Retur supplier tanpa faktur: {evidence}",
                )
            else:
                damaged_source.returned_qty = float(damaged_source.returned_qty or 0) + qty
            damaged_carrying += carrying
        else:
            if not warehouse:
                raise HTTPException(409, "Gudang aktif diperlukan untuk retur tanpa faktur")
            from .warehouse import get_warehouse_stock, adjust_warehouse_stock, get_total_branch_stock
            if get_warehouse_stock(db, warehouse.id, item.id) + 1e-9 < qty:
                raise HTTPException(400, f"Stok {item.name} tidak cukup untuk retur")
            before = get_total_branch_stock(db, branch_id, item.id)
            from ..services.inventory_fifo import consume_fifo
            allocations = consume_fifo(db, item_id=item.id, warehouse_id=warehouse.id, qty=qty)
            carrying = sum(float(q) * float(cost) for _batch, q, cost in allocations)
            adjust_warehouse_stock(db, warehouse.id, item.id, -qty)
            if branch_id == PUSAT_BRANCH_ID:
                item.stock = float(item.stock or 0) - qty
            db.add(models.StockMovement(
                date=get_local_date(), item_id=item.id, branch_id=branch_id, warehouse_id=warehouse.id,
                type="out", qty=qty, qty_before=before, qty_after=before-qty,
                reference=number, notes=f"Retur tanpa faktur: {evidence}",
            ))

        transition_serials(
            db, item=item, qty=qty, serial_values=payload.get("serials", []),
            from_statuses={"quarantine"} if damaged_source else {"in_stock"},
            to_status="returned_supplier", from_warehouse_id=None if damaged_source else warehouse.id,
            to_warehouse=None, reference_type="purchase_return", reference_id=document.id,
            reference_number=number, user_id=current_user.id, event_type="purchase_return_unlinked",
        )
        claim = qty * price
        total_claim += claim
        total_carrying += carrying
        db.add(models.PurchaseReturnItem(
            return_id=document.id, purchase_item_id=None, item_id=item.id, qty=qty,
            price=price, total=claim, carrying_cost=carrying, quarantine_cost=carrying if damaged_source else 0,
            trade_in_return_item_id=int(source_id) if source_id and source_type == "trade_in" else None,
            sale_return_item_id=int(source_id) if source_id and source_type == "sale_return" else None,
            inventory_quarantine_id=int(source_id) if source_id and source_type == "inventory_quarantine" else None,
            serials_json=json.dumps(payload.get("serials", []), ensure_ascii=False),
        ))

    document.total = total_claim
    document.total_carrying = total_carrying
    document.selisih = total_claim - total_carrying
    supplier.deposit_balance = float(supplier.deposit_balance or 0) + total_claim
    from ..services import journal_service
    journal_service.create_purchase_return_journal(
        db, date_val=document.date, number_ref=number, supplier_name=supplier.name,
        total_inventory=total_claim, total_tax=0, total_carrying=total_carrying,
        damaged_carrying=damaged_carrying, is_tax_included=True, ppn_dipisah=False,
        user_id=current_user.id, branch_id=branch_id,
    )
    write_audit(db, current_user.id, "CREATE", "purchase_returns", document.id, f"Retur tanpa faktur; bukti {evidence}")
    db.commit()
    return {"id": document.id, "number": number, "total": total_claim,
            "total_carrying": round(total_carrying, 2), "selisih": round(document.selisih, 2),
            "message": "Retur pembelian tanpa faktur berhasil"}


@router.post("/purchases")
def create_purchase_return(data: dict, db: Session = Depends(get_db),
                            current_user: models.User = Depends(get_current_user)):
    purchase_id = data.get("purchase_id")
    branch_id = resolve_active_branch_id(db, current_user)
    return_mode = str(data.get("return_mode") or ("invoice" if purchase_id else "unlinked")).lower()
    if return_mode == "unlinked":
        return _create_unlinked_purchase_return(data, db, current_user, branch_id)
    if return_mode != "invoice":
        raise HTTPException(400, "Mode retur pembelian tidak valid")
    purchase = db.query(models.Purchase).filter(
        models.Purchase.id == purchase_id,
        models.Purchase.branch_id == branch_id,
    ).with_for_update().first()
    if not purchase: raise HTTPException(404, "Pembelian tidak ditemukan")
    if purchase.status in {"cancelled", "draft", "pending"}:
        raise HTTPException(400, "Pembelian yang belum diterima atau sudah dibatalkan tidak dapat diretur")

    gudang_aktif = None
    if purchase.branch_id:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == purchase.branch_id,
            models.Warehouse.is_default == True,
        ).first()

    number = _next_number(db, "RP", models.PurchaseReturn)
    total_inventory = 0.0   # nilai refund barang (qty × harga retur)
    total_carrying = 0.0    # biaya modal FIFO nyata yang keluar (untuk selisih harga)
    damaged_carrying = 0.0  # bagian modal yang keluar dari aset karantina
    total_tax = 0.0
    
    # Pajak retur selalu mengikuti faktur asli. Nilai dari frontend hanya
    # dipakai untuk tampilan lama, bukan sebagai sumber perhitungan akuntansi.
    tax_type = purchase_tax_type(purchase)
    is_tax_included = tax_type == "include"
    tax_percent = float(getattr(purchase, "tax_percent", 0) or 0)

    # 🔒 Pastikan akun jurnal retur tersedia SEBELUM stok dimutasi, supaya jurnal tidak gagal
    # di tengah jalan dan meninggalkan stok berpindah tanpa pencatatan GL. (Akun selisih
    # 4-2000/5-1200 sengaja TIDAK dipaksa ada — sudah ada fallback 2-kaki di journal_service.)
    from .accounting import pastikan_akun_ada
    _akun_wajib = ["1-1600", "1-1400"]
    has_damaged_source = any(
        item.get("damaged_source_item_id") or item.get("trade_in_return_item_id")
        for item in data.get("items", [])
    )
    if has_damaged_source:
        _akun_wajib.extend(["1-1450", "4-2000", "5-1200"])
    if getattr(purchase, "ppn_dipisah", False):
        _akun_wajib.append("1-1550")  # PKP: PPN Masukan (1-1550) ikut dibalik saat retur
    elif tax_type == "exclude" and tax_percent > 0:
        _akun_wajib.append("5-2000")
    pastikan_akun_ada(db, _akun_wajib)

    retur = models.PurchaseReturn(
        number=number,
        date=get_local_date(),
        purchase_id=purchase_id,
        supplier_id=purchase.supplier_id,
        return_mode="invoice",
        branch_id=branch_id,
        created_by=current_user.id,
        tax_percent=tax_percent,
        is_tax_included=is_tax_included,
        reason=data.get("reason"),
        notes=data.get("notes")
    )
    db.add(retur); db.flush()

    return_items = data.get("items") or []
    if not return_items:
        raise HTTPException(400, "Retur harus memiliki minimal satu barang")
    seen_purchase_lines = set()

    for it in return_items:
        qty = _positive_qty(it)
        it["qty"] = qty
        item = db.query(models.Item).with_for_update().get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")

        requested_line_id = it.get("purchase_item_id")
        if requested_line_id is not None:
            try:
                requested_line_id = int(requested_line_id)
            except (TypeError, ValueError):
                raise HTTPException(400, "purchase_item_id tidak valid")
        candidates = [pi for pi in purchase.items if pi.item_id == it["item_id"]]
        pur_item = next(
            (pi for pi in candidates if requested_line_id and pi.id == requested_line_id),
            None,
        ) if requested_line_id else (candidates[0] if len(candidates) == 1 else None)
        if not pur_item:
            if len(candidates) > 1 and not requested_line_id:
                raise HTTPException(400, f"Barang {item.name} memiliki beberapa baris faktur; purchase_item_id wajib dikirim")
            raise HTTPException(400, f"Item {item.name} tidak ada di pembelian ini")
        if pur_item.id in seen_purchase_lines:
            raise HTTPException(400, f"Baris retur {item.name} dikirim lebih dari sekali")
        seen_purchase_lines.add(pur_item.id)

        total_prev = db.query(func.coalesce(func.sum(models.PurchaseReturnItem.qty), 0.0)).join(
            models.PurchaseReturn
        ).filter(
            models.PurchaseReturnItem.purchase_item_id == pur_item.id,
            models.PurchaseReturn.status != "cancelled",
        ).scalar() or 0.0
        total_prev += db.query(func.coalesce(func.sum(models.PurchaseReturnItem.qty), 0.0)).join(
            models.PurchaseReturn
        ).filter(
            models.PurchaseReturn.purchase_id == purchase_id,
            models.PurchaseReturnItem.item_id == it["item_id"],
            models.PurchaseReturnItem.purchase_item_id.is_(None),
            models.PurchaseReturn.status != "cancelled",
        ).scalar() or 0.0
        if total_prev + qty > float(pur_item.qty or 0) + 1e-9:
            raise HTTPException(
                400,
                f"Qty retur {item.name} melebihi qty pembelian ({float(pur_item.qty or 0) - total_prev} tersisa)",
            )
        _validate_return_serial_origin(
            db,
            entered_item=item,
            source_line=pur_item,
            serial_values=it.get("serials", []),
            source_type="purchase",
        )
        if purchase.from_po_id and gudang_aktif:
            provenance_stock = db.query(func.sum(models.StockBatch.qty_remaining)).filter(
                models.StockBatch.purchase_item_id == pur_item.id,
                models.StockBatch.warehouse_id == gudang_aktif.id,
            ).scalar() or 0
            if float(it["qty"]) > float(provenance_stock) + 1e-9:
                raise HTTPException(
                    400,
                    f"Barang {item.name} dari kiriman cabang yang sudah kembali ke pusat hanya {provenance_stock}",
                )

        # Harga retur dihitung dari snapshot faktur setelah diskon bertingkat
        # dan diskon header; nilai harga dari klien hanya bersifat tampilan.
        actual_price = _purchase_return_unit_price(purchase, pur_item)

        line_gross = it["qty"] * actual_price
        if tax_type == "include" and getattr(purchase, "ppn_dipisah", False):
            # PKP + Included: harga retur sudah termasuk PPN → kupas mundur PER-BARIS pakai tarif
            # saat beli (pur_item.ppn_percent; mundur ke tarif header bila kosong). total_inventory =
            # NET, total_tax = PPN → jurnal membalik PPN Masukan (1-1550).
            r = float(pur_item.ppn_percent) if getattr(pur_item, "ppn_percent", None) is not None else float(tax_percent or 0)
            line_inventory = line_gross / (1 + r / 100) if r > 0 else line_gross
            line_tax = line_gross - line_inventory
        elif tax_type == "include":
            # non-PKP Included: PPN melebur di modal (tak dipisah) → perilaku lama, tanpa kaki pajak.
            line_inventory = line_gross
            line_tax = 0.0
        elif tax_type == "exclude":
            # Excluded: harga ex-PPN, PPN ditambah di atas (satu tarif header).
            line_inventory = line_gross
            line_tax = line_inventory * (tax_percent / 100)
        else:
            # Tanpa PPN: harga barang dikembalikan apa adanya.
            line_inventory = line_gross
            line_tax = 0.0
        total_inventory += line_inventory
        total_tax += line_tax

        source_type = it.get("damaged_source_type")
        source_item_id = it.get("damaged_source_item_id")
        if not source_item_id and it.get("trade_in_return_item_id"):
            source_type = "trade_in"
            source_item_id = it.get("trade_in_return_item_id")

        damaged_source = None
        source_cost = 0.0
        if source_item_id:
            damaged_source = _damaged_source(
                db, str(source_type), int(source_item_id), branch_id, for_update=True
            )
            if damaged_source.item_id != item.id:
                raise HTTPException(400, "Barang rusak tidak sesuai dengan item pada faktur")
            source_available = (
                float(damaged_source.qty_open or 0)
                if source_type == "inventory_quarantine"
                else float(damaged_source.qty or 0) - float(damaged_source.returned_qty or 0)
            )
            if float(it["qty"]) > source_available + 1e-6:
                raise HTTPException(400, f"Qty barang rusak tersisa hanya {source_available}")
            unit_cost = (
                float(damaged_source.return_price or 0) if source_type == "trade_in"
                else float(damaged_source.unit_cost or 0) if source_type == "inventory_quarantine"
                else float(damaged_source.quarantine_unit_cost or 0)
            )
            source_cost = unit_cost * float(it["qty"])

        return_line = models.PurchaseReturnItem(
            return_id=retur.id, purchase_item_id=pur_item.id, item_id=it["item_id"],
            qty=it["qty"], price=actual_price, total=line_inventory + line_tax,
            trade_in_return_item_id=(int(source_item_id) if source_type == "trade_in" else None),
            sale_return_item_id=(int(source_item_id) if source_type == "sale_return" else None),
            inventory_quarantine_id=(int(source_item_id) if source_type == "inventory_quarantine" else None),
            quarantine_cost=source_cost,
            serials_json=json.dumps(it.get("serials", []), ensure_ascii=False),
        )
        db.add(return_line)

        if damaged_source:
            if source_type == "inventory_quarantine":
                from ..services.serials import assert_quarantine_serials
                from ..services.quarantine_movements import record_quarantine_movement
                quarantine_before = float(damaged_source.qty_open or 0)
                assert_quarantine_serials(
                    db, quarantine=damaged_source, qty=float(it["qty"]),
                    serial_values=it.get("serials", []),
                )
                damaged_source.qty_open = quarantine_before - float(it["qty"])
                damaged_source.status = "closed" if damaged_source.qty_open <= 1e-9 else "partial"
                record_quarantine_movement(
                    db, quarantine=damaged_source, qty=float(it["qty"]),
                    category="quarantine_supplier_return", date_value=retur.date,
                    reference=number, quarantine_before=quarantine_before,
                    quarantine_after=damaged_source.qty_open,
                    notes=f"Retur Pembelian {purchase.number}",
                )
            else:
                damaged_source.returned_qty = float(damaged_source.returned_qty or 0) + float(it["qty"])
            total_carrying += source_cost
            damaged_carrying += source_cost
            return_line.carrying_cost = source_cost
            transition_serials(
                db, item=item, qty=it["qty"], serial_values=it.get("serials", []),
                from_statuses={"quarantine"}, to_status="returned_supplier",
                from_warehouse_id=None, to_warehouse=None,
                reference_type="purchase_return", reference_id=retur.id,
                reference_number=number, user_id=current_user.id,
                event_type="purchase_return_quarantine",
            )
            # Sumbernya stok karantina; stok etalase dan lapisan FIFO tidak disentuh.
            continue

        transition_serials(
            db, item=item, qty=it["qty"], serial_values=it.get("serials", []),
            from_statuses={"in_stock", "quarantine"}, to_status="returned_supplier",
            from_warehouse_id=gudang_aktif.id if gudang_aktif else None,
            to_warehouse=None, reference_type="purchase_return", reference_id=retur.id,
            reference_number=number, user_id=current_user.id, event_type="purchase_return",
        )
        
        # Kurangi stok
        if gudang_aktif:
            from .warehouse import get_warehouse_stock, adjust_warehouse_stock
            stok_lokal = get_warehouse_stock(db, gudang_aktif.id, item.id)
            if stok_lokal < it["qty"]:
                raise HTTPException(400, f"Stok {item.name} tidak cukup untuk retur pembelian.")
        elif item.stock < it["qty"]:
            raise HTTPException(400, f"Stok {item.name} tidak cukup untuk retur pembelian.")

        if gudang_aktif:
            from .warehouse import get_total_branch_stock
            before = get_total_branch_stock(db, purchase.branch_id, item.id)
        else:
            before = float(item.stock or 0)
        if not gudang_aktif:
            item.stock -= it["qty"]
        # Default (mode global / fallback): modal = nilai refund → tanpa selisih.
        line_carrying = line_inventory
        if gudang_aktif:
            adjust_warehouse_stock(db, gudang_aktif.id, item.id, -it["qty"])
            # 🧱 FIFO: kurangi lapisan — utamakan batch dari pembelian yang diretur.
            # Bila batch itu sudah terjual (FIFO), fungsi ini mundur ke batch terbaru
            # supaya Σ batch == stok tetap terjaga (kasus koreksi biaya/"swap" lintas
            # supplier ditangani terpisah di Fase 3).
            from ..services.inventory_fifo import consume_fifo
            allocations = consume_fifo(db, item_id=item.id, warehouse_id=gudang_aktif.id, qty=it["qty"])
            line_carrying = sum(float(q) * float(cost) for _batch, q, cost in allocations)
        total_carrying += line_carrying
        return_line.carrying_cost = line_carrying
        db.add(models.StockMovement(
            date=get_local_date(),
            item_id=item.id,
            branch_id=purchase.branch_id,
            warehouse_id=gudang_aktif.id if gudang_aktif else None,
            type="out",
            qty=it["qty"],
            qty_before=before, qty_after=before - it["qty"],
            reference=number, notes=f"Retur Pembelian {purchase.number}"
        ))

    total = total_inventory + total_tax
    retur.total = total
    # Keterlacakan selisih retur (untung+/rugi−) — sejajar dgn variance jurnal di
    # journal_service.create_purchase_return_journal: mode gudang/FIFO pakai modal NYATA yang
    # keluar; mode PKP (ppn_dipisah) hitung selisih pada nilai NET; tanpa gudang → tak ada selisih.
    if gudang_aktif or damaged_carrying > 0:
        retur.total_carrying = total_carrying
        if getattr(purchase, "ppn_dipisah", False) and total_tax > 0.005:
            retur.selisih = total_inventory - total_carrying
        else:
            retur.selisih = (total_inventory + total_tax) - total_carrying
    else:
        retur.total_carrying = total_inventory  # tanpa lapisan FIFO: modal = nilai refund
        retur.selisih = 0.0

    # Update Supplier Deposit Balance
    if purchase.supplier_id:
        supp = db.query(models.Supplier).with_for_update().get(purchase.supplier_id)
        if supp:
            supp.deposit_balance = (supp.deposit_balance or 0) + total
            
    # Buat jurnal DI DALAM transaksi yang sama dengan mutasi stok → bila jurnal gagal, seluruh
    # retur (stok + saldo supplier) ikut rollback. Akun wajib sudah dipastikan ada di awal.
    from ..services import journal_service
    journal_service.create_purchase_return_journal(
        db,
        date_val=retur.date,
        number_ref=retur.number,
        supplier_name=purchase.supplier.name if purchase.supplier else "-",
        total_inventory=total_inventory,
        # Mode gudang → kirim biaya landed nyata; mode tanpa gudang → None (pajak dibalik terpisah).
        total_carrying=(total_carrying if gudang_aktif or damaged_carrying > 0 else None),
        damaged_carrying=damaged_carrying,
        total_tax=total_tax,
        is_tax_included=is_tax_included,
        # Ikuti mode PPN saat faktur DIBELI (tersimpan di faktur), bukan saklar PKP saat ini.
        ppn_dipisah=bool(getattr(purchase, "ppn_dipisah", False)),
        user_id=current_user.id,
        branch_id=purchase.branch_id
    )

    write_audit(db, current_user.id, "CREATE", "purchase_returns", retur.id, f"Retur {purchase.number}")
    db.commit()
    return {
        "id": retur.id, "number": retur.number, "total": total,
        "total_carrying": round(retur.total_carrying or 0, 2),
        "selisih": round(retur.selisih or 0, 2),
        "message": "Retur pembelian berhasil",
    }


@router.post("/sales/{return_id}/cancel")
def cancel_sale_return(
    return_id: int,
    data: ReturnCancel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    document = db.query(models.SaleReturn).filter(
        models.SaleReturn.id == return_id,
        models.SaleReturn.branch_id == branch_id,
    ).with_for_update().first()
    if not document:
        raise HTTPException(404, "Retur penjualan tidak ditemukan")
    if document.status != "posted":
        raise HTTPException(409, "Retur penjualan sudah dibatalkan")
    from .accounting import assert_books_open
    assert_books_open(db, branch_id, get_local_date(), "Pembatalan retur penjualan")
    warehouse = _default_branch_warehouse(db, branch_id)

    for line in document.items:
        entered_item = db.query(models.Item).get(line.item_id)
        stock_item = get_stock_source_item(db, entered_item)
        required_qty = get_required_stock_qty(entered_item, line.qty)
        if line.condition == "good":
            movement_scope = db.query(models.StockMovement.id).filter(
                models.StockMovement.item_id == stock_item.id,
                models.StockMovement.created_at > document.created_at,
                models.StockMovement.reference != document.number,
            )
            movement_scope = movement_scope.filter(
                models.StockMovement.warehouse_id == warehouse.id
            ) if warehouse else movement_scope.filter(models.StockMovement.branch_id == branch_id)
            later = movement_scope.first()
            if later:
                raise HTTPException(409, f"Stok {stock_item.name} sudah bergerak setelah retur; reversal ditolak")
            if warehouse:
                from .warehouse import adjust_warehouse_stock, get_total_branch_stock, get_warehouse_stock
                if get_warehouse_stock(db, warehouse.id, stock_item.id) + 1e-9 < required_qty:
                    raise HTTPException(409, f"Stok {stock_item.name} tidak cukup untuk reversal")
                from ..services.inventory_fifo import reduce_batches_for_reversal
                cost, _ = reduce_batches_for_reversal(
                    db, item_id=stock_item.id, warehouse_id=warehouse.id, qty=required_qty
                )
                if abs(cost - float(line.carrying_cost or 0)) > 0.01:
                    raise HTTPException(409, f"Nilai FIFO {stock_item.name} sudah berubah; reversal ditolak")
                before = get_total_branch_stock(db, branch_id, stock_item.id)
                adjust_warehouse_stock(db, warehouse.id, stock_item.id, -required_qty)
            else:
                before = float(stock_item.stock or 0)
                if before + 1e-9 < required_qty:
                    raise HTTPException(409, f"Stok {stock_item.name} tidak cukup untuk reversal")
                stock_item.stock = before - required_qty
            db.add(models.StockMovement(
                date=get_local_date(), item_id=stock_item.id, branch_id=branch_id,
                warehouse_id=warehouse.id if warehouse else None, type="reversal_out", qty=required_qty,
                qty_before=before, qty_after=before-required_qty,
                reference=f"REV-{document.number}", notes=f"Pembatalan {document.number}",
            ))

    reverse_reference_serials(
        db, reference_type="sale_return", reference_id=document.id,
        reversal_reference_number=f"REV-{document.number}", user_id=current_user.id,
    )
    if document.sale and document.sale.customer_id:
        customer = db.query(models.Customer).with_for_update().get(document.sale.customer_id)
        if not customer or float(customer.deposit_balance or 0) + 0.01 < float(document.total or 0):
            raise HTTPException(409, "Saldo retur pelanggan sudah digunakan; reversal ditolak")
        from ..services.customer_deposits import apply_customer_deposit_change
        apply_customer_deposit_change(
            db, customer, -float(document.total or 0), event_type="sale_return_reversal",
            transaction_date=get_local_date(), branch_id=branch_id,
            source_type="sale_return_reversal", source_id=document.id,
            reference=f"REV-{document.number}", description=data.reason,
            created_by=current_user.id,
        )
    reversal = _reverse_return_journal(
        db, reference=document.number, branch_id=branch_id, user_id=current_user.id
    )
    document.status = "cancelled"
    document.cancelled_by = current_user.id
    document.cancelled_at = datetime.now(WITA)
    document.cancellation_reason = data.reason.strip()
    document.reversal_journal_id = reversal.id
    write_audit(db, current_user.id, "DELETE", "sale_returns", document.id, data.reason.strip())
    db.commit()
    return {"message": "Retur penjualan berhasil dibatalkan", "id": document.id}


@router.post("/sales/{return_id}/print")
def print_sale_return(
    return_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    return_doc = db.query(models.SaleReturn).filter(
        models.SaleReturn.id == return_id,
        models.SaleReturn.branch_id == branch_id,
    ).first()
    if not return_doc:
        raise HTTPException(404, "Retur penjualan tidak ditemukan")
    if return_doc.status == "cancelled":
        raise HTTPException(409, "Retur yang dibatalkan tidak dapat dicetak sebagai transaksi aktif")
    branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    job = enqueue_print_job(
        db,
        branch_id=branch_id,
        content=build_sale_return_receipt(return_doc, settings_from_branch(branch)),
        document_type="sale_return",
        document_id=return_doc.id,
        created_by=current_user.id,
    )
    write_audit(db, current_user.id, "PRINT", "sale_returns", return_doc.id, "Antre cetak struk retur")
    db.commit()
    return {"status": "queued", "job_id": job.id, "branch_id": branch_id}


@router.post("/purchases/{return_id}/cancel")
def cancel_purchase_return(
    return_id: int,
    data: ReturnCancel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    document = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.id == return_id,
        models.PurchaseReturn.branch_id == branch_id,
    ).with_for_update().first()
    if not document:
        raise HTTPException(404, "Retur pembelian tidak ditemukan")
    if document.status != "posted":
        raise HTTPException(409, "Retur pembelian sudah dibatalkan")
    from .accounting import assert_books_open
    assert_books_open(db, branch_id, get_local_date(), "Pembatalan retur pembelian")
    warehouse = _default_branch_warehouse(db, branch_id)

    for line in document.items:
        if line.trade_in_return_item_id or line.sale_return_item_id or line.inventory_quarantine_id:
            if line.inventory_quarantine_id:
                source = db.query(models.InventoryQuarantine).with_for_update().get(line.inventory_quarantine_id)
                if not source:
                    raise HTTPException(409, "Sumber karantina tidak dapat dipulihkan")
                from ..services.quarantine_movements import record_quarantine_movement
                quarantine_before = float(source.qty_open or 0)
                source.qty_open = quarantine_before + float(line.qty or 0)
                source.status = "open" if source.qty_open >= float(source.qty or 0) - 1e-9 else "partial"
                record_quarantine_movement(
                    db, quarantine=source, qty=float(line.qty or 0),
                    category="quarantine_supplier_return_reversal",
                    date_value=get_local_date(), reference=f"REV-{document.number}",
                    quarantine_before=quarantine_before,
                    quarantine_after=source.qty_open,
                    notes=f"Pembatalan {document.number}: {data.reason.strip()}",
                )
                continue
            source = (
                db.query(models.TradeInReturnItem).with_for_update().get(line.trade_in_return_item_id)
                if line.trade_in_return_item_id
                else db.query(models.SaleReturnItem).with_for_update().get(line.sale_return_item_id)
            )
            if not source or float(source.returned_qty or 0) + 1e-9 < float(line.qty or 0):
                raise HTTPException(409, "Sumber barang karantina tidak dapat dipulihkan")
            source.returned_qty = float(source.returned_qty or 0) - float(line.qty or 0)
            continue

        entered_item = db.query(models.Item).get(line.item_id)
        stock_item = get_stock_source_item(db, entered_item)
        required_qty = get_required_stock_qty(entered_item, line.qty)
        if warehouse:
            from .warehouse import adjust_warehouse_stock, get_total_branch_stock
            from ..services.inventory_fifo import add_batch
            before = get_total_branch_stock(db, branch_id, stock_item.id)
            adjust_warehouse_stock(db, warehouse.id, stock_item.id, required_qty)
            unit_cost = float(line.carrying_cost or 0) / required_qty if required_qty else 0
            add_batch(
                db, item_id=stock_item.id, warehouse_id=warehouse.id, qty=required_qty,
                unit_cost=unit_cost, received_date=get_local_date(),
                purchase_item_id=line.purchase_item_id,
            )
        else:
            before = float(stock_item.stock or 0)
            stock_item.stock = before + required_qty
        db.add(models.StockMovement(
            date=get_local_date(), item_id=stock_item.id, branch_id=branch_id,
            warehouse_id=warehouse.id if warehouse else None, type="reversal_in", qty=required_qty,
            qty_before=before, qty_after=before+required_qty,
            reference=f"REV-{document.number}", notes=f"Pembatalan {document.number}",
        ))

    reverse_reference_serials(
        db, reference_type="purchase_return", reference_id=document.id,
        reversal_reference_number=f"REV-{document.number}", user_id=current_user.id,
    )
    supplier_id = document.supplier_id or (document.purchase.supplier_id if document.purchase else None)
    if supplier_id:
        supplier = db.query(models.Supplier).with_for_update().get(supplier_id)
        if not supplier or float(supplier.deposit_balance or 0) + 0.01 < float(document.total or 0):
            raise HTTPException(409, "Deposit supplier dari retur sudah digunakan; reversal ditolak")
        supplier.deposit_balance = float(supplier.deposit_balance or 0) - float(document.total or 0)
    reversal = _reverse_return_journal(
        db, reference=document.number, branch_id=branch_id, user_id=current_user.id
    )
    document.status = "cancelled"
    document.cancelled_by = current_user.id
    document.cancelled_at = datetime.now(WITA)
    document.cancellation_reason = data.reason.strip()
    document.reversal_journal_id = reversal.id
    write_audit(db, current_user.id, "DELETE", "purchase_returns", document.id, data.reason.strip())
    db.commit()
    return {"message": "Retur pembelian berhasil dibatalkan", "id": document.id}


@router.post("/purchases/{return_id}/print")
def print_purchase_return(
    return_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    return_doc = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.id == return_id,
        models.PurchaseReturn.branch_id == branch_id,
    ).first()
    if not return_doc:
        raise HTTPException(404, "Retur pembelian tidak ditemukan")
    if return_doc.status == "cancelled":
        raise HTTPException(409, "Retur yang dibatalkan tidak dapat dicetak sebagai transaksi aktif")
    branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    job = enqueue_print_job(
        db,
        branch_id=branch_id,
        content=build_purchase_return_receipt(return_doc, settings_from_branch(branch)),
        document_type="purchase_return",
        document_id=return_doc.id,
        created_by=current_user.id,
    )
    write_audit(db, current_user.id, "PRINT", "purchase_returns", return_doc.id, "Antre cetak struk retur")
    db.commit()
    return {"status": "queued", "job_id": job.id, "branch_id": branch_id}


# ══════════════════════════════════════════════════════════════════════════════
# FASE 3 — SWAP BATCH ANTAR-SUPPLIER + RESTATEMENT HPP
# ══════════════════════════════════════════════════════════════════════════════
# Skenario: barang supplier A sudah "terjual" menurut FIFO (batch A habis), tapi
# pengguna ingin meretur barang A. Solusi: tukar alokasi penjualan dari batch A ke
# batch supplier lain (B) yang stoknya masih ada → barang A kembali on-hand & bisa
# diretur. Jurnal HPP penjualan lama dikoreksi (bertanggal periode asal, boleh sudah
# Tutup Buku); karena laporan dihitung live, neraca/laba-rugi lama otomatis update.

EPS_SWAP = 1e-6


@router.get("/swap-candidates")
def get_swap_candidates(
    purchase_id: int,
    item_id: int,
    qty: float,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Cek apakah retur pembelian ini butuh SWAP. Bila batch dari pembelian tsb sudah
    kurang dari qty yang mau diretur (terjual FIFO), kembalikan daftar batch item yang
    sama dari SUPPLIER LAIN yang stoknya masih ada untuk ditukar."""
    purchase = db.query(models.Purchase).get(purchase_id)
    if not purchase:
        raise HTTPException(404, "Pembelian tidak ditemukan")
    pur_item = next((pi for pi in purchase.items if pi.item_id == item_id), None)
    if not pur_item:
        raise HTTPException(400, "Item tidak ada di pembelian ini")

    gudang_aktif = None
    if purchase.branch_id:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == purchase.branch_id,
            models.Warehouse.is_default == True,
        ).first()
    if not gudang_aktif:
        return {"needs_swap": False, "reason": "Mode tanpa gudang — retur biasa."}

    from_batch = db.query(models.StockBatch).filter(
        models.StockBatch.purchase_item_id == pur_item.id,
        models.StockBatch.warehouse_id == gudang_aktif.id,
    ).first()
    if not from_batch:
        return {"needs_swap": False, "reason": "Pembelian ini belum punya lapisan FIFO."}

    on_hand = float(from_batch.qty_remaining or 0)
    shortfall = float(qty) - on_hand
    if shortfall <= EPS_SWAP:
        return {"needs_swap": False, "from_batch_id": from_batch.id, "on_hand": round(on_hand, 4)}

    # Hanya qty yang benar-benar "terjual" dari batch A yang bisa di-swap balik
    swappable = float(
        db.query(func.coalesce(func.sum(models.SaleItemBatch.qty), 0.0))
        .filter(models.SaleItemBatch.batch_id == from_batch.id)
        .scalar()
        or 0.0
    )

    cands = (
        db.query(models.StockBatch)
        .filter(
            models.StockBatch.item_id == item_id,
            models.StockBatch.warehouse_id == gudang_aktif.id,
            models.StockBatch.qty_remaining > EPS_SWAP,
            models.StockBatch.id != from_batch.id,
        )
        .order_by(models.StockBatch.received_date.desc(), models.StockBatch.id.desc())
        .all()
    )
    candidates = []
    for b in cands:
        supp = db.query(models.Supplier).get(b.supplier_id) if b.supplier_id else None
        candidates.append({
            "batch_id": b.id,
            "supplier_id": b.supplier_id,
            "supplier_name": supp.name if supp else "Tanpa supplier / saldo awal",
            "unit_cost": float(b.unit_cost or 0),
            "qty_remaining": float(b.qty_remaining or 0),
            "received_date": str(b.received_date),
        })

    return {
        "needs_swap": True,
        "from_batch_id": from_batch.id,
        "from_supplier_name": purchase.supplier.name if purchase.supplier else "-",
        "from_unit_cost": float(from_batch.unit_cost or 0),
        "on_hand": round(on_hand, 4),
        "shortfall": round(shortfall, 4),
        "swappable_sold_qty": round(swappable, 4),
        "candidates": candidates,
    }


@router.post("/swap-batch")
def swap_batch(
    data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    """Pindahkan alokasi penjualan sebanyak `qty` (satuan dasar) dari from_batch (A)
    ke to_batch (B), lalu posting jurnal KOREKSI HPP bertanggal di periode penjualan
    asal (boleh sudah tutup buku). Tidak mengubah WarehouseStock — Σ batch == stok
    tetap terjaga. ADMIN-ONLY + audit."""
    raise HTTPException(410, "Swap batch sudah dinonaktifkan; retur memakai biaya FIFO stok tersedia")
    from .accounting import create_auto_journal, get_books_locked_until
    from ..services.journal_service import ACCOUNT_COGS, ACCOUNT_INVENTORY

    qty = float(data.get("qty") or 0)
    reason = data.get("reason")
    if qty <= 0:
        raise HTTPException(400, "qty harus > 0")

    A = db.query(models.StockBatch).with_for_update().get(data.get("from_batch_id"))
    B = db.query(models.StockBatch).with_for_update().get(data.get("to_batch_id"))
    if not A or not B:
        raise HTTPException(404, "Batch tidak ditemukan")
    if A.id == B.id:
        raise HTTPException(400, "Batch asal & tujuan tidak boleh sama")
    if A.item_id != B.item_id or A.warehouse_id != B.warehouse_id:
        raise HTTPException(400, "Batch harus item & gudang yang sama")
    if float(B.qty_remaining or 0) + EPS_SWAP < qty:
        raise HTTPException(400, f"Stok batch tujuan tidak cukup untuk swap (sisa {B.qty_remaining}).")

    allocs = (
        db.query(models.SaleItemBatch)
        .join(models.SaleItem, models.SaleItem.id == models.SaleItemBatch.sale_item_id)
        .join(models.Sale, models.Sale.id == models.SaleItem.sale_id)
        .filter(
            models.SaleItemBatch.batch_id == A.id,
            models.SaleItemBatch.qty > EPS_SWAP,
            models.Sale.status != "cancelled",
        )
        .order_by(models.Sale.date.desc(), models.SaleItem.id.desc())
        .all()
    )
    total_sold_A = sum(float(a.qty) for a in allocs)
    if total_sold_A + EPS_SWAP < qty:
        raise HTTPException(
            400,
            f"Hanya {round(total_sold_A, 4)} unit dari batch ini yang tercatat terjual; tak bisa swap {qty}.",
        )

    from_cost = float(A.unit_cost or 0)
    to_cost = float(B.unit_cost or 0)
    remaining = qty
    total_delta = 0.0
    affected = []

    for a in allocs:
        if remaining <= EPS_SWAP:
            break
        take = min(float(a.qty), remaining)
        si = a.sale_item
        sale = si.sale
        cost_delta = (to_cost - from_cost) * take

        # 1) pindahkan alokasi A -> B
        a.qty = float(a.qty) - take
        if a.qty <= EPS_SWAP:
            db.delete(a)
        b_alloc = (
            db.query(models.SaleItemBatch)
            .filter(
                models.SaleItemBatch.sale_item_id == si.id,
                models.SaleItemBatch.batch_id == B.id,
            )
            .first()
        )
        if b_alloc:
            b_alloc.qty = float(b_alloc.qty) + take
            b_alloc.unit_cost = to_cost
        else:
            db.add(models.SaleItemBatch(sale_item_id=si.id, batch_id=B.id, qty=take, unit_cost=to_cost))

        # 2) pindahkan lapisan fisik: A kembali on-hand, B dianggap terjual
        A.qty_remaining = float(A.qty_remaining) + take
        B.qty_remaining = float(B.qty_remaining) - take

        # 3) koreksi HPP baris jual (incremental → aman thd porsi fallback)
        if si.qty:
            si.buy_price = float(si.buy_price or 0) + (cost_delta / si.qty)

        # 4) jurnal koreksi bertanggal sale.date (boleh sudah tutup buku)
        jid = None
        if abs(cost_delta) > 0.005:
            if cost_delta > 0:  # HPP naik (B lebih mahal): Dr HPP / Cr Persediaan
                entries = [
                    {"code": ACCOUNT_COGS, "debit": cost_delta, "credit": 0},
                    {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": cost_delta},
                ]
            else:               # HPP turun (B lebih murah): Dr Persediaan / Cr HPP
                d = -cost_delta
                entries = [
                    {"code": ACCOUNT_INVENTORY, "debit": d, "credit": 0},
                    {"code": ACCOUNT_COGS, "debit": 0, "credit": d},
                ]
            j = create_auto_journal(
                db,
                date_val=sale.date,
                number_ref=f"SWAP-{sale.number}",
                description=(
                    f"Koreksi HPP swap batch (Sale {sale.number}): "
                    f"{from_cost:.0f} -> {to_cost:.0f} x {round(take, 4)}"
                ),
                entries=entries,
                user_id=current_user.id,
                branch_id=sale.branch_id,
                allow_closed_period=True,
            )
            jid = j.id

        locked_until = get_books_locked_until(db, sale.branch_id)
        was_locked = bool(locked_until and sale.date <= locked_until)

        db.add(models.Restatement(
            sale_item_id=si.id,
            from_batch_id=A.id,
            to_batch_id=B.id,
            qty=take,
            cost_delta=cost_delta,
            correction_journal_id=jid,
            period_was_locked=was_locked,
            sale_date=sale.date,
            reason=reason,
            created_by=current_user.id,
        ))

        affected.append({
            "sale_id": sale.id,
            "sale_number": sale.number,
            "sale_date": str(sale.date),
            "qty": round(take, 4),
            "cost_delta": round(cost_delta, 2),
            "journal_id": jid,
            "period_was_locked": was_locked,
        })
        total_delta += cost_delta
        remaining -= take

    write_audit(
        db, current_user.id, "SWAP_BATCH", "stock_batches", A.id,
        f"Swap {round(qty,4)} unit batch#{A.id}->#{B.id}; koreksi HPP {round(total_delta,2)}; "
        f"{len(affected)} penjualan; sebagian periode terkunci="
        f"{any(x['period_was_locked'] for x in affected)}",
    )
    db.commit()

    return {
        "success": True,
        "swapped_qty": round(qty, 4),
        "total_cost_delta": round(total_delta, 2),
        "freed_on_hand_from_batch": round(float(A.qty_remaining or 0), 4),
        "affected_sales": affected,
        "message": (
            f"Swap berhasil: {round(qty,4)} unit dipindah dari batch#{A.id} ke batch#{B.id}. "
            f"Stok supplier asal kini bisa diretur. Koreksi HPP total Rp{round(total_delta,2)} "
            f"diposting ke {len(affected)} periode penjualan."
        ),
    }
