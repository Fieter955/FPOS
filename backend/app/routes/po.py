from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user
from .purchases import _next_number, get_local_date, get_local_datetime

router = APIRouter()

@router.get("/requests", response_model=list[schemas.PurchaseOut])
def get_incoming_requests(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    """
    Toko Pusat (ID 1) melihat permintaan yang masuk dari cabang.
    """
    if current_user.active_branch_id != 1:
        raise HTTPException(status_code=403, detail="Hanya Toko Pusat yang bisa melihat daftar request masuk.")
        
    return db.query(models.Purchase).filter(
        models.Purchase.is_branch_request == True,
        models.Purchase.target_branch_id == 1,
        models.Purchase.status == "pending"
    ).order_by(models.Purchase.id.desc()).all()

@router.get("/count-pending")
def count_pending_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Mengambil jumlah request pending yang ditujukan ke Toko Pusat.
    """
    if current_user.active_branch_id != 1:
        return {"count": 0}
        
    count = db.query(models.Purchase).filter(
        models.Purchase.is_branch_request == True,
        models.Purchase.target_branch_id == 1,
        models.Purchase.status == "pending"
    ).count()
    
    return {"count": count}

@router.get("/my-requests", response_model=list[schemas.PurchaseOut])
def get_my_requests(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    """
    Cabang melihat permintaan yang pernah mereka buat.
    """
    return db.query(models.Purchase).filter(
        models.Purchase.branch_id == current_user.active_branch_id,
        models.Purchase.is_branch_request == True
    ).order_by(models.Purchase.id.desc()).all()

@router.post("/")
def create_branch_request(
    data: schemas.PurchaseCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    """
    Membuat permintaan barang (PO) dari cabang ke pusat.
    """
    tanggal = data.date if data.date else get_local_date()
    
    # Pastikan ini ditandai sebagai request
    data.is_branch_request = True
    if not data.target_branch_id:
        data.target_branch_id = 1 # Default ke Pusat
        
    number = data.number or _next_number(db, "REQ", models.Purchase, current_user)

    subtotal = sum(it.buy_price * it.qty for it in data.items)
    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount

    purchase = models.Purchase(
        number=number,
        date=tanggal,
        branch_id=current_user.active_branch_id,
        created_at=get_local_datetime(),
        supplier_id=data.supplier_id,
        subtotal=subtotal,
        discount=disc_amount,
        tax=tax_amount,
        total=total,
        paid=0,
        status="pending",
        notes=data.notes,
        created_by=current_user.id,
        is_branch_request=True,
        target_branch_id=data.target_branch_id
    )
    db.add(purchase)
    db.flush()

    for it in data.items:
        db.add(models.PurchaseItem(
            purchase_id=purchase.id,
            item_id=it.item_id,
            qty=it.qty,
            buy_price=it.buy_price,
            total=it.buy_price * it.qty
        ))

    db.commit()
    db.refresh(purchase)
    return purchase
