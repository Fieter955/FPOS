from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional
import pandas as pd
from io import BytesIO
import uuid

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


# ─── IMPORT EXCEL (FITUR BARU) ────────────────────────────────────────────────
@router.post("/import")
async def import_items_from_excel(
    file: UploadFile = File(...), 
    db: Session = Depends(get_db), 
    _=Depends(get_current_user)
):
    """
    Import ribuan barang dari file Excel/CSV.
    Otomatis mendeteksi Kategori (JENIS) dan Satuan (SATUAN) yang baru.
    """
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(400, "Format file harus Excel (.xlsx/.xls) atau CSV")

    try:
        # 1. Baca file ke dalam memori
        contents = await file.read()
        if file.filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents))

        # 2. Bersihkan Data (Ganti sel kosong dengan default aman)
        df = df.fillna({
            'KODEBARCODE': '', 'NAMAITEM': '', 'JENIS': '', 'MEREK': '', 'SATUAN': '',
            'HARGAPOKOK': 0, 'HARGAJUAL': 0, 'STOK': 0, 'STOKMIN': 0, 'KETERANGAN': ''
        })

        # 3. Cache Kategori & Satuan yang sudah ada agar sangat cepat
        existing_cats = {c.name.upper(): c.id for c in db.query(models.Category).all()}
        existing_units = {u.name.upper(): u.id for u in db.query(models.Unit).all()}

        items_to_insert = []
        
        # 4. Looping Baris Excel
        for index, row in df.iterrows():
            nama_item = str(row['NAMAITEM']).strip()
            if not nama_item:
                continue # Abaikan baris jika nama barang kosong

            # --- Handle Kategori (Otomatis Buat Jika Belum Ada) ---
            cat_name = str(row['JENIS']).strip()
            cat_id = None
            if cat_name:
                if cat_name.upper() not in existing_cats:
                    new_cat = models.Category(name=cat_name)
                    db.add(new_cat); db.flush() # Simpan sementara untuk dapat ID
                    existing_cats[cat_name.upper()] = new_cat.id
                cat_id = existing_cats[cat_name.upper()]

            # --- Handle Satuan (Otomatis Buat Jika Belum Ada) ---
            unit_name = str(row['SATUAN']).strip()
            unit_id = None
            if unit_name:
                if unit_name.upper() not in existing_units:
                    new_unit = models.Unit(name=unit_name)
                    db.add(new_unit); db.flush()
                    existing_units[unit_name.upper()] = new_unit.id
                unit_id = existing_units[unit_name.upper()]

            # --- Handle Kode Item (Wajib Unik) ---
            kode_barcode = str(row['KODEBARCODE']).strip()
            # Jika barcode ada, jadikan kode item. Jika kosong, generate kode unik!
            item_code = kode_barcode if kode_barcode else f"ITM-{uuid.uuid4().hex[:6].upper()}"

            # --- Gabung Merek & Keterangan ke kolom Deskripsi ---
            merek = str(row['MEREK']).strip()
            ket = str(row['KETERANGAN']).strip()
            desc = f"Merek: {merek} | {ket}" if merek or ket else ""

            # 5. Rakit Objek Item (Sesuai dengan skema Anda)
            new_item = models.Item(
                code=item_code,
                name=nama_item,
                category_id=cat_id,
                unit_id=unit_id,
                buy_price=float(row['HARGAPOKOK']),
                sell_price=float(row['HARGAJUAL']),
                stock=float(row['STOK']),
                min_stock=float(row['STOKMIN']),
                description=desc,
                barcode=kode_barcode if kode_barcode else None,
                is_active=True
            )
            items_to_insert.append(new_item)

        # 6. BULK INSERT (Eksekusi 5000+ data dalam sekejap)
        db.bulk_save_objects(items_to_insert)
        db.commit()

        return {"success": True, "message": f"Berhasil mengimpor {len(items_to_insert)} barang!"}

    except Exception as e:
        db.rollback() # Jika error, kembalikan DB ke kondisi awal (Aman!)
        raise HTTPException(500, f"Gagal import: {str(e)}")