from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()

# ─── Customer Groups ──────────────────────────────────────────────────────────
@router.get("/groups", response_model=list[schemas.CustomerGroupOut])
def get_groups(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.CustomerGroup).all()

@router.post("/groups", response_model=schemas.CustomerGroupOut)
def create_group(g: schemas.CustomerGroupCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = models.CustomerGroup(**g.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.put("/groups/{gid}", response_model=schemas.CustomerGroupOut)
def update_group(gid: int, g: schemas.CustomerGroupCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
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
                  skip: int = 0, limit: int = 100,
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(models.Customer)
    if active_only: q = q.filter(models.Customer.is_active == True)
    if search: q = q.filter(
        models.Customer.name.ilike(f"%{search}%") |
        models.Customer.code.ilike(f"%{search}%") |
        models.Customer.phone.ilike(f"%{search}%")
    )
    return q.offset(skip).limit(limit).all()

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
def delete_customer(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Customer).get(cid)
    if not obj: raise HTTPException(404, "Pelanggan tidak ditemukan")
    obj.is_active = False
    db.commit()
    return {"message": "Pelanggan dinonaktifkan"}
