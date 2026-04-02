from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()


# ─── Categories ───────────────────────────────────────────────────────────────
@router.get("/categories", response_model=list[schemas.CategoryOut])
def get_categories(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Category).all()

@router.post("/categories", response_model=schemas.CategoryOut)
def create_category(cat: schemas.CategoryCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = models.Category(**cat.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

@router.put("/categories/{cat_id}", response_model=schemas.CategoryOut)
def update_category(cat_id: int, cat: schemas.CategoryCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Category).get(cat_id)
    if not obj: raise HTTPException(404, "Kategori tidak ditemukan")
    for k, v in cat.model_dump().items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Category).get(cat_id)
    if not obj: raise HTTPException(404, "Kategori tidak ditemukan")
    db.delete(obj); db.commit()
    return {"message": "Kategori dihapus"}


# ─── Units ────────────────────────────────────────────────────────────────────
@router.get("/units", response_model=list[schemas.UnitOut])
def get_units(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Unit).all()

@router.post("/units", response_model=schemas.UnitOut)
def create_unit(unit: schemas.UnitCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = models.Unit(**unit.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.put("/units/{unit_id}", response_model=schemas.UnitOut)
def update_unit(unit_id: int, unit: schemas.UnitCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Unit).get(unit_id)
    if not obj: raise HTTPException(404, "Satuan tidak ditemukan")
    for k, v in unit.model_dump().items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/units/{unit_id}")
def delete_unit(unit_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Unit).get(unit_id)
    if not obj: raise HTTPException(404, "Satuan tidak ditemukan")
    db.delete(obj); db.commit()
    return {"message": "Satuan dihapus"}


# ─── Items ────────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[schemas.ItemOut])
def get_items(
    search: Optional[str] = None,
    category_id: Optional[int] = None,
    active_only: bool = True,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(models.Item)
    if active_only: q = q.filter(models.Item.is_active == True)
    if search: q = q.filter(
        models.Item.name.ilike(f"%{search}%") |
        models.Item.code.ilike(f"%{search}%") |
        models.Item.barcode.ilike(f"%{search}%")
    )
    if category_id: q = q.filter(models.Item.category_id == category_id)
    return q.offset(skip).limit(limit).all()

@router.get("/{item_id}", response_model=schemas.ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Item).get(item_id)
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    return obj

@router.post("/", response_model=schemas.ItemOut)
def create_item(item: schemas.ItemCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    if db.query(models.Item).filter(models.Item.code == item.code).first():
        raise HTTPException(400, "Kode item sudah digunakan")
    prices = item.prices or []
    item_data = item.model_dump(exclude={"prices"})
    obj = models.Item(**item_data)
    db.add(obj); db.flush()
    for p in prices:
        db.add(models.ItemPrice(item_id=obj.id, **p.model_dump()))
    db.commit(); db.refresh(obj); return obj

@router.put("/{item_id}", response_model=schemas.ItemOut)
def update_item(item_id: int, item: schemas.ItemUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Item).get(item_id)
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    data = item.model_dump(exclude_unset=True, exclude={"prices"})
    for k, v in data.items(): setattr(obj, k, v)
    if item.prices is not None:
        db.query(models.ItemPrice).filter(models.ItemPrice.item_id == item_id).delete()
        for p in item.prices:
            db.add(models.ItemPrice(item_id=item_id, **p.model_dump()))
    db.commit(); db.refresh(obj); return obj

@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Item).get(item_id)
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    obj.is_active = False
    db.commit()
    return {"message": "Item dinonaktifkan"}
