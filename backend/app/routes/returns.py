from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from ..database import get_db
from .. import models
from ..auth import get_current_user, write_audit

router = APIRouter()


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
                     db: Session = Depends(get_db), _=Depends(get_current_user)):
    returns = db.query(models.SaleReturn).order_by(models.SaleReturn.id.desc()).offset(skip).limit(limit).all()
    result = []
    for r in returns:
        sale = db.query(models.Sale).get(r.sale_id)
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
            "sale_id": r.sale_id,
            "sale_number": sale.number if sale else "-",
            "total": r.total, "reason": r.reason, "notes": r.notes,
            "items": items_out,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    return result


@router.post("/sales")
def create_sale_return(data: dict, db: Session = Depends(get_db),
                       current_user: models.User = Depends(get_current_user)):
    sale_id = data.get("sale_id")
    sale = db.query(models.Sale).get(sale_id)
    if not sale: raise HTTPException(404, "Penjualan tidak ditemukan")

    number = _next_number(db, "RS", models.SaleReturn)
    total = 0.0

    retur = models.SaleReturn(
        number=number,
        date=data.get("date", str(date.today())),
        sale_id=sale_id,
        reason=data.get("reason"),
        notes=data.get("notes")
    )
    db.add(retur); db.flush()

    for it in data.get("items", []):
        item = db.query(models.Item).get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")

        # Validasi: qty retur tidak boleh lebih dari qty jual
        sale_item = next((si for si in sale.items if si.item_id == it["item_id"]), None)
        if not sale_item:
            raise HTTPException(400, f"Item {item.name} tidak ada di faktur ini")

        # Cek total retur sebelumnya untuk item ini
        prev_returned = db.query(
            models.SaleReturnItem
        ).join(models.SaleReturn).filter(
            models.SaleReturn.sale_id == sale_id,
            models.SaleReturnItem.item_id == it["item_id"]
        ).all()
        total_prev = sum(p.qty for p in prev_returned)

        if total_prev + it["qty"] > sale_item.qty:
            raise HTTPException(400, f"Qty retur {item.name} melebihi qty terjual ({sale_item.qty - total_prev} tersisa)")

        line_total = it["qty"] * sale_item.sell_price
        total += line_total

        db.add(models.SaleReturnItem(
            return_id=retur.id, item_id=it["item_id"],
            qty=it["qty"], price=sale_item.sell_price, total=line_total
        ))

        # Kembalikan stok
        before = item.stock
        item.stock += it["qty"]
        db.add(models.StockMovement(
            date=data.get("date", str(date.today())),
            item_id=item.id, type="in", qty=it["qty"],
            qty_before=before, qty_after=item.stock,
            reference=number, notes=f"Retur Penjualan {sale.number}"
        ))

    retur.total = total
    db.commit(); db.refresh(retur)

    write_audit(db, current_user.id, "CREATE", "sale_returns", retur.id,
                f"Retur penjualan {sale.number} sebesar {total}")
    db.commit()
    return {"id": retur.id, "number": retur.number, "total": total, "message": "Retur penjualan berhasil"}


# ══════════════════════════════════════════════════════════════════════════════
# RETUR PEMBELIAN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/purchases")
def get_purchase_returns(skip: int = 0, limit: int = 100,
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    returns = db.query(models.PurchaseReturn).order_by(models.PurchaseReturn.id.desc()).offset(skip).limit(limit).all()
    result = []
    for r in returns:
        purchase = db.query(models.Purchase).get(r.purchase_id)
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
            "purchase_number": purchase.number if purchase else "-",
            "supplier_name": purchase.supplier.name if purchase and purchase.supplier else "-",
            "total": r.total, "reason": r.reason, "notes": r.notes,
            "items": items_out
        })
    return result


@router.post("/purchases")
def create_purchase_return(data: dict, db: Session = Depends(get_db),
                            current_user: models.User = Depends(get_current_user)):
    purchase_id = data.get("purchase_id")
    purchase = db.query(models.Purchase).get(purchase_id)
    if not purchase: raise HTTPException(404, "Pembelian tidak ditemukan")

    number = _next_number(db, "RP", models.PurchaseReturn)
    total = 0.0

    retur = models.PurchaseReturn(
        number=number,
        date=data.get("date", str(date.today())),
        purchase_id=purchase_id,
        reason=data.get("reason"),
        notes=data.get("notes")
    )
    db.add(retur); db.flush()

    for it in data.get("items", []):
        item = db.query(models.Item).get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")

        pur_item = next((pi for pi in purchase.items if pi.item_id == it["item_id"]), None)
        if not pur_item:
            raise HTTPException(400, f"Item {item.name} tidak ada di pembelian ini")

        line_total = it["qty"] * pur_item.buy_price
        total += line_total

        db.add(models.PurchaseReturnItem(
            return_id=retur.id, item_id=it["item_id"],
            qty=it["qty"], price=pur_item.buy_price, total=line_total
        ))

        # Kurangi stok
        before = item.stock
        item.stock -= it["qty"]
        db.add(models.StockMovement(
            date=data.get("date", str(date.today())),
            item_id=item.id, type="out", qty=it["qty"],
            qty_before=before, qty_after=item.stock,
            reference=number, notes=f"Retur Pembelian {purchase.number}"
        ))

    retur.total = total
    db.commit(); db.refresh(retur)
    write_audit(db, current_user.id, "CREATE", "purchase_returns", retur.id, f"Retur {purchase.number}")
    db.commit()
    return {"id": retur.id, "number": retur.number, "total": total, "message": "Retur pembelian berhasil"}
