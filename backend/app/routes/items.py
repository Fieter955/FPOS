from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.concurrency import run_in_threadpool
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
    get_conversion_factor,
    get_effective_buy_price,
    get_effective_stock_from_source,
    get_stock_source_item,
    is_virtual_variant,
)

router = APIRouter()


def _is_admin_user(user: models.User) -> bool:
    return (user.role or "") == "admin"


def _serialize_item_for_user(item: models.Item, current_user: models.User):
    data = schemas.ItemOut.model_validate(item).model_dump()
    # Lantai harga modal (HPP) untuk penjaga anti-rugi diskon grup di POS.
    # Dikirim ke semua role agar harga grup konsisten antara admin & kasir,
    # tanpa pernah menampilkan harga modal di UI mana pun.
    _fifo_min = getattr(item, "_fifo_min_price", None)
    data["min_price"] = float(_fifo_min if _fifo_min is not None else (item.buy_price or 0))
    if not _is_admin_user(current_user):
        data["buy_price"] = 0
    return data


def _serialize_item_lite(item: models.Item, current_user: models.User):
    """Serialisasi RINGAN untuk daftar/tabel barang. TANPA suppliers, supplier_details,
    group_discounts, dan brand — field-field itu hanya dipakai POS & menu pembelian (yang
    memanggil tanpa lite). Dengan begitu eager-load + hidrasi ORM jauh lebih ringan
    (query daftar item ~4x lebih cepat). Dipakai endpoint ?lite=1 (mis. tabel Data Barang)."""
    cat = item.category
    unit = item.unit
    buy = float(item.buy_price or 0)
    _fifo_min = getattr(item, "_fifo_min_price", None)
    min_price = float(_fifo_min if _fifo_min is not None else buy)
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "barcode": item.barcode,
        "category_id": item.category_id,
        "unit_id": item.unit_id,
        "category": {"id": cat.id, "name": cat.name} if cat else None,
        "unit": {
            "id": unit.id, "name": unit.name, "abbreviation": unit.abbreviation,
        } if unit else None,
        "buy_price": buy if _is_admin_user(current_user) else 0,
        "min_price": min_price,
        "sell_price": float(item.sell_price or 0),
        "ppn_percent": item.ppn_percent,
        "stock": float(item.stock or 0),
        "min_stock": float(item.min_stock or 0),
        "is_active": bool(item.is_active),
        "is_discountable": bool(item.is_discountable),
        "is_virtual_variant": bool(getattr(item, "is_virtual_variant", False)),
        "parent_item_id": item.parent_item_id,
        "prices": [
            {"name": p.name, "price": float(p.price or 0), "min_qty": float(p.min_qty or 0)}
            for p in item.prices
        ],
    }


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
    min_cost_map = {}   # item_id -> harga modal batch FIFO tertua (per satuan dasar)
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

                # Lantai HPP (min_price): harga modal batch FIFO TERTUA per item di
                # gudang ini. SATU query; baris pertama (terlama) tiap item dipakai.
                batch_rows = db.query(
                    models.StockBatch.item_id,
                    models.StockBatch.unit_cost,
                ).filter(
                    models.StockBatch.warehouse_id == gudang[0],
                    models.StockBatch.item_id.in_(stock_item_ids),
                    models.StockBatch.qty_remaining > 1e-9,
                ).order_by(
                    models.StockBatch.item_id.asc(),
                    models.StockBatch.received_date.asc(),
                    models.StockBatch.id.asc(),
                ).all()
                for it_id, cost in batch_rows:
                    if it_id not in min_cost_map:
                        min_cost_map[it_id] = float(cost or 0)
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

        # Lantai HPP per satuan jual: biaya batch tertua dari sumber stok (induk),
        # dikonversi ke satuan item ini bila multi-satuan. Fallback ke buy_price
        # (dilakukan di serializer) bila item belum punya batch.
        base_min = min_cost_map.get(stock_source.id)
        if base_min is not None:
            factor = get_conversion_factor(item) if is_virtual_variant(item) else 1.0
            item._fifo_min_price = round(base_min * (factor or 1.0), 4)
        else:
            item._fifo_min_price = None

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


# ─── Brands ───────────────────────────────────────────────────────────────────
@router.get("/brands", response_model=list[schemas.BrandOut])
def get_brands(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Brand).all()

@router.post("/brands", response_model=schemas.BrandOut)
def create_brand(brand: schemas.BrandCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = models.Brand(**brand.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

@router.put("/brands/{brand_id}", response_model=schemas.BrandOut)
def update_brand(brand_id: int, brand: schemas.BrandCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Brand).get(brand_id)
    if not obj: raise HTTPException(404, "Merek tidak ditemukan")
    for k, v in brand.model_dump().items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

@router.delete("/brands/{brand_id}")
def delete_brand(brand_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    obj = db.query(models.Brand).get(brand_id)
    if not obj: raise HTTPException(404, "Merek tidak ditemukan")
    db.delete(obj); db.commit()
    return {"message": "Merek dihapus"}


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
# response_model=None: _serialize_item_for_user sudah menghasilkan dict berbentuk ItemOut,
# jadi tak perlu validasi ulang Pydantic (hindari serialisasi dobel atas ribuan item).
@router.get("/", response_model=None)
def get_items(
    search: Optional[str] = None,
    category_id: Optional[int] = None,
    active_only: bool = True,
    lite: bool = False,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user) # 👈 Wajib passing current_user
):

    # 🛡️ FIX BUG GAIB (PAGINATION LIMIT) 🛡️
    # Ubah angka 500 menjadi 1000 agar halaman Supplier juga ikut ter-cover!
    if limit <= 1000:
        limit = 20000

    # Eager-load relasi to-one via joinedload, dan koleksi via selectinload.
    # selectinload menghindari "cartesian product" (row explosion) yang terjadi bila beberapa
    # relasi one-to-many di-joinedload sekaligus.
    # Mode `lite`: hanya muat relasi yang dipakai TABEL barang (category, unit, prices) →
    # lewati suppliers/supplier_details/group_discounts/brand yang berat untuk dihidrasi.
    from sqlalchemy.orm import joinedload, selectinload
    if lite:
        q = db.query(models.Item).options(
            joinedload(models.Item.category),
            joinedload(models.Item.unit),
            selectinload(models.Item.prices),
        )
    else:
        q = db.query(models.Item).options(
            joinedload(models.Item.category),
            joinedload(models.Item.brand),
            joinedload(models.Item.unit),
            selectinload(models.Item.suppliers),
            selectinload(models.Item.supplier_details),
            selectinload(models.Item.prices),
            selectinload(models.Item.group_discounts),
        )

    # 🏢 FILTER LINTAS CABANG (Penyaringan Barang)
    active_branch = None
    if current_user.active_branch_id:
        active_branch = db.query(models.Branch).get(current_user.active_branch_id)
    
    is_pusat = not active_branch or (active_branch.id == 1 or active_branch.status == "Toko Utama")

    if not is_pusat:
        # Jika bukan Pusat, hanya munculkan barang yang sudah pernah didaftarkan stoknya di gudang cabang ini
        gudang_ids = [g.id for g in db.query(models.Warehouse.id).filter(models.Warehouse.branch_id == active_branch.id).all()]
        if gudang_ids:
            # Menggunakan join + distinct agar lebih mantap dan menghindari duplikasi jika ada >1 gudang per cabang
            q = q.join(models.WarehouseStock).filter(models.WarehouseStock.warehouse_id.in_(gudang_ids)).distinct()
        else:
            q = q.filter(models.Item.id == -1)

    if active_only: q = q.filter(models.Item.is_active == True)
    if search: q = q.filter(
        models.Item.name.ilike(f"%{search}%") |
        models.Item.code.ilike(f"%{search}%") |
        models.Item.barcode.ilike(f"%{search}%")
    )
    if category_id: q = q.filter(models.Item.category_id == category_id)

    items = q.offset(skip).limit(limit).all()
    items = _apply_virtual_item_metrics(items, db, current_user)
    if lite:
        return [_serialize_item_lite(item, current_user) for item in items]
    return [_serialize_item_for_user(item, current_user) for item in items]

@router.get("/{item_id}", response_model=schemas.ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    from sqlalchemy.orm import joinedload, selectinload
    obj = db.query(models.Item).options(
        joinedload(models.Item.category),
        joinedload(models.Item.brand),
        joinedload(models.Item.unit),
        selectinload(models.Item.suppliers),
        selectinload(models.Item.supplier_details),
        selectinload(models.Item.prices),
        selectinload(models.Item.group_discounts),
    ).filter(models.Item.id == item_id).first()
    if not obj: raise HTTPException(404, "Item tidak ditemukan")
    obj = _apply_virtual_item_metrics([obj], db, current_user)[0]
    return _serialize_item_for_user(obj, current_user)

@router.post("/", response_model=schemas.ItemOut)
def create_item(item: schemas.ItemCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    import uuid

    # Ambil data form, kecuali prices dan supplier_ids
    item_data = item.model_dump(exclude={"prices", "supplier_ids", "supplier_settings", "group_discounts"})

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
                barcode=s_data.barcode,
                ppn_type=s_data.ppn_type,
                ppn_percent=s_data.ppn_percent
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

    # 5b. Simpan Potongan Harga Jual per grup (diskon bertingkat Pot.1–Pot.4)
    if item.group_discounts:
        for gd in item.group_discounts:
            db.add(models.ItemGroupDiscount(item_id=obj.id, **gd.model_dump()))

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
    
    data = item.model_dump(exclude_unset=True, exclude={"prices", "supplier_ids", "supplier_settings", "group_discounts"})

    # Cegah bentrok nama (Item.name unik) agar tidak jatuh ke error 500 yang membingungkan
    nama_baru = data.get("name")
    if nama_baru and nama_baru != obj.name:
        bentrok = db.query(models.Item).filter(
            models.Item.name == nama_baru,
            models.Item.id != item_id,
        ).first()
        if bentrok:
            raise HTTPException(400, f"Nama barang '{nama_baru}' sudah dipakai item lain.")

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
                barcode=s_data.barcode,
                ppn_type=s_data.ppn_type,
                ppn_percent=s_data.ppn_percent
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

    if item.group_discounts is not None:
        db.query(models.ItemGroupDiscount).filter(models.ItemGroupDiscount.item_id == item_id).delete()
        for gd in item.group_discounts:
            db.add(models.ItemGroupDiscount(item_id=item_id, **gd.model_dump()))

    db.commit()
    db.refresh(obj)
    return obj

@router.put("/{item_id}/harga-supplier")
def ubah_harga_beli_supplier(
    item_id: int,
    data: schemas.HargaSupplierUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Set harga beli dan/atau PPN item ini khusus untuk satu supplier (upsert ItemSupplier).
    Hanya field yang dikirim yang di-update — tidak mengubah Item.buy_price global maupun
    baris supplier lain. Harga 0/None dianggap 'belum di-set' dan tidak menimpa harga lama."""
    item = db.query(models.Item).get(item_id)
    if not item:
        raise HTTPException(404, "Item tidak ditemukan")
    spec = db.query(models.ItemSupplier).filter(
        models.ItemSupplier.item_id == item_id,
        models.ItemSupplier.supplier_id == data.supplier_id,
    ).first()
    if spec:
        if data.harga_beli is not None and data.harga_beli > 0:
            spec.buy_price = data.harga_beli
        if data.ppn_type is not None:
            spec.ppn_type = data.ppn_type
        if data.ppn_percent is not None:
            spec.ppn_percent = data.ppn_percent
    else:
        db.add(models.ItemSupplier(
            item_id=item_id,
            supplier_id=data.supplier_id,
            buy_price=(data.harga_beli if data.harga_beli and data.harga_beli > 0 else item.buy_price),
            barcode=item.barcode,
            ppn_type=(data.ppn_type if data.ppn_type is not None else "included"),
            ppn_percent=(data.ppn_percent if data.ppn_percent is not None else 0),
        ))
    db.commit()
    return {"message": "Data supplier untuk item diperbarui"}

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
# ─── IMPORT EXCEL (VERSI AUTO-GUDANG, FIX WARNING, SKIP DUPLIKAT & SAFE PER-BARIS) ───────────────
@router.post("/import")
async def import_items_from_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Import ribuan barang dari file Excel/CSV.
    - Otomatis normalisasi nama kolom (SUPPLIER1 → SUPPLIER)
    - Otomatis deteksi Kategori, Satuan, Supplier
    - AUTO-CREATE Gudang jika belum ada
    - SKIP duplikat nama barang (case-insensitive, whitespace-safe)
    - Aman per-baris: 1 baris gagal tidak rollback semua
    """
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(400, "Format file harus Excel (.xlsx/.xls) atau CSV")

    contents = await file.read()
    # Parsing pandas + ribuan insert = kerja blocking berat. Jalankan di threadpool
    # agar TIDAK membekukan event loop (request cabang lain tetap responsif).
    return await run_in_threadpool(_import_items_sync, contents, file.filename, db, current_user)


def _import_items_sync(contents: bytes, filename: str, db: Session, current_user: models.User):
    import pytz
    from datetime import datetime

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents))

        # ✅ Normalisasi nama kolom agar kompatibel berbagai versi export
        df = df.rename(columns={
            'SUPPLIER1': 'SUPPLIER',
            'SUPPLIER2': 'SUPPLIER',  # jaga-jaga
        })

        df = df.fillna({
            'KODEBARCODE': '', 'NAMAITEM': '', 'JENIS': '', 'MEREK': '', 'SATUAN': '',
            'HARGAPOKOK': 0, 'HARGAJUAL': 0, 'STOK': 0, 'STOKMIN': 0, 'KETERANGAN': '',
            'SUPPLIER': ''
        })

        # ── 1. AUTO-CREATE GUDANG JIKA BELUM ADA ──────────────────────────
        b_id = current_user.active_branch_id
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == b_id
        ).first()

        if not gudang_aktif:
            kode_gudang = f"WH-{b_id or 'PUSAT'}-{uuid.uuid4().hex[:4].upper()}"
            nama_gudang = f"Gudang Cabang {b_id}" if b_id else "Gudang Pusat (Utama)"
            gudang_aktif = models.Warehouse(
                code=kode_gudang,
                name=nama_gudang,
                branch_id=b_id,
                is_active=True
            )
            db.add(gudang_aktif)
            db.flush()

        # ── 2. BUILD CACHE DATA MASTER ─────────────────────────────────────
        existing_cats      = {c.name.upper(): c.id for c in db.query(models.Category).all()}
        existing_units     = {u.name.upper(): u.id for u in db.query(models.Unit).all()}
        existing_suppliers = {s.name.upper(): s    for s in db.query(models.Supplier).all()}

        # ✅ Cache nama barang dari DB — key sudah di-normalize
        def normalize(text: str) -> str:
            """Collapse spasi ganda, strip, uppercase — biar perbandingan konsisten."""
            return " ".join(str(text).split()).upper()

        existing_item_names = {
            normalize(i.name): i for i in db.query(models.Item).all()
        }

        wita_time      = datetime.now(pytz.timezone("Asia/Makassar"))
        items_imported = 0
        items_skipped  = 0

        # ── 3. LOOP BARIS PER BARIS ───────────────────────────────────────
        for index, row in df.iterrows():
            raw_nama = str(row['NAMAITEM']).strip()
            if not raw_nama or raw_nama.lower() == 'nan':
                continue

            # ✅ Normalize nama: collapse spasi ganda & strip invisible chars
            nama_item    = " ".join(raw_nama.split())
            nama_key     = normalize(nama_item)

            # ✅ Cek cache dulu — kalau sudah ada, langsung skip (tanpa query ke DB)
            if nama_key in existing_item_names:
                items_skipped += 1
                continue

            # ✅ Double-check langsung ke DB untuk tangkap case yang lolos cache
            # (misal: spasi/dash berbeda tapi constraint tetap UNIQUE di DB)
            row_in_db = db.query(models.Item).filter(
                models.Item.name == nama_item
            ).first()
            if row_in_db:
                items_skipped += 1
                existing_item_names[nama_key] = row_in_db  # update cache
                continue

            # ── Kategori ──
            cat_name = " ".join(str(row['JENIS']).split())
            cat_id   = None
            if cat_name and cat_name.lower() != 'nan':
                cat_key = cat_name.upper()
                if cat_key not in existing_cats:
                    new_cat = models.Category(name=cat_name)
                    db.add(new_cat); db.flush()
                    existing_cats[cat_key] = new_cat.id
                cat_id = existing_cats[cat_key]

            # ── Satuan ──
            unit_name = " ".join(str(row['SATUAN']).split())
            unit_id   = None
            if unit_name and unit_name.lower() != 'nan':
                unit_key = unit_name.upper()
                if unit_key not in existing_units:
                    new_unit = models.Unit(name=unit_name)
                    db.add(new_unit); db.flush()
                    existing_units[unit_key] = new_unit.id
                unit_id = existing_units[unit_key]

            # ── Supplier ──
            sup_col        = str(row['SUPPLIER']).strip()
            item_suppliers = []
            if sup_col and sup_col.lower() != 'nan':
                for s_name in [s.strip() for s in sup_col.split(',') if s.strip()]:
                    s_key = s_name.upper()
                    if s_key not in existing_suppliers:
                        new_sup = models.Supplier(
                            code=f"SUP-{uuid.uuid4().hex[:5].upper()}",
                            name=s_name
                        )
                        db.add(new_sup); db.flush()
                        existing_suppliers[s_key] = new_sup
                    item_suppliers.append(existing_suppliers[s_key])

            # ── Field lain ──
            kode_barcode   = str(row['KODEBARCODE']).strip()
            barcode_valid  = kode_barcode and kode_barcode.lower() != 'nan'
            item_code      = kode_barcode if barcode_valid else f"ITM-{uuid.uuid4().hex[:6].upper()}"

            merek = " ".join(str(row['MEREK']).split())
            merek = "" if merek.lower() == 'nan' else merek
            ket   = " ".join(str(row['KETERANGAN']).split())
            ket   = "" if ket.lower() == 'nan' else ket
            desc  = f"Merek: {merek} | {ket}" if merek or ket else ""

            imported_stock = float(row['STOK'])     if pd.notnull(row['STOK'])     else 0.0
            buy_price      = float(row['HARGAPOKOK']) if pd.notnull(row['HARGAPOKOK']) else 0.0
            sell_price     = float(row['HARGAJUAL'])  if pd.notnull(row['HARGAJUAL'])  else 0.0
            min_stock      = float(row['STOKMIN'])    if pd.notnull(row['STOKMIN'])    else 0.0

            # ── 4. INSERT BARANG ───────────────────────────────────────────
            new_item = models.Item(
                code=item_code,
                name=nama_item,
                category_id=cat_id,
                unit_id=unit_id,
                buy_price=buy_price,
                sell_price=sell_price,
                stock=imported_stock,
                min_stock=min_stock,
                description=desc,
                barcode=kode_barcode if barcode_valid else None,
                is_active=True
            )
            db.add(new_item)
            db.flush()  # dapat ID resmi sekarang

            # ✅ Update cache agar baris berikutnya di file yang sama ikut ter-skip
            existing_item_names[nama_key] = new_item

            # ── 5. HUBUNGKAN SUPPLIER ──────────────────────────────────────
            for sup_obj in item_suppliers:
                db.add(models.ItemSupplier(
                    item_id=new_item.id,
                    supplier_id=sup_obj.id,
                    buy_price=buy_price,
                    barcode=new_item.barcode
                ))

            # ── 6. STOK GUDANG ─────────────────────────────────────────────
            db.add(models.WarehouseStock(
                warehouse_id=gudang_aktif.id,
                item_id=new_item.id,
                stock=imported_stock
            ))

            # ── 7. KARTU STOK (hanya jika ada saldo awal) ──────────────────
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
                # 🧱 FIFO: lapisan saldo awal. Tanggal sentinel 2000-01-01 (sama dgn
                # seed di main.py) agar stok awal dianggap TERTUA → keluar duluan.
                from ..services.inventory_fifo import add_batch
                from datetime import date as _date
                add_batch(
                    db,
                    item_id=new_item.id,
                    warehouse_id=gudang_aktif.id,
                    qty=imported_stock,
                    unit_cost=buy_price,
                    received_date=_date(2000, 1, 1),
                )

            items_imported += 1

        # ── COMMIT SEMUA SEKALIGUS ─────────────────────────────────────────
        db.commit()

        return {
            "success":  True,
            "imported": items_imported,
            "skipped":  items_skipped,
            "message": (
                f"Import selesai: {items_imported} barang berhasil ditambahkan"
                + (f", {items_skipped} dilewati karena sudah ada." if items_skipped else ".")
            )
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Gagal import: {str(e)}")
