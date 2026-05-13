from datetime import date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user
from ..database import get_db
from ..services.purchase_flow import PUSAT_BRANCH_ID, create_branch_request as service_create_branch_request
from .purchases import _next_number

router = APIRouter()


@router.get("/requests", response_model=List[schemas.PurchaseOut])
def get_incoming_requests(
    status: Optional[str] = "pending",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.active_branch_id != PUSAT_BRANCH_ID:
        raise HTTPException(403, "Hanya Toko Pusat yang bisa melihat daftar request masuk.")

    q = db.query(models.Purchase).filter(
        models.Purchase.is_branch_request == True,
        models.Purchase.target_branch_id == PUSAT_BRANCH_ID,
    )

    if status and status != "all":
        q = q.filter(models.Purchase.status == status)
    
    if start_date:
        q = q.filter(models.Purchase.date >= start_date)
    if end_date:
        q = q.filter(models.Purchase.date <= end_date)

    return q.order_by(models.Purchase.id.desc()).all()


@router.get("/count-pending")
def count_pending_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.active_branch_id != PUSAT_BRANCH_ID:
        return {"count": 0}

    count = db.query(models.Purchase).filter(
        models.Purchase.is_branch_request == True,
        models.Purchase.target_branch_id == PUSAT_BRANCH_ID,
        models.Purchase.status == "pending",
    ).count()
    return {"count": count}


@router.get("/my-requests", response_model=List[schemas.PurchaseOut])
def get_my_requests(
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.Purchase).filter(
        models.Purchase.branch_id == current_user.active_branch_id,
        models.Purchase.is_branch_request == True,
    )

    if status and status != "all":
        q = q.filter(models.Purchase.status == status)
    
    if start_date:
        q = q.filter(models.Purchase.date >= start_date)
    if end_date:
        q = q.filter(models.Purchase.date <= end_date)

    return q.order_by(models.Purchase.id.desc()).all()


@router.post("/")
def create_branch_request(
    data: schemas.PurchaseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    number = data.number or _next_number(db, "REQ", models.Purchase, current_user)
    request = service_create_branch_request(
        db,
        data=data,
        current_user=current_user,
        number=number,
    )
    db.commit()
    db.refresh(request)
    return request
