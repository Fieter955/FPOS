from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
import pandas as pd
from io import BytesIO
import uuid

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user
from ..services.virtual_units import (
    get_effective_buy_price,
    get_effective_stock_from_source,
    get_stock_source_item,
    is_virtual_variant,
)

router = APIRouter()


def _is_admin_user(user: models.User) -> bool:
    return "admin" in (user.role or "")


def _serialize_item_for_user(item: models.Item, current_user: models.User):
    data = schemas.ItemOut.model_validate(item).model_dump()
    if not _is_admin_user(current_user):
        data["buy_price"] = 0
    return data


def _apply_virtual_item_metrics(items, db: Session, current_user: models.User):
    if not items:
        return items

    item_map = {item.id: item for item in items}
    parent_ids = {
        item.parent_item_id
        for item in items
        if is_virtual_variant(item)
        and item.parent_item_id
        and item.parent_item_id not in item_map
    }

    if parent_ids:
        for parent in db.query(models.Item).filter(models.Item.id.in_(parent_ids)).all():
            item_map[parent.id] = parent

    local_stock_map = None
    b_id = current_user.active_branch_id
    if b_id:
        gudang = db.query(models.Warehouse.id).filter(models.Warehouse.branch_id == b_id).first()
        if gudang:
            stock_item_ids = list(item_map.keys())
            if stock_item_ids:
                local_stocks = db.query(
                    models.WarehouseStock.item_id,
                    models.WarehouseStock.stock,
                ).filter(
                    models.WarehouseStock.warehouse_id == gudang[0],
                    models.WarehouseStock.item_id.in_(stock_item_ids),
                ).all()
                local_stock_map = {item_id: stock for item_id, stock in local_stocks}
        else:
            local_stock_map = {}

    for item in items:
        stock_source = get_stock_source_item(db, item, item_map=item_map)
        source_stock = (
            float(local_stock_map.get(stock_source.id, 0) or 0)
            if local_stock_map is not None
            else float(stock_source.stock or 0)
        )
        item.stock = round(get_effective_stock_from_source(item, source_stock), 4)
        item.buy_price = round(get_effective_buy_price(db, item, item_map=item_map), 4)

    return items

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
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user) # 👈 Wajib passing current_user
):
    
    # 🛡️ FIX BUG GAIB (PAGINATION LIMIT) 🛡️
    # Ubah angka 500 menjadi 1000 agar halaman Supplier juga ikut ter-cover!
    if limit <= 1000:
        limit = 20000
        
    q = db.query(models.Item)
    if active_only: q = q.filter(models.Item.is_active == True)
    if search: q = q.filter(
        models.Item.name.ilike(f"%{search}%") |
        models.Item.code.ilike(f"%{search}%") |
        models.Item.barcode.ilike(f"%{search}%")
    )
    if category_id: q = q.filter(models.Item.category_id == category_id)

    items = q.offset(skip).limit(limit).all()
    print(f"[DEBUG get_items] search='{search}', found {len(items)} items, user active_branch_id={current_user.active_branch_id}")  # 👈 DEBUG
    items = _apply_virtual_item_metrics(items, db, current_user)
    return [_serialize_item_for_user(item, current_user) for item in items]

@router.get("/{item_id}", response_model=schemas.ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Item).get(item_id)
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    obj = _apply_virtual_item_metrics([obj], db, current_user)[0]
    return _serialize_item_for_user(obj, current_user)

@router.post("/", response_model=schemas.ItemOut)
def create_item(item: schemas.ItemCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    import uuid

    # Ambil data form, kecuali prices dan supplier_ids
    item_data = item.model_dump(exclude={"prices", "supplier_ids"})

    # 1. Pastikan Kode ter-generate otomatis dengan aman di backend
    if not item_data.get("code") or item_data["code"] == "AUTO":
        item_data["code"] = f"ITM-{uuid.uuid4().hex[:6].upper()}"

    # 2. Paksa status Aktif agar PASTI terbaca di POS
    item_data["is_active"] = True

    obj = models.Item(**item_data)

    # 1.5. Simpan Supplier & Harga Khusus Supplier
    if item.supplier_settings:
        for s_data in item.supplier_settings:
            db.add(models.ItemSupplier(
                item=obj,
                supplier_id=s_data.supplier_id,
                buy_price=s_data.buy_price,
                barcode=s_data.barcode
            ))
    elif item.supplier_ids:
        for sid in item.supplier_ids:
            db.add(models.ItemSupplier(
                item=obj,
                supplier_id=sid,
                buy_price=obj.buy_price, # Default pakai harga beli umum
                barcode=obj.barcode       # Default pakai barcode umum
            ))

    db.add(obj)
    db.flush() # Simpan sementara untuk dapatkan ID Resmi    
    # 3. Auto-Generate Barcode
    need_barcode_gen = False
    if not obj.barcode or obj.barcode == "AUTO":
        need_barcode_gen = True
        from .barcode_gen import _generate_barcode_value
        obj.barcode = _generate_barcode_value(obj.code, "CODE128")
        
    # 4. Daftarkan Barcode ke Mesin Printer Label
    if need_barcode_gen:
        label_record = models.BarcodeLabel(
            item_id=obj.id,
            barcode_value=obj.barcode,
            barcode_type="CODE128",
            label_text=obj.name[:30],
        )
        db.add(label_record)
        
    # 5. Simpan Multi-Harga (Normal & Diskon)
    if item.prices:
        for p in item.prices:
            db.add(models.ItemPrice(item_id=obj.id, **p.model_dump()))
            
    # 6. 🛡️ INISIALISASI STOK GUDANG (SUPER PENTING!) 🛡️
    # Ini yang membuat barang terbaca di POS dan Menu Pembelian Supplier!
    b_id = current_user.active_branch_id
    if b_id:
        gudang_aktif = db.query(models.Warehouse).filter(models.Warehouse.branch_id == b_id).first()
        if gudang_aktif:
            db.add(models.WarehouseStock(
                warehouse_id=gudang_aktif.id,
                item_id=obj.id,
                stock=0.0 # Berikan stok awal 0
            ))
    else:
        # Jika user tidak memiliki cabang aktif, buat WarehouseStock untuk gudang pertama yang ditemukan
        # agar barang tetap terbaca di menu supplier
        gudang_pertama = db.query(models.Warehouse).filter(models.Warehouse.is_active == True).first()
        if gudang_pertama:
            db.add(models.WarehouseStock(
                warehouse_id=gudang_pertama.id,
                item_id=obj.id,
                stock=0.0
            ))

    db.commit()
    db.refresh(obj)
    return obj

@router.put("/{item_id}", response_model=schemas.ItemOut)
def update_item(item_id: int, item: schemas.ItemUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Item).get(item_id)
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    if is_virtual_variant(obj):
        raise HTTPException(
            400,
            f"Barang multi-satuan {obj.name} dikelola dari menu Multi Satuan, bukan edit barang biasa.",
        )
    
    data = item.model_dump(exclude_unset=True, exclude={"prices", "supplier_ids"})
    
    need_barcode_gen = False
    if data.get("barcode") == "AUTO":
        need_barcode_gen = True
        from .barcode_gen import _generate_barcode_value
        data["barcode"] = _generate_barcode_value(obj.code, "CODE128")
        
    for k, v in data.items(): 
        setattr(obj, k, v)
        
    # Update Supplier Settings
    if item.supplier_settings is not None:
        # Hapus yang lama, ganti yang baru
        db.query(models.ItemSupplier).filter(models.ItemSupplier.item_id == item_id).delete()
        for s_data in item.supplier_settings:
            db.add(models.ItemSupplier(
                item_id=item_id,
                supplier_id=s_data.supplier_id,
                buy_price=s_data.buy_price,
                barcode=s_data.barcode
            ))
    elif item.supplier_ids is not None:
        # Jika hanya kirim ID, reset detail dengan nilai default item saat ini
        db.query(models.ItemSupplier).filter(models.ItemSupplier.item_id == item_id).delete()
        for sid in item.supplier_ids:
            db.add(models.ItemSupplier(
                item_id=item_id,
                supplier_id=sid,
                buy_price=obj.buy_price,
                barcode=obj.barcode
            ))
        
    if need_barcode_gen:
        exist = db.query(models.BarcodeLabel).filter(models.BarcodeLabel.item_id == obj.id).first()
        if not exist:
            label_record = models.BarcodeLabel(
                item_id=obj.id,
                barcode_value=obj.barcode,
                barcode_type="CODE128",
                label_text=obj.name[:30],
            )
            db.add(label_record)
            
    if item.prices is not None:
        db.query(models.ItemPrice).filter(models.ItemPrice.item_id == item_id).delete()
        for p in item.prices:
            db.add(models.ItemPrice(item_id=item_id, **p.model_dump()))
            
    db.commit()
    db.refresh(obj)
    return obj

@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Item).get(item_id)
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    if is_virtual_variant(obj):
        raise HTTPException(
            400,
            f"Barang multi-satuan {obj.name} dinonaktifkan dari menu Multi Satuan.",
        )
    obj.is_active = False
    db.commit()
    return {"message": "Item dinonaktifkan"}


# ─── IMPORT EXCEL (TETAP SAMA) ───────────────
# Pastikan Anda sudah import ini di bagian paling atas file items.py:
# from datetime import datetime
# import pytz

# Pastikan Anda sudah import ini di bagian atas file items.py:
# from datetime import datetime
# import pytz
# import uuid
# import pandas as pd
# from io import BytesIO

# ─── IMPORT EXCEL (VERSI AUTO-GUDANG & FIX WARNING) ───────────────
@router.post("/import")
async def import_items_from_excel(
    file: UploadFile = File(...), 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user) 
):
    """
    Import ribuan barang dari file Excel/CSV.
    Otomatis mendeteksi Kategori, Satuan, SUPPLIER,
    dan AUTO-CREATE Gudang jika belum ada untuk menampung Saldo Awal!
    """
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(400, "Format file harus Excel (.xlsx/.xls) atau CSV")

    try:
        contents = await file.read()
        if file.filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents))

        df = df.fillna({
            'KODEBARCODE': '', 'NAMAITEM': '', 'JENIS': '', 'MEREK': '', 'SATUAN': '',
            'HARGAPOKOK': 0, 'HARGAJUAL': 0, 'STOK': 0, 'STOKMIN': 0, 'KETERANGAN': '',
            'SUPPLIER': '' 
        })

# 1. 🛡️ AUTO-CREATE GUDANG JIKA BELUM ADA
        b_id = current_user.active_branch_id
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == b_id
        ).first()

        if not gudang_aktif:
            # Generate kode unik untuk gudang, misal: WH-6-A1B2 atau WH-PUSAT-9F8A
            kode_gudang = f"WH-{b_id or 'PUSAT'}-{uuid.uuid4().hex[:4].upper()}"
            nama_gudang = f"Gudang Cabang {b_id}" if b_id else "Gudang Pusat (Utama)"
            
            gudang_aktif = models.Warehouse(
                code=kode_gudang, # 👈 PERBAIKAN: Kolom code wajib diisi!
                name=nama_gudang,
                branch_id=b_id,
                is_active=True
            )
            db.add(gudang_aktif)
            db.flush() # Simpan ke memori agar dapat ID Gudang

        # 2. Cache data master agar proses import ngebut
        existing_cats = {c.name.upper(): c.id for c in db.query(models.Category).all()}
        existing_units = {u.name.upper(): u.id for u in db.query(models.Unit).all()}
        existing_suppliers = {s.name.upper(): s for s in db.query(models.Supplier).all()}
        
        import pytz
        from datetime import datetime
        wita_time = datetime.now(pytz.timezone("Asia/Makassar"))
        items_imported = 0
        
        # 3. Looping baca baris per baris dari Excel
        for index, row in df.iterrows():
            nama_item = str(row['NAMAITEM']).strip()
            if not nama_item or nama_item.lower() == 'nan':
                continue 

            # Kategori
            cat_name = str(row['JENIS']).strip()
            cat_id = None
            if cat_name and cat_name.lower() != 'nan':
                if cat_name.upper() not in existing_cats:
                    new_cat = models.Category(name=cat_name)
                    db.add(new_cat); db.flush()
                    existing_cats[cat_name.upper()] = new_cat.id
                cat_id = existing_cats[cat_name.upper()]

            # Satuan
            unit_name = str(row['SATUAN']).strip()
            unit_id = None
            if unit_name and unit_name.lower() != 'nan':
                if unit_name.upper() not in existing_units:
                    new_unit = models.Unit(name=unit_name)
                    db.add(new_unit); db.flush()
                    existing_units[unit_name.upper()] = new_unit.id
                unit_id = existing_units[unit_name.upper()]

            # Persiapkan list Supplier
            sup_col = str(row['SUPPLIER']).strip()
            item_suppliers = [] 
            if sup_col and sup_col.lower() != 'nan':
                sup_names = [s.strip() for s in sup_col.split(',')]
                for s_name in sup_names:
                    if s_name:
                        if s_name.upper() not in existing_suppliers:
                            sup_code = f"SUP-{uuid.uuid4().hex[:5].upper()}"
                            new_sup = models.Supplier(code=sup_code, name=s_name)
                            db.add(new_sup); db.flush()
                            existing_suppliers[s_name.upper()] = new_sup
                        item_suppliers.append(existing_suppliers[s_name.upper()])

            kode_barcode = str(row['KODEBARCODE']).strip()
            item_code = kode_barcode if kode_barcode and kode_barcode.lower() != 'nan' else f"ITM-{uuid.uuid4().hex[:6].upper()}"
            merek = str(row['MEREK']).strip()
            merek = "" if merek.lower() == 'nan' else merek
            ket = str(row['KETERANGAN']).strip()
            ket = "" if ket.lower() == 'nan' else ket
            desc = f"Merek: {merek} | {ket}" if merek or ket else ""

            imported_stock = float(row['STOK']) if pd.notnull(row['STOK']) else 0.0

            # 4. 🛡️ BIKIN BARANG & LANGSUNG FLUSH (Ini yang menyembuhkan Error SAWarning!)
            # Catatan: Kita tidak memasukkan `suppliers=item_suppliers` di sini
            new_item = models.Item(
                code=item_code,
                name=nama_item,
                category_id=cat_id,
                unit_id=unit_id,
                buy_price=float(row['HARGAPOKOK']) if pd.notnull(row['HARGAPOKOK']) else 0.0,
                sell_price=float(row['HARGAJUAL']) if pd.notnull(row['HARGAJUAL']) else 0.0,
                stock=imported_stock, # Stok Global (Pusat)
                min_stock=float(row['STOKMIN']) if pd.notnull(row['STOKMIN']) else 0.0,
                description=desc,
                barcode=kode_barcode if kode_barcode and kode_barcode.lower() != 'nan' else None,
                is_active=True
            )
            db.add(new_item)
            db.flush() # Wajib flush agar new_item dapat ID resmi dari database!

            # 5. 🛡️ HUBUNGKAN SUPPLIER KE BARANG SECARA RESMI
            # Catatan: Kita gunakan model ItemSupplier karena 'suppliers' di model Item adalah viewonly=True
            for sup_obj in item_suppliers:
                db.add(models.ItemSupplier(
                    item_id=new_item.id,
                    supplier_id=sup_obj.id,
                    buy_price=new_item.buy_price, # Harga default dari excel
                    barcode=new_item.barcode       # Barcode default dari excel
                ))

            # 6. 🛡️ SUNTIKKAN STOK LOKAL & KARTU STOK KE GUDANG AKTIF
            # Selalu buat WarehouseStock agar barang terbaca di POS dan menu supplier
            db.add(models.WarehouseStock(
                warehouse_id=gudang_aktif.id,
                item_id=new_item.id,
                stock=imported_stock
            ))

            # Tulis riwayat kartu stok hanya jika ada stok awal
            if imported_stock > 0:
                db.add(models.StockMovement(
                    date=wita_time.date(),
                    created_at=wita_time,
                    item_id=new_item.id,
                    branch_id=b_id,
                    type="in",
                    qty=imported_stock,
                    qty_before=0.0,
                    qty_after=imported_stock,
                    reference="IMPORT-EXCEL",
                    notes=f"Saldo Awal - {gudang_aktif.name}"
                ))
            
            items_imported += 1

        db.commit()
        return {"success": True, "message": f"Berhasil mengimpor {items_imported} barang dan menyuntikkan Saldo Awal Gudang!"}

    except Exception as e:
        db.rollback() 
        raise HTTPException(500, f"Gagal import: {str(e)}")
