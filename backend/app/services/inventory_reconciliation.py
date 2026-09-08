"""Analisa dan Perbaikan Saldo persediaan dengan pratinjau yang dapat diaudit."""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

import pytz
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import write_audit
from ..config import settings
from ..permissions import has_permission
from ..routes.accounting import create_auto_journal, get_account_balance, pastikan_akun_ada
from .inventory_documents import PUSAT_BRANCH_ID, branch_stock, suggested_inbound_cost
from .inventory_fifo import EPS, add_batch

WITA = pytz.timezone("Asia/Makassar")
TOL = 1e-4


def assert_access(db: Session, user: models.User, action: str) -> None:
    if not has_permission(db, user, "inventory.repair_balance", action):
        raise HTTPException(403, "Akses Proses Perbaikan Saldo ditolak")


def _warehouse(db: Session, warehouse_id: int, user: models.User) -> models.Warehouse:
    row = db.query(models.Warehouse).filter(
        models.Warehouse.id == warehouse_id,
        models.Warehouse.branch_id == user.active_branch_id,
        models.Warehouse.is_active == True,
    ).first()
    if not row:
        raise HTTPException(403, "Gudang tidak tersedia pada cabang aktif")
    return row


def _branch_fifo_value(db: Session, branch_id: int) -> float:
    return float(
        db.query(func.coalesce(func.sum(models.StockBatch.qty_remaining * models.StockBatch.unit_cost), 0.0))
        .join(models.Warehouse, models.Warehouse.id == models.StockBatch.warehouse_id)
        .filter(models.Warehouse.branch_id == branch_id)
        .scalar()
        or 0
    )


def _inventory_gl(db: Session, branch_id: int) -> float:
    account = db.query(models.Account).filter(models.Account.code == "1-1400").first()
    if not account:
        raise HTTPException(400, "Akun Persediaan 1-1400 belum tersedia")
    return float(get_account_balance(db, account.id, branch_id=branch_id))


def _item_fifo(db: Session, warehouse_id: int, item_id: int) -> tuple[float, float]:
    qty, value = db.query(
        func.coalesce(func.sum(models.StockBatch.qty_remaining), 0.0),
        func.coalesce(func.sum(models.StockBatch.qty_remaining * models.StockBatch.unit_cost), 0.0),
    ).filter(
        models.StockBatch.warehouse_id == warehouse_id,
        models.StockBatch.item_id == item_id,
    ).one()
    return float(qty or 0), float(value or 0)


def _token(db: Session, warehouse: models.Warehouse, gl: float) -> str:
    stock_rows = db.query(
        models.WarehouseStock.item_id, models.WarehouseStock.stock,
    ).filter(models.WarehouseStock.warehouse_id == warehouse.id).order_by(
        models.WarehouseStock.item_id
    ).all()
    batch_rows = db.query(
        models.StockBatch.id, models.StockBatch.item_id,
        models.StockBatch.qty_remaining, models.StockBatch.unit_cost,
        models.StockBatch.received_date,
    ).filter(models.StockBatch.warehouse_id == warehouse.id).order_by(models.StockBatch.id).all()
    item_ids = sorted({iid for iid, _ in stock_rows} | {iid for _, iid, _, _, _ in batch_rows})
    item_rows = db.query(models.Item.id, models.Item.stock).filter(models.Item.id.in_(item_ids)).order_by(
        models.Item.id
    ).all() if item_ids else []
    raw = repr((
        warehouse.id,
        [(i, round(float(q or 0), 6)) for i, q in stock_rows],
        [(bid, iid, round(float(q or 0), 6), round(float(c or 0), 6), str(received)) for bid, iid, q, c, received in batch_rows],
        [(i, round(float(q or 0), 6)) for i, q in item_rows],
        round(float(gl), 2),
    )).encode()
    return hmac.new(settings.SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()


def preview(db: Session, warehouse_id: int, user: models.User) -> dict:
    assert_access(db, user, "view")
    warehouse = _warehouse(db, warehouse_id, user)
    stock_ids = {iid for (iid,) in db.query(models.WarehouseStock.item_id).filter(
        models.WarehouseStock.warehouse_id == warehouse.id
    ).all()}
    batch_ids = {iid for (iid,) in db.query(models.StockBatch.item_id).filter(
        models.StockBatch.warehouse_id == warehouse.id
    ).distinct().all()}
    item_ids = sorted(stock_ids | batch_ids)
    items_by_id = {
        item.id: item for item in db.query(models.Item).filter(models.Item.id.in_(item_ids)).all()
    } if item_ids else {}
    show_cost = has_permission(db, user, "inventory.show_cost_in", "view") or has_permission(
        db, user, "inventory.show_cost_out", "view"
    )
    can_apply = has_permission(db, user, "inventory.repair_balance", "update")
    lines = []
    for item_id in item_ids:
        item = items_by_id.get(item_id)
        if not item:
            continue
        ws = db.query(models.WarehouseStock.stock).filter(
            models.WarehouseStock.warehouse_id == warehouse.id,
            models.WarehouseStock.item_id == item_id,
        ).scalar()
        warehouse_qty = float(ws or 0)
        fifo_qty, fifo_value = _item_fifo(db, warehouse.id, item_id)
        gap = warehouse_qty - fifo_qty
        item_target = branch_stock(db, PUSAT_BRANCH_ID, item_id) if warehouse.branch_id == PUSAT_BRANCH_ID else None
        item_before = float(item.stock or 0) if item_target is not None else None
        cache_gap = (item_target - item_before) if item_target is not None else 0.0
        if abs(gap) <= TOL and abs(cache_gap) <= TOL:
            continue
        suggested_cost, cost_source = suggested_inbound_cost(db, warehouse.id, item)
        lines.append({
            "item_id": item.id,
            "code": item.code,
            "name": item.name,
            "warehouse_qty": round(warehouse_qty, 4),
            "fifo_qty": round(fifo_qty, 4),
            "qty_gap": round(gap, 4),
            "fifo_value": round(fifo_value, 2) if show_cost else None,
            "suggested_cost": round(suggested_cost, 4) if show_cost else None,
            "cost_source": cost_source if show_cost else None,
            "item_stock": round(item_before, 4) if item_before is not None else None,
            "item_stock_target": round(item_target, 4) if item_target is not None else None,
        })
    gl = _inventory_gl(db, warehouse.branch_id)
    fifo_value = _branch_fifo_value(db, warehouse.branch_id)
    return {
        "warehouse_id": warehouse.id,
        "warehouse": warehouse.name,
        "branch_id": warehouse.branch_id,
        "items": lines,
        "item_count": len(lines),
        "gl_persediaan": round(gl, 2) if show_cost else None,
        "fifo_value": round(fifo_value, 2) if show_cost else None,
        "value_gap": round(gl - fifo_value, 2) if show_cost else None,
        "show_cost": show_cost,
        "can_apply": can_apply,
        "preview_token": _token(db, warehouse, gl),
        "balanced": not lines and abs(gl - fifo_value) < 1,
    }


def apply(db: Session, data: schemas.InventoryReconciliationApply, user: models.User) -> models.InventoryReconciliation:
    assert_access(db, user, "update")
    warehouse = _warehouse(db, data.warehouse_id, user)
    # Rekonsiliasi jarang dijalankan dan menyentuh valuasi satu cabang. Kunci seluruh
    # ledger persediaan cabang agar pratinjau tidak berlomba dengan jual/transfer/opname.
    branch_warehouse_ids = [wid for (wid,) in db.query(models.Warehouse.id).filter(
        models.Warehouse.branch_id == warehouse.branch_id
    ).all()]
    db.query(models.WarehouseStock).filter(
        models.WarehouseStock.warehouse_id.in_(branch_warehouse_ids)
    ).order_by(models.WarehouseStock.id).with_for_update().all()
    db.query(models.StockBatch).filter(
        models.StockBatch.warehouse_id.in_(branch_warehouse_ids)
    ).order_by(models.StockBatch.id).with_for_update().all()
    before = preview(db, warehouse.id, user)
    if not hmac.compare_digest(data.preview_token, before["preview_token"]):
        raise HTTPException(409, "Data stok berubah setelah pratinjau; jalankan analisa kembali")
    if before["balanced"]:
        raise HTTPException(400, "Saldo persediaan sudah seimbang")
    requested = {line.item_id: line for line in data.lines}
    if len(requested) != len(data.lines):
        raise HTTPException(400, "Barang rekonsiliasi tidak boleh duplikat")
    preview_ids = {line["item_id"] for line in before["items"]}
    if requested and set(requested) != preview_ids:
        raise HTTPException(409, "Daftar barang berbeda dari pratinjau; jalankan analisa kembali")

    now = datetime.now(WITA)
    gl_before_actual = _inventory_gl(db, warehouse.branch_id)
    fifo_before_actual = _branch_fifo_value(db, warehouse.branch_id)
    rec = models.InventoryReconciliation(
        number=f"REK-C{warehouse.branch_id}-{now.strftime('%Y%m%d%H%M%S%f')}",
        branch_id=warehouse.branch_id,
        warehouse_id=warehouse.id,
        reason=data.reason.strip(),
        gl_before=gl_before_actual,
        fifo_value_before=fifo_before_actual,
        gl_after=0,
        fifo_value_after=0,
        created_by=user.id,
        created_at=now,
    )
    db.add(rec)
    db.flush()

    for state in before["items"]:
        item = db.query(models.Item).filter(models.Item.id == state["item_id"]).with_for_update().first()
        warehouse_qty_actual = float(db.query(models.WarehouseStock.stock).filter(
            models.WarehouseStock.warehouse_id == warehouse.id,
            models.WarehouseStock.item_id == item.id,
        ).scalar() or 0)
        fifo_before_qty, fifo_before_value = _item_fifo(db, warehouse.id, item.id)
        gap = warehouse_qty_actual - fifo_before_qty
        selected = requested.get(item.id)
        suggested, cost_source = suggested_inbound_cost(db, warehouse.id, item)
        cost = float(selected.unit_cost) if selected and selected.unit_cost is not None else suggested
        if selected and selected.unit_cost is not None and abs(cost - suggested) > 0.005:
            cost_source = "input_admin"
        actions: list[tuple[models.StockBatch, float, float]] = []
        action_names = []
        if gap > TOL:
            if cost <= EPS and not (selected and selected.allow_zero_cost):
                raise HTTPException(400, f"Harga modal {item.name} nol dan belum dikonfirmasi")
            batch = add_batch(
                db, item_id=item.id, warehouse_id=warehouse.id, qty=gap,
                unit_cost=cost, received_date=now.date(),
            )
            actions.append((batch, gap, cost))
            action_names.append("add_batch")
        elif gap < -TOL:
            remaining = -gap
            batches = db.query(models.StockBatch).filter(
                models.StockBatch.warehouse_id == warehouse.id,
                models.StockBatch.item_id == item.id,
                models.StockBatch.qty_remaining > EPS,
            ).order_by(models.StockBatch.received_date.desc(), models.StockBatch.id.desc()).with_for_update().all()
            for batch in batches:
                take = min(float(batch.qty_remaining or 0), remaining)
                if take <= EPS:
                    continue
                batch.qty_remaining = float(batch.qty_remaining or 0) - take
                actions.append((batch, -take, float(batch.unit_cost or 0)))
                remaining -= take
                if remaining <= EPS:
                    break
            if remaining > TOL:
                raise HTTPException(409, f"Batch {item.name} berubah; jalankan analisa kembali")
            action_names.append("trim_newest_batch")

        item_before = float(item.stock or 0) if warehouse.branch_id == PUSAT_BRANCH_ID else None
        item_after = item_before
        if warehouse.branch_id == PUSAT_BRANCH_ID:
            item_after = branch_stock(db, PUSAT_BRANCH_ID, item.id)
            if abs(item_after - item_before) > TOL:
                item.stock = item_after
                action_names.append("sync_item_stock")
        db.flush()
        fifo_after_qty, fifo_after_value = _item_fifo(db, warehouse.id, item.id)
        line = models.InventoryReconciliationLine(
            reconciliation_id=rec.id,
            item_id=item.id,
            action="+".join(action_names) or "check",
            warehouse_qty=warehouse_qty_actual,
            fifo_qty_before=fifo_before_qty,
            fifo_qty_after=fifo_after_qty,
            fifo_value_before=fifo_before_value,
            fifo_value_after=fifo_after_value,
            unit_cost=cost,
            cost_source=cost_source if gap > TOL else "not_applicable",
            item_stock_before=item_before,
            item_stock_after=item_after,
        )
        db.add(line)
        db.flush()
        for batch, qty_delta, unit_cost in actions:
            db.add(models.InventoryReconciliationBatchAction(
                line_id=line.id, batch_id=batch.id, qty_delta=qty_delta, unit_cost=unit_cost,
            ))

    fifo_after = _branch_fifo_value(db, warehouse.branch_id)
    gl_before = gl_before_actual
    difference = round(gl_before - fifo_after, 2)
    if abs(difference) >= 1:
        pastikan_akun_ada(db, ["1-1400", "3-1999"])
        entries = ([
            {"code": "1-1400", "debit": 0, "credit": difference},
            {"code": "3-1999", "debit": difference, "credit": 0},
        ] if difference > 0 else [
            {"code": "1-1400", "debit": -difference, "credit": 0},
            {"code": "3-1999", "debit": 0, "credit": -difference},
        ])
        rec.journal = create_auto_journal(
            db=db,
            date_val=now.date(),
            number_ref=rec.number,
            description=f"Perbaikan saldo persediaan - {warehouse.name}: {rec.reason}",
            entries=entries,
            user_id=user.id,
            branch_id=warehouse.branch_id,
        )
    rec.fifo_value_after = fifo_after
    rec.gl_after = fifo_after
    write_audit(db, user.id, "RECONCILE", "inventory_reconciliations", rec.id, rec.number)
    db.flush()
    return rec


def serialize(rec: models.InventoryReconciliation) -> dict:
    return {
        "id": rec.id,
        "number": rec.number,
        "warehouse_id": rec.warehouse_id,
        "warehouse": rec.warehouse.name if rec.warehouse else None,
        "reason": rec.reason,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "journal_number": rec.journal.number if rec.journal else None,
        "gl_before": rec.gl_before,
        "fifo_value_before": rec.fifo_value_before,
        "gl_after": rec.gl_after,
        "fifo_value_after": rec.fifo_value_after,
        "lines": [{
            "item_id": line.item_id,
            "code": line.item.code if line.item else None,
            "name": line.item.name if line.item else None,
            "action": line.action,
            "warehouse_qty": line.warehouse_qty,
            "fifo_qty_before": line.fifo_qty_before,
            "fifo_qty_after": line.fifo_qty_after,
            "fifo_value_before": line.fifo_value_before,
            "fifo_value_after": line.fifo_value_after,
            "unit_cost": line.unit_cost,
            "cost_source": line.cost_source,
            "item_stock_before": line.item_stock_before,
            "item_stock_after": line.item_stock_after,
        } for line in rec.lines],
    }
