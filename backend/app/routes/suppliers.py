from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import Optional
import uuid  # 👈 TAMBAHKAN INI
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()


# ─── GET ALL SUPPLIERS ────────────────────────────────────────────────────────
@router.get("/", response_model=list[schemas.SupplierOut])
def get_suppliers(
    search: Optional[str] = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.Supplier)
    if active_only:
        q = q.filter(models.Supplier.is_active == True)
    if search:
        q = q.filter(
            models.Supplier.name.ilike(f"%{search}%") |
            models.Supplier.code.ilike(f"%{search}%")
        )
    return q.offset(skip).limit(limit).all()


# ─── GET SINGLE SUPPLIER ──────────────────────────────────────────────────────
@router.get("/{sid}", response_model=schemas.SupplierOut)
def get_supplier(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    obj = db.query(models.Supplier).options(
        joinedload(models.Supplier.items)
    ).get(sid)
    if not obj:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return obj


# ─── GET ITEMS BY SUPPLIER ────────────────────────────────────────────────────
@router.get("/{sid}/items", response_model=list[schemas.ItemOut])
def get_supplier_items(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    supplier = db.query(models.Supplier).get(sid)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return supplier.items


# ─── CREATE SUPPLIER (AUTO-GENERATE CODE) ─────────────────────────────────────
@router.post("/", response_model=schemas.SupplierOut)
def create_supplier(
    s: schemas.SupplierCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    # 1. Generate kode jika kosong
    sup_code = s.code.strip() if s.code else f"SUP-{uuid.uuid4().hex[:5].upper()}"

    # 2. Cek duplikat SATU KALI SAJA dengan konsisten
    if db.query(models.Supplier).filter(models.Supplier.code == sup_code).first():
        raise HTTPException(status_code=400, detail="Kode supplier sudah digunakan")
    
    # 👇 TAMBAHKAN CEK NAMA
    if db.query(models.Supplier).filter(models.Supplier.name.ilike(s.name)).first():
        raise HTTPException(status_code=400, detail="Nama supplier sudah terdaftar")

    obj = models.Supplier(
        code=sup_code,
        name=s.name,
        contact_person=s.contact_person,
        phone=s.phone,
        email=s.email,
        address=s.address,
        is_active=True
    )
    db.add(obj)

    # 3. Hubungkan Item tanpa filter is_active agar tidak ada barang yang "nyangkut"
    if s.item_ids:
        for iid in s.item_ids:
            db.add(models.ItemSupplier(
                supplier=obj,
                item_id=iid
            ))

    db.commit()
    db.refresh(obj)
    return obj


# ─── UPDATE SUPPLIER ──────────────────────────────────────────────────────────
@router.put("/{sid}", response_model=schemas.SupplierOut)
def update_supplier(
    sid: int,
    s: schemas.SupplierUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    obj = db.query(models.Supplier).get(sid)
    if not obj:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")

    # 👇 CEK NAMA SAAT UPDATE
    # Hanya cek jika user mengubah nama (s.name ada isinya)
    if s.name:
        existing_name = db.query(models.Supplier).filter(
            models.Supplier.name.ilike(s.name),
            models.Supplier.id != sid  # 👈 Penting: Abaikan ID dirinya sendiri
        ).first()
        
        if existing_name:
            raise HTTPException(status_code=400, detail="Nama Supplier sudah terdaftar di supplier lain!")
    
    update_data = s.model_dump(exclude_unset=True, exclude={"item_ids"})
    item_ids = s.item_ids
    
    for k, v in update_data.items():
        setattr(obj, k, v)
    
    if item_ids is not None:
        # 🛡️ HAPUS RELASI LAMA LANGSUNG KE TABEL PERANTARA 🛡️
        # Karena relasi 'items' di model Supplier adalah viewonly=True
        db.query(models.ItemSupplier).filter(models.ItemSupplier.supplier_id == sid).delete()
        
        if item_ids:
            # Hubungkan item baru tanpa filter is_active agar data tetap konsisten
            for iid in item_ids:
                db.add(models.ItemSupplier(
                    supplier_id=sid,
                    item_id=iid
                ))
    
    db.commit()
    db.refresh(obj)
    return obj


# ─── DELETE/SOFT DELETE SUPPLIER ──────────────────────────────────────────────
@router.delete("/{sid}")
def delete_supplier(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    obj = db.query(models.Supplier).get(sid)
    if not obj:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    obj.is_active = False
    db.commit()
    return {"message": "Supplier dinonaktifkan"}


# ─── SALES PERSON ENDPOINTS (TIDAK DIUBAH) ────────────────────────────────────
@router.get("/salesperson/all", response_model=list[schemas.SalesPersonOut])
def get_salespersons(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return db.query(models.SalesPerson).filter(
        models.SalesPerson.is_active == True
    ).all()


@router.post("/salesperson", response_model=schemas.SalesPersonOut, status_code=status.HTTP_201_CREATED)
def create_salesperson(
    sp: schemas.SalesPersonCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    obj = models.SalesPerson(**sp.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj