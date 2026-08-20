"""Dokumen Item Masuk/Keluar, Saldo Awal, dan Stok Opname yang atomik."""
from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime
from typing import Iterable

import pytz
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import write_audit
from ..config import settings
from ..permissions import has_permission
from ..routes.accounting import (
    assert_books_open,
    create_auto_journal,
    next_journal_number,
    pastikan_akun_ada,
)
from .inventory_fifo import EPS, add_batch, consume_fifo

WITA = pytz.timezone("Asia/Makassar")
TYPE_PREFIX = {
    "item_in": "IM",
    "item_out": "IK",
    "opening_stock": "SA",
    "stock_opname": "OPN",
}
DEFAULT_ACCOUNT = {
    "item_in": ("4-1300", None),
    "item_out": (None, "5-2700"),
    "opening_stock": ("3-1999", None),
    "stock_opname": ("4-1300", "5-1200"),
}
TYPE_PERMISSION = {
    "item_in": "inventory.item_in",
    "item_out": "inventory.item_out",
    "opening_stock": "inventory.opening_stock",
    "stock_opname": "inventory.stock_opname",
}


def local_date() -> date:
    return datetime.now(WITA).date()


def local_datetime() -> datetime:
    return datetime.now(WITA)


def snapshot_token(warehouse_id: int, item_id: int, qty: float) -> str:
    payload = f"{warehouse_id}:{item_id}:{float(qty):.6f}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def _warehouse_stock_row(db: Session, warehouse_id: int, item_id: int):
    return (
        db.query(models.WarehouseStock)
        .filter(
            models.WarehouseStock.warehouse_id == warehouse_id,
            models.WarehouseStock.item_id == item_id,
        )
        .with_for_update()
        .first()
    )


def warehouse_stock(db: Session, warehouse_id: int, item_id: int) -> float:
    row = _warehouse_stock_row(db, warehouse_id, item_id)
    return float(row.stock or 0) if row else 0.0


def branch_stock(db: Session, branch_id: int, item_id: int) -> float:
    return float(
        db.query(func.coalesce(func.sum(models.WarehouseStock.stock), 0.0))
        .join(models.Warehouse, models.Warehouse.id == models.WarehouseStock.warehouse_id)
        .filter(
            models.Warehouse.branch_id == branch_id,
            models.WarehouseStock.item_id == item_id,
        )
        .scalar()
        or 0.0
    )


def _change_stock(db: Session, warehouse_id: int, item: models.Item, delta: float) -> None:
    row = _warehouse_stock_row(db, warehouse_id, item.id)
    if not row:
        row = models.WarehouseStock(warehouse_id=warehouse_id, item_id=item.id, stock=0)
        db.add(row)
        db.flush()
    row.stock = float(row.stock or 0) + float(delta)
    item.stock = float(item.stock or 0) + float(delta)


def _next_number(db: Session, doc_type: str, branch_id: int, tx_date: date) -> str:
    stem = f"{TYPE_PREFIX[doc_type]}-C{branch_id}-{tx_date.strftime('%Y%m%d')}-"
    last = (
        db.query(models.InventoryDocument)
        .filter(models.InventoryDocument.number.like(f"{stem}%"))
        .order_by(models.InventoryDocument.id.desc())
        .with_for_update()
        .first()
    )
    seq = int(last.number.rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{stem}{seq:04d}"


def _account_by_code(db: Session, code: str) -> models.Account:
    account = db.query(models.Account).filter(models.Account.code == code).first()
    if not account:
        raise HTTPException(400, f"Akun default {code} belum tersedia")
    return account


def _resolve_account(
    db: Session,
    user: models.User,
    requested_id: int | None,
    default_code: str | None,
    allowed_types: Iterable[str],
) -> models.Account | None:
    if not default_code:
        return None
    default = _account_by_code(db, default_code)
    if requested_id is None or requested_id == default.id:
        return default
    if not has_permission(db, user, "inventory.account_override", "view"):
        raise HTTPException(403, "Anda tidak memiliki izin untuk mengganti akun jurnal")
    account = db.query(models.Account).filter(models.Account.id == requested_id).first()
    if not account or not account.is_active or account.type not in set(allowed_types):
        raise HTTPException(400, "Akun jurnal tidak sesuai dengan jenis transaksi")
    return account


def _assert_permission(db: Session, user: models.User, doc_type: str, action: str) -> None:
    key = TYPE_PERMISSION[doc_type]
    if not has_permission(db, user, key, action):
        raise HTTPException(403, f"Akses {action} untuk {doc_type} ditolak")


def _assert_date_allowed(db: Session, user: models.User, doc_type: str, tx_date: date) -> None:
    if tx_date != local_date() and not has_permission(db, user, TYPE_PERMISSION[doc_type], "lock_date"):
        raise HTTPException(403, "Tanggal transaksi dikunci; gunakan tanggal hari ini")
    assert_books_open(db, user.active_branch_id, tx_date, "Dokumen persediaan")


def _journal_entries(inventory_code: str, surplus, shortage, plus_value: float, minus_value: float):
    entries = []
    if plus_value > 0:
        entries.extend([
            {"code": inventory_code, "debit": plus_value, "credit": 0},
            {"code": surplus.code, "debit": 0, "credit": plus_value},
        ])
    if minus_value > 0:
        entries.extend([
            {"code": shortage.code, "debit": minus_value, "credit": 0},
            {"code": inventory_code, "debit": 0, "credit": minus_value},
        ])
    return entries


def create_document(
    db: Session, data: schemas.InventoryDocumentCreate, user: models.User
) -> models.InventoryDocument:
    _assert_permission(db, user, data.type, "create")
    if not user.active_branch_id:
        raise HTTPException(400, "Pilih cabang aktif terlebih dahulu")
    _assert_date_allowed(db, user, data.type, data.date)
    if not data.lines:
        raise HTTPException(400, "Dokumen harus memiliki minimal satu barang")
    item_ids = [line.item_id for line in data.lines]
    if len(item_ids) != len(set(item_ids)):
        raise HTTPException(400, "Barang yang sama tidak boleh dicatat dua kali")

    warehouse = db.query(models.Warehouse).filter(models.Warehouse.id == data.warehouse_id).first()
    if not warehouse or warehouse.branch_id != user.active_branch_id or not warehouse.is_active:
        raise HTTPException(403, "Gudang tidak tersedia pada cabang aktif")

    plus_code, minus_code = DEFAULT_ACCOUNT[data.type]
    surplus_types = {"revenue", "equity"} if data.type != "opening_stock" else {"equity"}
    surplus = _resolve_account(db, user, data.surplus_account_id, plus_code, surplus_types)
    shortage = _resolve_account(db, user, data.shortage_account_id, minus_code, {"expense"})
    inventory = _account_by_code(db, "1-1400")

    document = models.InventoryDocument(
        number=_next_number(db, data.type, warehouse.branch_id, data.date),
        type=data.type,
        date=data.date,
        branch_id=warehouse.branch_id,
        warehouse_id=warehouse.id,
        status="posted",
        notes=(data.notes or "").strip() or None,
        surplus_account_id=surplus.id if surplus else None,
        shortage_account_id=shortage.id if shortage else None,
        created_by=user.id,
        created_at=local_datetime(),
    )
    db.add(document)
    db.flush()

    plus_value = 0.0
    minus_value = 0.0
    for payload in data.lines:
        item = db.query(models.Item).filter(models.Item.id == payload.item_id).with_for_update().first()
        if not item or not item.is_active:
            raise HTTPException(404, f"Barang {payload.item_id} tidak ditemukan atau nonaktif")
        if item.is_virtual_variant or item.parent_item_id:
            raise HTTPException(400, f"{item.name} adalah barang multi-satuan; pilih barang induknya")

        current = warehouse_stock(db, warehouse.id, item.id)
        physical = None
        if data.type == "stock_opname":
            if payload.physical_qty is None or float(payload.physical_qty) < 0:
                raise HTTPException(400, f"Stok fisik {item.name} wajib diisi dan tidak boleh negatif")
            expected_token = snapshot_token(warehouse.id, item.id, current)
            if not payload.snapshot_token or not hmac.compare_digest(payload.snapshot_token, expected_token):
                raise HTTPException(409, f"Stok {item.name} berubah saat opname; muat ulang lalu hitung kembali")
            physical = float(payload.physical_qty)
            delta = physical - current
        else:
            if payload.qty is None or float(payload.qty) <= 0:
                raise HTTPException(400, f"Jumlah {item.name} harus lebih dari nol")
            qty = float(payload.qty)
            delta = -qty if data.type == "item_out" else qty
            if data.type == "opening_stock":
                existing_batch = db.query(models.StockBatch.id).filter(
                    models.StockBatch.warehouse_id == warehouse.id,
                    models.StockBatch.item_id == item.id,
                ).first()
                if abs(current) > EPS or existing_batch:
                    raise HTTPException(400, f"Saldo awal {item.name} sudah pernah diisi atau memiliki riwayat stok")

        if delta < -EPS and current + delta < -EPS:
            raise HTTPException(400, f"Stok {item.name} di {warehouse.name} tidak cukup")

        line = models.InventoryDocumentLine(
            document_id=document.id,
            item_id=item.id,
            system_qty=current,
            physical_qty=physical,
            qty_delta=delta,
            unit_cost=float(item.buy_price or 0),
            total_cost=0,
            notes=(payload.notes or "").strip() or None,
        )
        db.add(line)
        db.flush()

        value = 0.0
        if delta > EPS:
            value = delta * float(item.buy_price or 0)
            add_batch(
                db,
                item_id=item.id,
                warehouse_id=warehouse.id,
                qty=delta,
                unit_cost=float(item.buy_price or 0),
                received_date=data.date,
                source_inventory_line_id=line.id,
            )
            plus_value += value
        elif delta < -EPS:
            allocations = consume_fifo(
                db, item_id=item.id, warehouse_id=warehouse.id, qty=-delta
            )
            for batch, qty, cost in allocations:
                db.add(models.InventoryDocumentBatchAllocation(
                    line_id=line.id,
                    batch_id=batch.id if batch else None,
                    qty=qty,
                    unit_cost=cost,
                ))
                value += qty * cost
            minus_value += value
        line.unit_cost = value / abs(delta) if abs(delta) > EPS else float(item.buy_price or 0)
        line.total_cost = value

        if abs(delta) > EPS:
            before_branch = branch_stock(db, warehouse.branch_id, item.id)
            _change_stock(db, warehouse.id, item, delta)
            db.add(models.StockMovement(
                date=data.date,
                created_at=local_datetime(),
                item_id=item.id,
                branch_id=warehouse.branch_id,
                type={
                    "item_in": "item_in",
                    "item_out": "item_out",
                    "opening_stock": "opening_stock",
                    "stock_opname": "opname_in" if delta > 0 else "opname_out",
                }[data.type],
                qty=abs(delta),
                qty_before=before_branch,
                qty_after=before_branch + delta,
                reference=document.number,
                notes=line.notes or document.notes,
            ))

    entries = _journal_entries(inventory.code, surplus, shortage, plus_value, minus_value)
    if entries:
        pastikan_akun_ada(db, [entry["code"] for entry in entries])
        document.journal = create_auto_journal(
            db=db,
            date_val=data.date,
            number_ref=document.number,
            description=f"{TYPE_PREFIX[data.type]} {document.number} - {document.notes or 'Persediaan'}",
            entries=entries,
            user_id=user.id,
            branch_id=warehouse.branch_id,
        )

    write_audit(db, user.id, "CREATE", "inventory_documents", document.id, document.number)
    db.flush()
    return document


def _reverse_journal(db: Session, original: models.Journal, document, user: models.User):
    reversal_date = local_date()
    assert_books_open(db, document.branch_id, reversal_date, "Pembatalan dokumen persediaan")
    journal = models.Journal(
        number=next_journal_number(db, document.branch_id),
        date=reversal_date,
        description=f"REVERSAL {document.number}: {document.cancellation_reason}",
        reference=document.number,
        source="auto",
        created_by=user.id,
        branch_id=document.branch_id,
    )
    db.add(journal)
    db.flush()
    for line in original.lines:
        db.add(models.JournalEntryLine(
            journal_id=journal.id,
            debit_account_id=line.credit_account_id,
            credit_account_id=line.debit_account_id,
            amount=line.amount,
            description=journal.description,
        ))
    return journal


def cancel_document(db: Session, document: models.InventoryDocument, reason: str, user: models.User):
    _assert_permission(db, user, document.type, "delete")
    if document.status != "posted":
        raise HTTPException(400, "Dokumen sudah dibatalkan")
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(400, "Alasan pembatalan wajib diisi")

    assert_books_open(db, document.branch_id, local_date(), "Pembatalan dokumen persediaan")

    for line in document.lines:
        item = db.query(models.Item).filter(models.Item.id == line.item_id).with_for_update().first()
        delta = float(line.qty_delta or 0)
        current = warehouse_stock(db, document.warehouse_id, line.item_id)
        if delta > EPS:
            source_batches = (
                db.query(models.StockBatch)
                .filter(
                    models.StockBatch.source_inventory_line_id == line.id,
                    models.StockBatch.warehouse_id == document.warehouse_id,
                    models.StockBatch.qty_remaining > EPS,
                )
                .with_for_update()
                .all()
            )
            if sum(float(batch.qty_remaining) for batch in source_batches) + EPS < delta:
                raise HTTPException(
                    409,
                    f"{item.name} dari dokumen ini sudah dipakai atau dipindahkan; buat Item Keluar/Opname koreksi",
                )
            remaining = delta
            for batch in source_batches:
                take = min(float(batch.qty_remaining), remaining)
                batch.qty_remaining -= take
                remaining -= take
                if remaining <= EPS:
                    break
        elif delta < -EPS:
            for allocation in line.allocations:
                if allocation.batch_id:
                    batch = db.get(models.StockBatch, allocation.batch_id, with_for_update=True)
                    if batch:
                        batch.qty_remaining = float(batch.qty_remaining or 0) + float(allocation.qty)
                        continue
                add_batch(
                    db,
                    item_id=line.item_id,
                    warehouse_id=document.warehouse_id,
                    qty=allocation.qty,
                    unit_cost=allocation.unit_cost,
                    received_date=document.date,
                )

        inverse = -delta
        if abs(inverse) > EPS:
            if inverse < 0 and current + inverse < -EPS:
                raise HTTPException(409, f"Stok {item.name} tidak cukup untuk membatalkan dokumen")
            before_branch = branch_stock(db, document.branch_id, line.item_id)
            _change_stock(db, document.warehouse_id, item, inverse)
            db.add(models.StockMovement(
                date=local_date(),
                created_at=local_datetime(),
                item_id=line.item_id,
                branch_id=document.branch_id,
                type="reversal_in" if inverse > 0 else "reversal_out",
                qty=abs(inverse),
                qty_before=before_branch,
                qty_after=before_branch + inverse,
                reference=f"REV-{document.number}",
                notes=reason,
            ))

    document.status = "cancelled"
    document.cancelled_by = user.id
    document.cancelled_at = local_datetime()
    document.cancellation_reason = reason
    if document.journal:
        document.reversal_journal = _reverse_journal(db, document.journal, document, user)
    write_audit(db, user.id, "CANCEL", "inventory_documents", document.id, f"{document.number}: {reason}")
    db.flush()
    return document


def can_see_cost(db: Session, user: models.User, doc_type: str) -> bool:
    if doc_type == "stock_opname":
        return has_permission(db, user, "inventory.show_cost_in", "view") or has_permission(
            db, user, "inventory.show_cost_out", "view"
        )
    key = "inventory.show_cost_in" if doc_type in {"item_in", "opening_stock"} else "inventory.show_cost_out"
    return has_permission(db, user, key, "view")


def serialize_document(db: Session, document: models.InventoryDocument, user: models.User, detail=False):
    show_cost = can_see_cost(db, user, document.type)
    show_stock = document.type != "stock_opname" or has_permission(
        db, user, "inventory.opname_show_stock", "view"
    )
    result = {
        "id": document.id,
        "number": document.number,
        "type": document.type,
        "date": str(document.date),
        "branch_id": document.branch_id,
        "warehouse_id": document.warehouse_id,
        "warehouse_name": document.warehouse.name if document.warehouse else "-",
        "status": document.status,
        "notes": document.notes,
        "created_by": document.creator.full_name if document.creator else "-",
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "cancelled_at": document.cancelled_at.isoformat() if document.cancelled_at else None,
        "cancellation_reason": document.cancellation_reason,
        "journal_id": document.journal_id,
        "reversal_journal_id": document.reversal_journal_id,
        "line_count": len(document.lines),
        "total_value": sum(float(line.total_cost or 0) for line in document.lines) if show_cost else None,
        "can_cancel": document.status == "posted" and has_permission(db, user, TYPE_PERMISSION[document.type], "delete"),
    }
    if detail:
        result["lines"] = [
            {
                "id": line.id,
                "item_id": line.item_id,
                "item_code": line.item.code if line.item else "-",
                "item_name": line.item.name if line.item else "Item dihapus",
                "system_qty": line.system_qty if show_stock else None,
                "physical_qty": line.physical_qty,
                "qty_delta": line.qty_delta,
                "unit_cost": line.unit_cost if show_cost else None,
                "total_cost": line.total_cost if show_cost else None,
                "notes": line.notes,
            }
            for line in document.lines
        ]
    return result
