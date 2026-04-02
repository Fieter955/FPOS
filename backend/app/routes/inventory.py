from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()


@router.get("/movements")
def get_movements(
    item_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(models.StockMovement)
    if item_id: q = q.filter(models.StockMovement.item_id == item_id)
    if start_date: q = q.filter(models.StockMovement.date >= start_date)
    if end_date: q = q.filter(models.StockMovement.date <= end_date)
    movements = q.order_by(models.StockMovement.id.desc()).offset(skip).limit(limit).all()
    result = []
    for m in movements:
        item = db.query(models.Item).get(m.item_id)
        result.append({
            "id": m.id, "date": str(m.date),
            "item_id": m.item_id, "item_name": item.name if item else "-",
            "item_code": item.code if item else "-",
            "type": m.type, "qty": m.qty,
            "qty_before": m.qty_before, "qty_after": m.qty_after,
            "reference": m.reference, "notes": m.notes
        })
    return result


@router.get("/low-stock")
def get_low_stock(db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(models.Item).filter(
        models.Item.is_active == True,
        models.Item.stock <= models.Item.min_stock
    ).all()
    return [{"id": i.id, "code": i.code, "name": i.name, "stock": i.stock, "min_stock": i.min_stock} for i in items]


@router.post("/adjustment")
def stock_adjustment(
    item_id: int, qty: float, notes: Optional[str] = None,
    adj_date: Optional[date] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    item = db.query(models.Item).get(item_id)
    if not item: raise HTTPException(404, "Item tidak ditemukan")
    before = item.stock
    item.stock += qty
    d = adj_date or date.today()
    db.add(models.StockMovement(
        date=d, item_id=item_id, type="adjustment",
        qty=qty, qty_before=before, qty_after=item.stock,
        reference="ADJ", notes=notes or "Penyesuaian stok"
    ))
    db.commit()
    return {"message": "Stok disesuaikan", "new_stock": item.stock}


# ─── Stock Opname ─────────────────────────────────────────────────────────────
@router.get("/opname", response_model=list[schemas.StockOpnameOut])
def get_opnames(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.StockOpname).order_by(models.StockOpname.id.desc()).all()

@router.post("/opname", response_model=schemas.StockOpnameOut)
def create_opname(data: schemas.StockOpnameCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from datetime import date as _date
    today = _date.today()
    prefix = f"OP{today.strftime('%Y%m%d')}"
    last = db.query(models.StockOpname).filter(models.StockOpname.number.like(f"{prefix}%")).order_by(models.StockOpname.id.desc()).first()
    number = data.number or f"{prefix}{(int(last.number[-4:])+1 if last else 1):04d}"

    opname = models.StockOpname(number=number, date=data.date, notes=data.notes)
    db.add(opname); db.flush()

    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        if not item: raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        diff = it.actual_qty - item.stock
        db.add(models.StockOpnameItem(
            opname_id=opname.id, item_id=it.item_id,
            system_qty=item.stock, actual_qty=it.actual_qty, difference=diff
        ))

    db.commit(); db.refresh(opname); return opname

@router.post("/opname/{oid}/confirm")
def confirm_opname(oid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    opname = db.query(models.StockOpname).get(oid)
    if not opname: raise HTTPException(404, "Opname tidak ditemukan")
    if opname.status == "confirmed": raise HTTPException(400, "Sudah dikonfirmasi")

    for oi in opname.items:
        item = db.query(models.Item).get(oi.item_id)
        if item:
            before = item.stock
            item.stock = oi.actual_qty
            db.add(models.StockMovement(
                date=opname.date, item_id=item.id,
                type="opname", qty=oi.difference,
                qty_before=before, qty_after=item.stock,
                reference=opname.number, notes="Stock Opname"
            ))
    opname.status = "confirmed"
    db.commit()
    return {"message": "Opname dikonfirmasi"}
