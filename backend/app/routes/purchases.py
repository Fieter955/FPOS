from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date
import pytz

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()

# --- Setup Zona Waktu Lokal (WITA / Bali) ---
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    return datetime.now(WITA).date()

def get_local_datetime():
    return datetime.now(WITA)


def _next_number(db: Session, prefix: str, model) -> str:
    today = get_local_date()
    prefix_full = f"{prefix}{today.strftime('%Y%m%d')}"
    
    last = db.query(model).filter(model.number.like(f"{prefix_full}%")).order_by(model.id.desc()).first()
    
    if last and len(last.number) >= 4:
        try:
            seq = int(last.number[-4:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
        
    return f"{prefix_full}{seq:04d}"


@router.get("/", response_model=list[schemas.PurchaseOut])
def get_purchases(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    supplier_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    q = db.query(models.Purchase)
    if start_date: q = q.filter(models.Purchase.date >= start_date)
    if end_date: q = q.filter(models.Purchase.date <= end_date)
    if supplier_id: q = q.filter(models.Purchase.supplier_id == supplier_id)
    if status: q = q.filter(models.Purchase.status == status)
    return q.order_by(models.Purchase.id.desc()).offset(skip).limit(limit).all()


@router.get("/{pid}", response_model=schemas.PurchaseOut)
def get_purchase(pid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Purchase).get(pid)
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    return obj


@router.post("/", response_model=schemas.PurchaseOut)
def create_purchase(
    data: schemas.PurchaseCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    local_date = get_local_date()
    local_datetime = get_local_datetime()

    number = data.number or _next_number(db, "PB", models.Purchase)

    subtotal = 0.0
    for it in data.items:
        disc = it.buy_price * (it.discount / 100)
        subtotal += (it.buy_price - disc) * it.qty

    tax_amount = subtotal * (data.tax / 100)
    disc_amount = subtotal * (data.discount / 100)
    total = subtotal - disc_amount + tax_amount

    # PERBAIKAN: Gunakan local_date dan catat siapa yang buat
    purchase = models.Purchase(
        number=number,
        date=local_date, # <--- Obat Zona Waktu
        created_at=local_datetime,
        created_by=current_user.id,
        supplier_id=data.supplier_id,
        subtotal=subtotal,
        discount=disc_amount,
        tax=tax_amount,
        total=total,
        status="unpaid",
        notes=data.notes
    )
    db.add(purchase)
    db.flush()

    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        if not item: raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        
        disc = it.buy_price * (it.discount / 100)
        line_total = (it.buy_price - disc) * it.qty

        db.add(models.PurchaseItem(
            purchase_id=purchase.id, item_id=it.item_id,
            qty=it.qty, buy_price=it.buy_price,
            discount=it.discount, total=line_total
        ))
        
        # Update stock & update buy_price
        before = item.stock
        item.stock += it.qty
        item.buy_price = it.buy_price
        
        db.add(models.StockMovement(
            date=local_date, 
            created_at=local_datetime,
            item_id=item.id,
            type="in", qty=it.qty,
            qty_before=before, qty_after=item.stock,
            reference=number, notes="Pembelian"
        ))

    db.commit()
    db.refresh(purchase)
    return purchase


@router.post("/{pid}/pay")
def pay_purchase(
    pid: int, 
    payment: schemas.PurchasePayment,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    obj = db.query(models.Purchase).get(pid)
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    
    obj.paid += payment.amount
    
    if obj.paid >= obj.total:
        obj.status = "paid"
    else:
        obj.status = "partial"
        
    db.commit()
    return {"message": "Pembayaran berhasil", "status": obj.status, "paid": obj.paid}


@router.delete("/{pid}")
def delete_purchase(pid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Purchase).get(pid)
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    if obj.status != "unpaid":
        raise HTTPException(400, "Hanya pembelian belum dibayar yang bisa dihapus")
        
    # Reverse stock
    for it in obj.items:
        item = db.query(models.Item).get(it.item_id)
        if item:
            item.stock -= it.qty
            
    db.delete(obj)
    db.commit()
    return {"message": "Pembelian dihapus"}