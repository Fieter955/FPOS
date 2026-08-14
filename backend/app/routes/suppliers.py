from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import Optional
import uuid  # 👈 TAMBAHKAN INI
from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()


# ─── GET ALL SUPPLIERS ────────────────────────────────────────────────────────
@router.get("/", response_model=list[schemas.SupplierListOut])
def get_suppliers(
    search: Optional[str] = None,
    active_only: bool = True,
    sort: Optional[str] = None,
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
    if sort == "deposit_desc":
        q = q.order_by(
            models.Supplier.deposit_balance.desc(),
            models.Supplier.name.asc(),
            models.Supplier.id.asc(),
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


# ─── SAMAKAN JENIS PPN SEMUA BARANG SUPPLIER ──────────────────────────────────
@router.post("/{sid}/apply-ppn-type")
def apply_supplier_ppn_type(
    sid: int,
    payload: schemas.SupplierPpnApply,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """Samakan Jenis PPN (included/excluded) seluruh barang milik supplier ini.

    - dry_run=True  → hanya laporkan barang yang setelannya BERBEDA (untuk popup konfirmasi).
    - dry_run=False → ubah ppn_type tiap barang berbeda + hitung ulang harga beli per-supplier
      (ItemSupplier.buy_price) dgn rumus yang sama spt form barang:
        included→excluded: harga × (1+r)   |   excluded→included: harga ÷ (1+r)
      r = ppn_percent barang; jika 0/kosong → PpnSupplier; jika itu pun 0 → 11.

    Hanya menyentuh harga beli referensi per-supplier — TIDAK menyentuh GL/jurnal/HPP
    (HPP diambil dari batch FIFO yang di-set saat pembelian)."""
    supplier = db.query(models.Supplier).get(sid)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")

    target = (payload.ppn_type or "").strip()
    if target not in ("included", "excluded"):
        raise HTTPException(status_code=400, detail="Jenis PPN tidak valid")

    rows = db.query(models.ItemSupplier).options(
        joinedload(models.ItemSupplier.item)
    ).filter(models.ItemSupplier.supplier_id == sid).all()

    berbeda = [r for r in rows if (r.ppn_type or "included") != target]

    if payload.dry_run:
        return {
            "count": len(berbeda),
            "different": [
                {
                    "item_id": r.item_id,
                    "name": r.item.name if r.item else f"#{r.item_id}",
                    "current_ppn_type": r.ppn_type or "included",
                    "current_buy_price": r.buy_price or 0,
                }
                for r in berbeda
            ],
        }

    # tarif default supplier (0/kosong → 11)
    default_rate = float(supplier.PpnSupplier or 0) or 11.0
    for r in berbeda:
        rate = float(r.ppn_percent or 0) or default_rate
        harga = float(r.buy_price or 0)
        if target == "excluded":
            # included → excluded: harga beli naik (PPN ditampilkan terpisah)
            r.buy_price = int(harga * (1 + rate / 100) + 0.5)
        else:
            # excluded → included: harga beli turun (PPN tersembunyi di dalam harga)
            r.buy_price = int(harga / (1 + rate / 100) + 0.5)
        r.ppn_type = target
        r.ppn_percent = rate

    supplier.ppn_type = target
    db.commit()
    return {"updated": len(berbeda)}


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
        phone=s.phone,
        email=s.email,
        address=s.address,
        PpnSupplier=s.PpnSupplier,
        ppn_type=s.ppn_type,
        credit_limit=s.credit_limit,
        due_date=s.due_date,
        is_active=True
    )
    db.add(obj)

    # 3. Hubungkan Item tanpa filter is_active agar tidak ada barang yang "nyangkut"
    if s.item_ids:
        default_ppn = s.ppn_type or "included"
        for iid in s.item_ids:
            db.add(models.ItemSupplier(
                supplier=obj,
                item_id=iid,
                ppn_type=default_ppn
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
        # Sinkronkan tautan item TANPA menghapus baris yang masih dipilih, supaya
        # harga beli khusus & setelan PPN per-supplier (buy_price/ppn_type/ppn_percent/barcode)
        # tidak ikut hilang setiap kali supplier disimpan.
        target_ids = set(item_ids)
        existing = {
            r.item_id: r
            for r in db.query(models.ItemSupplier).filter(
                models.ItemSupplier.supplier_id == sid
            ).all()
        }
        # Hapus tautan yang tidak lagi dipilih
        for iid, row in existing.items():
            if iid not in target_ids:
                db.delete(row)
        # Tambah tautan baru saja (pertahankan baris lama beserta atributnya)
        default_ppn = obj.ppn_type or "included"
        for iid in target_ids:
            if iid not in existing:
                db.add(models.ItemSupplier(
                    supplier_id=sid,
                    item_id=iid,
                    ppn_type=default_ppn
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


# ─── PURCHASE HISTORY & VALIDATION FOR RETURNS ────────────────────────────────

@router.get("/{sid}/purchased-items")
def get_supplier_purchased_items(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Mengambil daftar unik item yang PERNAH dibeli dari supplier ini.
    Digunakan untuk validasi retur manual agar user tidak meretur barang yang tidak pernah dibeli.
    """
    items = db.query(models.Item).join(models.PurchaseItem).join(models.Purchase).filter(
        models.Purchase.supplier_id == sid,
        models.Purchase.status.in_(["unpaid", "paid", "partial"])
    ).distinct().all()
    
    return [
        {"id": i.id, "code": i.code, "name": i.name, "barcode": i.barcode} 
        for i in items
    ]


@router.get("/{sid}/items/{iid}/history")
def get_supplier_item_purchase_history(
    sid: int,
    iid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Mengambil riwayat harga beli item tertentu dari supplier tertentu.
    Membantu user menentukan harga retur berdasarkan faktur lama.
    """
    history = db.query(
        models.Purchase.id, # 👈 TAMBAHKAN ID
        models.Purchase.number,
        models.Purchase.date,
        models.PurchaseItem.qty_received,
        models.PurchaseItem.buy_price,
        models.Purchase.tax_percent
    ).join(models.PurchaseItem, models.Purchase.id == models.PurchaseItem.purchase_id).filter(
        models.Purchase.supplier_id == sid,
        models.PurchaseItem.item_id == iid,
        models.Purchase.status.in_(["unpaid", "paid", "partial"])
    ).order_by(models.Purchase.date.desc()).all()
    
    # Hitung qty_available untuk tiap faktur (qty - yang sudah diretur)
    results = []
    from sqlalchemy import func
    for h in history:
        returned_qty = db.query(func.sum(models.PurchaseReturnItem.qty)).join(models.PurchaseReturn).filter(
            models.PurchaseReturn.purchase_id == h.id,
            models.PurchaseReturnItem.item_id == iid
        ).scalar() or 0.0
        
        results.append({
            "purchase_id": h.id,
            "number": h.number,
            "date": h.date.isoformat() if h.date else None,
            "qty": h.qty_received,
            "qty_available": h.qty_received - returned_qty,
            "price": h.buy_price,
            "tax_percent": h.tax_percent
        })
    
    return results
