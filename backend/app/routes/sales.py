from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date
import pytz

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter()

# --- Setup Zona Waktu Lokal (WITA / Bali) ---
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    """Mengambil tanggal akurat berdasarkan zona waktu toko"""
    return datetime.now(WITA).date()

def get_local_datetime():
    """Mengambil tanggal & jam akurat berdasarkan zona waktu toko"""
    return datetime.now(WITA)

def _next_number(db: Session) -> str:
    today = get_local_date()
    prefix = f"INV{today.strftime('%Y%m%d')}"
    
    # Perbaikan pencarian nomor urut terakhir
    last = db.query(models.Sale).filter(models.Sale.number.like(f"{prefix}%")).order_by(models.Sale.id.desc()).first()
    
    if last and len(last.number) >= 4:
        try:
            seq = int(last.number[-4:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
        
    return f"{prefix}{seq:04d}"


@router.get("/", response_model=list[schemas.SaleOut])
def get_sales(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user) # Ganti _ jadi current_user
):
    q = db.query(models.Sale)
    if start_date: q = q.filter(models.Sale.date >= start_date)
    if end_date: q = q.filter(models.Sale.date <= end_date)
    if customer_id: q = q.filter(models.Sale.customer_id == customer_id)
    if status: q = q.filter(models.Sale.status == status)
    return q.order_by(models.Sale.id.desc()).offset(skip).limit(limit).all()


@router.get("/{sid}", response_model=schemas.SaleOut)
def get_sale(sid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Sale).get(sid)
    if not obj: raise HTTPException(404, "Penjualan tidak ditemukan")
    return obj


@router.post("/", response_model=schemas.SaleOut)
def create_sale(
    data: schemas.SaleCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user) # Tangkap data kasir yg login
):
    # 1. CEK SHIFT AKTIF (Cegah Transaksi Yatim)
    active_shift = db.query(models.Shift).filter(
        models.Shift.user_id == current_user.id,
        models.Shift.status == "open"
    ).first()

    if not active_shift:
        raise HTTPException(status_code=400, detail="Shift Kasir belum dibuka! Buka Shift terlebih dahulu sebelum berjualan.")

    # 2. KUNCI WAKTU LOKAL (Cegah Beda Hari)
    local_date = get_local_date()
    local_datetime = get_local_datetime()

    number = data.number or _next_number(db)

    # Validate stock
    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        if not item: raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        if item.stock < it.qty:
            raise HTTPException(400, f"Stok {item.name} tidak cukup ({item.stock} tersedia)")

    subtotal = sum((it.sell_price * (1 - it.discount / 100)) * it.qty for it in data.items)
    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount
    change = max(0, data.paid - total)
    status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    # Apply customer group discount
    if data.customer_id:
        cust = db.query(models.Customer).get(data.customer_id)
        if cust and cust.group:
            grp_disc = cust.group.discount_percent
            if grp_disc > 0 and data.discount == 0:
                disc_amount = subtotal * (grp_disc / 100)
                total = subtotal - disc_amount + tax_amount
                change = max(0, data.paid - total)

    # 3. SIMPAN KE DATABASE DENGAN SHIFT_ID
    sale = models.Sale(
        number=number, 
        date=local_date, # Waktu lokal paksa timpa data dari frontend
        created_at=local_datetime,
        created_by=current_user.id,
        shift_id=active_shift.id, # <--- INI OBATNYA
        customer_id=data.customer_id, 
        salesperson_id=data.salesperson_id,
        subtotal=subtotal, 
        discount=disc_amount,
        tax=tax_amount, 
        total=total,
        paid=data.paid, 
        change=change,
        payment_method=data.payment_method,
        status=status, 
        notes=data.notes
    )
    db.add(sale)
    db.flush() # Eksekusi agar ID sale tercipta

    # 4. SIMPAN ITEM DAN POTONG STOK
    for it in data.items:
        item = db.query(models.Item).get(it.item_id)
        line_total = (it.sell_price * (1 - it.discount / 100)) * it.qty
        
        db.add(models.SaleItem(
            sale_id=sale.id, item_id=it.item_id,
            qty=it.qty, sell_price=it.sell_price,
            discount=it.discount, total=line_total
        ))
        
        before = item.stock
        item.stock -= it.qty
        
        # Track mutasi stok (menggunakan jam lokal juga)
        db.add(models.StockMovement(
            date=local_date, 
            created_at=local_datetime,
            item_id=item.id,
            type="out", 
            qty=it.qty,
            qty_before=before, 
            qty_after=item.stock,
            reference=number, 
            notes="Penjualan"
        ))
        
    # 5. POIN PELANGGAN
    if data.customer_id:
        cust = db.query(models.Customer).get(data.customer_id)
        if cust:
            cust.points += int(total / 1000)

    db.commit()
    db.refresh(sale)
    return sale


@router.delete("/{sid}")
def delete_sale(sid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Sale).get(sid)
    if not obj: raise HTTPException(404, "Penjualan tidak ditemukan")
    
    # Restore stock
    for it in obj.items:
        item = db.query(models.Item).get(it.item_id)
        if item: item.stock += it.qty
        
    db.delete(obj)
    db.commit()
    return {"message": "Penjualan dihapus"}