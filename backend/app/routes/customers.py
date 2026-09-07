from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date, datetime
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, require_admin

router = APIRouter()


def _ensure_not_system_level_one(name: str) -> None:
    """Level 1 direpresentasikan oleh customer.group_id NULL, bukan baris grup."""
    normalized = "".join((name or "").casefold().split())
    if normalized == "level1":
        raise HTTPException(
            400,
            "Level 1 adalah harga umum bawaan dengan diskon 0% dan tidak perlu dibuat sebagai grup.",
        )


# ─── Customer Groups ──────────────────────────────────────────────────────────
@router.get("/groups", response_model=list[schemas.CustomerGroupOut])
def get_groups(db: Session = Depends(get_db), _=Depends(get_current_user)):
    from sqlalchemy.orm import subqueryload
    return db.query(models.CustomerGroup).options(subqueryload(models.CustomerGroup.customers)).all()

@router.post("/groups", response_model=schemas.CustomerGroupOut)
def create_group(g: schemas.CustomerGroupCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _ensure_not_system_level_one(g.name)
    obj = models.CustomerGroup(**g.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.put("/groups/{gid}", response_model=schemas.CustomerGroupOut)
def update_group(gid: int, g: schemas.CustomerGroupCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _ensure_not_system_level_one(g.name)
    obj = db.query(models.CustomerGroup).get(gid)
    if not obj: raise HTTPException(404, "Grup tidak ditemukan")
    for k, v in g.model_dump().items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/groups/{gid}")
def delete_group(gid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.CustomerGroup).get(gid)
    if not obj: raise HTTPException(404, "Grup tidak ditemukan")
    db.delete(obj); db.commit()
    return {"message": "Grup dihapus"}


# ─── Customers ────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[schemas.CustomerOut])
def get_customers(search: Optional[str] = None, active_only: bool = True,
                  sort: Optional[str] = None,
                  group_id: Optional[int] = None,
                  without_group: bool = False,
                  skip: int = 0, limit: int = 100,
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    from sqlalchemy.orm import subqueryload, joinedload
    q = db.query(models.Customer).options(
        subqueryload(models.Customer.sales),
        joinedload(models.Customer.group)
    )
    if active_only: q = q.filter(models.Customer.is_active == True)
    if search: q = q.filter(
        models.Customer.name.ilike(f"%{search}%") |
        models.Customer.code.ilike(f"%{search}%") |
        models.Customer.phone.ilike(f"%{search}%")
    )
    if group_id is not None:
        q = q.filter(models.Customer.group_id == group_id)
    elif without_group:
        q = q.filter(models.Customer.group_id.is_(None))
    if sort == "deposit_desc":
        q = q.order_by(
            models.Customer.deposit_balance.desc(),
            models.Customer.name.asc(),
            models.Customer.id.asc(),
        )
    elif sort == "deposit_asc":
        q = q.order_by(
            models.Customer.deposit_balance.asc(),
            models.Customer.name.asc(),
            models.Customer.id.asc(),
        )
    else:
        q = q.order_by(models.Customer.name.asc(), models.Customer.id.asc())
    return q.offset(skip).limit(limit).all()


def _iso(value):
    return value.isoformat() if value else None


def _branch_payload(branch):
    return {"id": branch.id, "name": branch.name} if branch else None


@router.get("/{cid}/history")
def get_customer_history(
    cid: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    event_type: Optional[str] = None,
    branch_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    customer = db.query(models.Customer).get(cid)
    if not customer:
        raise HTTPException(404, "Pelanggan tidak ditemukan")
    limit = min(max(limit, 1), 300)

    movements = (
        db.query(models.CustomerDepositMovement)
        .filter(models.CustomerDepositMovement.customer_id == cid)
        .order_by(models.CustomerDepositMovement.occurred_at.asc(), models.CustomerDepositMovement.id.asc())
        .all()
    )
    source_movements = {}
    for movement in movements:
        source_movements.setdefault((movement.source_type, movement.source_id), []).append(movement)

    def balance_at(moment):
        result = 0.0
        moment_key = str(moment or "")
        for movement in movements:
            if str(movement.occurred_at or "") <= moment_key:
                result = float(movement.balance_after or 0)
            else:
                break
        return result

    events = []
    sales = db.query(models.Sale).filter(models.Sale.customer_id == cid).all()
    for sale in sales:
        linked = [m for m in source_movements.get(("sale", sale.id), []) if m.event_type == "purchase"]
        events.append({
            "event_key": f"sale:{sale.id}", "event_type": "purchase", "label": "Pembelian",
            "source_type": "sale", "source_id": sale.id, "number": sale.number,
            "transaction_date": _iso(sale.date), "occurred_at": _iso(sale.created_at),
            "branch": _branch_payload(sale.branch), "transaction_total": float(sale.total or 0),
            "deposit_change": sum(float(m.amount or 0) for m in linked),
            "balance_after": linked[-1].balance_after if linked else balance_at(sale.created_at),
            "status": sale.status,
        })

    returns = (
        db.query(models.SaleReturn).join(models.Sale)
        .filter(models.Sale.customer_id == cid).all()
    )
    for return_doc in returns:
        linked = source_movements.get(("sale_return", return_doc.id), [])
        events.append({
            "event_key": f"sale_return:{return_doc.id}", "event_type": "sale_return", "label": "Retur",
            "source_type": "sale_return", "source_id": return_doc.id, "number": return_doc.number,
            "transaction_date": _iso(return_doc.date), "occurred_at": _iso(return_doc.created_at),
            "branch": _branch_payload(return_doc.branch or return_doc.sale.branch),
            "transaction_total": float(return_doc.total or 0),
            "deposit_change": sum(float(m.amount or 0) for m in linked),
            "balance_after": linked[-1].balance_after if linked else balance_at(return_doc.created_at),
            "status": "completed",
        })

    trades = db.query(models.TradeIn).filter(models.TradeIn.customer_id == cid).all()
    for trade in trades:
        linked = [m for m in source_movements.get(("trade_in", trade.id), []) if m.event_type == "trade_in"]
        events.append({
            "event_key": f"trade_in:{trade.id}", "event_type": "trade_in", "label": "Tukar Tambah",
            "source_type": "trade_in", "source_id": trade.id, "number": trade.number,
            "transaction_date": _iso(trade.date), "occurred_at": _iso(trade.created_at),
            "branch": _branch_payload(db.query(models.Branch).get(trade.branch_id)),
            "transaction_total": float(trade.new_subtotal or 0),
            "deposit_change": sum(float(m.amount or 0) for m in linked),
            "balance_after": linked[-1].balance_after if linked else balance_at(trade.created_at),
            "status": getattr(trade, "status", "completed") or "completed",
        })

    document_keys = {("sale", sale.id) for sale in sales}
    document_keys.update(("sale_return", row.id) for row in returns)
    document_keys.update(("trade_in", row.id) for row in trades)
    for movement in movements:
        is_document_primary = (
            (movement.source_type, movement.source_id) in document_keys
            and movement.event_type in {"purchase", "sale_return", "trade_in"}
        )
        if is_document_primary:
            continue
        events.append({
            "event_key": f"movement:{movement.id}", "event_type": movement.event_type,
            "label": {
                "transfer_in": "Transfer Masuk", "transfer_out": "Transfer Keluar",
                "write_off": "Penghangusan", "purchase_cancel": "Pembatalan Pembelian",
                "trade_in_cancel": "Pembatalan Tukar Tambah",
                "opening_adjustment": "Saldo Awal/Penyesuaian",
            }.get(movement.event_type, "Mutasi Saldo"),
            "source_type": "movement", "source_id": movement.id,
            "number": movement.reference or "-", "transaction_date": _iso(movement.transaction_date),
            "occurred_at": _iso(movement.occurred_at), "branch": _branch_payload(movement.branch),
            "transaction_total": abs(float(movement.amount or 0)),
            "deposit_change": float(movement.amount or 0), "balance_after": float(movement.balance_after or 0),
            "status": "completed",
        })

    available_branches = sorted(
        {row["branch"]["id"]: row["branch"] for row in events if row["branch"]}.values(),
        key=lambda branch: branch["name"],
    )
    if start_date:
        events = [row for row in events if row["transaction_date"] and row["transaction_date"] >= start_date.isoformat()]
    if end_date:
        events = [row for row in events if row["transaction_date"] and row["transaction_date"] <= end_date.isoformat()]
    if event_type:
        if event_type == "balance":
            document_types = {"purchase", "sale_return", "trade_in"}
            events = [row for row in events if row["event_type"] not in document_types]
        else:
            events = [row for row in events if row["event_type"] == event_type]
    if branch_id is not None:
        events = [row for row in events if row["branch"] and row["branch"]["id"] == branch_id]

    events.sort(key=lambda row: (row["occurred_at"] or row["transaction_date"] or "", row["event_key"]), reverse=True)
    total = len(events)
    return {
        "customer": {
            "id": customer.id, "code": customer.code, "name": customer.name,
            "phone": customer.phone, "group": customer.group.name if customer.group else None,
            "deposit_balance": float(customer.deposit_balance or 0),
        },
        "items": events[skip:skip + limit], "total": total, "skip": skip, "limit": limit,
        "branches": available_branches,
    }


@router.get("/{cid}/history/{source_type}/{source_id}")
def get_customer_history_detail(
    cid: int, source_type: str, source_id: int,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    if source_type == "sale":
        doc = db.query(models.Sale).filter(models.Sale.id == source_id, models.Sale.customer_id == cid).first()
        if not doc: raise HTTPException(404, "Pembelian tidak ditemukan")
        return {
            "type": "purchase", "number": doc.number, "notes": doc.notes,
            "items": [{"code": row.item.code, "name": row.item.name, "qty": row.qty,
                       "price": row.sell_price, "discount": row.discount, "total": row.total} for row in doc.items],
            "payments": [{"method": row.method, "amount": row.amount} for row in doc.payments]
                or [{"method": doc.payment_method, "amount": doc.paid}],
        }
    if source_type == "sale_return":
        doc = db.query(models.SaleReturn).join(models.Sale).filter(
            models.SaleReturn.id == source_id, models.Sale.customer_id == cid).first()
        if not doc: raise HTTPException(404, "Retur tidak ditemukan")
        return {
            "type": "sale_return", "number": doc.number, "notes": doc.notes or doc.reason,
            "items": [{"code": row.item.code, "name": row.item.name, "qty": row.qty,
                       "price": row.price, "condition": row.condition, "total": row.total} for row in doc.items],
            "payments": [{"method": "deposit", "amount": doc.total}],
        }
    if source_type == "trade_in":
        doc = db.query(models.TradeIn).filter(models.TradeIn.id == source_id, models.TradeIn.customer_id == cid).first()
        if not doc: raise HTTPException(404, "Tukar tambah tidak ditemukan")
        items = [
            {"code": row.item.code, "name": row.item.name, "qty": row.qty,
             "price": row.return_price, "kind": "Barang kembali", "total": row.total}
            for row in doc.return_items
        ] + [
            {"code": row.item.code, "name": row.item.name, "qty": row.qty,
             "price": row.sell_price, "kind": "Barang baru", "total": row.total}
            for row in doc.new_items
        ]
        payments = [{"method": name, "amount": amount} for name, amount in (
            ("cash", doc.cash_amount), ("bank", doc.bank_amount), ("deposit", doc.deposit_amount)
        ) if float(amount or 0) > 0.005]
        return {"type": "trade_in", "number": doc.number, "notes": doc.notes,
                "items": items, "payments": payments}
    if source_type == "movement":
        movement = db.query(models.CustomerDepositMovement).filter(
            models.CustomerDepositMovement.id == source_id,
            models.CustomerDepositMovement.customer_id == cid,
        ).first()
        if not movement: raise HTTPException(404, "Mutasi saldo tidak ditemukan")
        return {"type": movement.event_type, "number": movement.reference,
                "notes": movement.description, "items": [], "payments": []}
    raise HTTPException(400, "Jenis riwayat tidak valid")

@router.get("/{cid}", response_model=schemas.CustomerOut)
def get_customer(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Customer).get(cid)
    if not obj: raise HTTPException(404, "Pelanggan tidak ditemukan")
    return obj

@router.post("/", response_model=schemas.CustomerOut)
def create_customer(c: schemas.CustomerCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    import uuid
    cust_code = c.code.strip() if c.code else f"CUST-{uuid.uuid4().hex[:5].upper()}"
    
    if db.query(models.Customer).filter(models.Customer.code == cust_code).first():
        raise HTTPException(400, "Kode pelanggan sudah digunakan")
    
    data = c.model_dump()
    data["code"] = cust_code
    obj = models.Customer(**data)
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.put("/{cid}", response_model=schemas.CustomerOut)
def update_customer(cid: int, c: schemas.CustomerUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Customer).get(cid)
    if not obj: raise HTTPException(404, "Pelanggan tidak ditemukan")
    for k, v in c.model_dump(exclude_unset=True).items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/{cid}")
def delete_customer(cid: int, force: bool = False, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Customer).filter(models.Customer.id == cid).with_for_update().first()
    if not obj: raise HTTPException(404, "Pelanggan tidak ditemukan")
    
    # Validasi Saldo sebelum hapus
    if obj.deposit_balance > 0:
        if not force:
            raise HTTPException(
                status_code=400, 
                detail={
                    "error_code": "HAS_BALANCE", 
                    "balance": obj.deposit_balance,
                    "message": f"Pelanggan ini memiliki saldo Rp {obj.deposit_balance:,.0f}. Saldo harus dipindahkan atau dihanguskan sebelum menghapus."
                }
            )
        else:
            # Hanguskan saldo (Jurnal ke Pendapatan Lain-lain)
            from ..services import journal_service
            journal_service.create_customer_balance_write_off_journal(
                db, 
                date_val=datetime.now().date(),
                amount=obj.deposit_balance,
                customer_name=obj.name,
                user_id=current_user.id,
                branch_id=current_user.active_branch_id or 1
            )
            from ..services.customer_deposits import apply_customer_deposit_change
            apply_customer_deposit_change(
                db, obj, -float(obj.deposit_balance or 0), event_type="write_off",
                transaction_date=datetime.now().date(), branch_id=current_user.active_branch_id or 1,
                source_type="customer", source_id=obj.id, reference=f"WO-{obj.id}",
                description="Saldo dihanguskan saat pelanggan dinonaktifkan", created_by=current_user.id,
            )

    obj.is_active = False
    db.commit()
    return {"message": "Pelanggan dinonaktifkan"}


@router.post("/{cid}/transfer-balance")
def transfer_customer_balance(
    cid: int,
    data: schemas.CustomerTransferBalance,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin)
):
    locked = db.query(models.Customer).filter(
        models.Customer.id.in_(sorted({cid, data.target_customer_id}))
    ).order_by(models.Customer.id.asc()).with_for_update().all()
    locked_by_id = {customer.id: customer for customer in locked}
    source = locked_by_id.get(cid)
    if not source: raise HTTPException(404, "Pelanggan asal tidak ditemukan")
    target = locked_by_id.get(data.target_customer_id)
    if not target: raise HTTPException(404, "Pelanggan tujuan tidak ditemukan")
    
    if source.id == target.id:
        raise HTTPException(400, "Tidak bisa transfer ke diri sendiri")
        
    if data.amount <= 0:
        raise HTTPException(400, "Jumlah transfer harus lebih dari 0")
        
    if source.deposit_balance < data.amount:
        raise HTTPException(400, f"Saldo tidak mencukupi (Tersedia: {source.deposit_balance:,.0f})")
        
    # Proses Transfer
    from ..services.customer_deposits import apply_customer_deposit_change
    transfer_ref = f"TRF-{datetime.now().strftime('%Y%m%d%H%M%S')}-{source.id}-{target.id}"
    branch_id = current_user.active_branch_id or 1
    apply_customer_deposit_change(
        db, source, -data.amount, event_type="transfer_out", transaction_date=datetime.now().date(),
        branch_id=branch_id, source_type="customer_transfer", source_id=target.id,
        reference=transfer_ref, description=f"Transfer saldo ke {target.name}", created_by=current_user.id,
    )
    apply_customer_deposit_change(
        db, target, data.amount, event_type="transfer_in", transaction_date=datetime.now().date(),
        branch_id=branch_id, source_type="customer_transfer", source_id=source.id,
        reference=transfer_ref, description=f"Transfer saldo dari {source.name}", created_by=current_user.id,
    )
    
    # Catat Jurnal
    from ..services import journal_service
    journal_service.create_customer_balance_transfer_journal(
        db,
        date_val=datetime.now().date(),
        amount=data.amount,
        source_name=source.name,
        target_name=target.name,
        user_id=current_user.id,
        branch_id=branch_id
    )
    
    db.commit()
    return {"message": f"Saldo Rp {data.amount:,.0f} berhasil ditransfer ke {target.name}"}


@router.get("/{cid}/sold-items")
def get_customer_sold_items(
    cid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Mengambil daftar unik item yang PERNAH dibeli oleh customer ini.
    Digunakan untuk validasi retur manual agar user tidak meretur barang yang tidak pernah dibeli.
    """
    items = db.query(models.Item).join(models.SaleItem).join(models.Sale).filter(
        models.Sale.customer_id == cid,
        models.Sale.status != "cancelled"
    ).distinct().all()

    return [
        {"id": i.id, "code": i.code, "name": i.name, "barcode": i.barcode}
        for i in items
    ]


@router.get("/{cid}/items/{iid}/history")
def get_customer_item_sale_history(
    cid: int,
    iid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Mengambil riwayat harga jual item tertentu kepada customer tertentu.
    Membantu user menentukan harga retur berdasarkan faktur lama.
    """
    history = db.query(
        models.Sale.id,
        models.Sale.number,
        models.Sale.date,
        models.SaleItem.qty,
        models.SaleItem.sell_price,
        models.Sale.tax_percent
    ).join(models.SaleItem, models.Sale.id == models.SaleItem.sale_id).filter(
        models.Sale.customer_id == cid,
        models.SaleItem.item_id == iid,
        models.Sale.status != "cancelled"
    ).order_by(models.Sale.date.desc()).all()

    results = []
    from sqlalchemy import func
    for h in history:
        returned_qty = db.query(func.sum(models.SaleReturnItem.qty)).join(models.SaleReturn).filter(
            models.SaleReturn.sale_id == h.id,
            models.SaleReturnItem.item_id == iid
        ).scalar() or 0.0
        
        results.append({
            "purchase_id": h.id, # Mapping to same key as purchase for frontend consistency
            "number": h.number,
            "date": h.date.isoformat() if h.date else None,
            "qty": h.qty,
            "qty_available": h.qty - returned_qty,
            "price": h.sell_price,
            "tax_percent": h.tax_percent
        })
    
    return results
