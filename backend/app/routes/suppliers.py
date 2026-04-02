from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()

@router.get("/", response_model=list[schemas.SupplierOut])
def get_suppliers(search: Optional[str] = None, active_only: bool = True,
                  skip: int = 0, limit: int = 100,
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(models.Supplier)
    if active_only: q = q.filter(models.Supplier.is_active == True)
    if search: q = q.filter(
        models.Supplier.name.ilike(f"%{search}%") |
        models.Supplier.code.ilike(f"%{search}%")
    )
    return q.offset(skip).limit(limit).all()

@router.get("/{sid}", response_model=schemas.SupplierOut)
def get_supplier(sid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Supplier).get(sid)
    if not obj: raise HTTPException(404, "Supplier tidak ditemukan")
    return obj

@router.post("/", response_model=schemas.SupplierOut)
def create_supplier(s: schemas.SupplierCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    if db.query(models.Supplier).filter(models.Supplier.code == s.code).first():
        raise HTTPException(400, "Kode supplier sudah digunakan")
    obj = models.Supplier(**s.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.put("/{sid}", response_model=schemas.SupplierOut)
def update_supplier(sid: int, s: schemas.SupplierUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Supplier).get(sid)
    if not obj: raise HTTPException(404, "Supplier tidak ditemukan")
    for k, v in s.model_dump(exclude_unset=True).items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/{sid}")
def delete_supplier(sid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Supplier).get(sid)
    if not obj: raise HTTPException(404, "Supplier tidak ditemukan")
    obj.is_active = False
    db.commit()
    return {"message": "Supplier dinonaktifkan"}


# ─── Sales Person ─────────────────────────────────────────────────────────────
@router.get("/salesperson/all", response_model=list[schemas.SalesPersonOut])
def get_salespersons(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.SalesPerson).filter(models.SalesPerson.is_active == True).all()

@router.post("/salesperson", response_model=schemas.SalesPersonOut)
def create_salesperson(sp: schemas.SalesPersonCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = models.SalesPerson(**sp.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj
