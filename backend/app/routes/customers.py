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
