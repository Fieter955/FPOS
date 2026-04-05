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

@router.post("/")
def create_sale(
    data: schemas.SaleCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    # 1. SETUP WAKTU LOKAL (WITA)
    local_date = get_local_date()
    local_datetime = get_local_datetime()

    # Cek Shift Kasir (Sesuai dengan logika kode Anda sebelumnya)
    active_shift = db.query(models.Shift).filter(
        models.Shift.user_id == current_user.id, 
        models.Shift.status == "open"
    ).first()
    if not active_shift:
        raise HTTPException(400, "Anda belum membuka shift kasir hari ini.")

    # 2. PENOMORAN & KALKULASI TOTAL
    number = data.number or _next_number(db)  # Generate nomor faktur jika tidak disediakan

    subtotal = sum((it.sell_price * (1 - it.discount / 100)) * it.qty for it in data.items)
    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount
    change = max(0, data.paid - total)
    status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    # 3. SIMPAN HEADER PENJUALAN
    sale = models.Sale(
        number=number, 
        date=local_date, 
        created_at=local_datetime,
        created_by=current_user.id,
        shift_id=active_shift.id, 
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

    # Variabel penampung total HPP (Harga Modal)
    total_hpp = 0.0

    # 4. SIMPAN ITEM, HITUNG HPP DATABASE, & POTONG STOK (DENGAN LOCK)
    for it in data.items:
        # LOCK AKTIF: Mencegah kasir lain memotong stok di milidetik yang sama
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if not item:
            raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")
        if item.stock < it.qty:
            raise HTTPException(400, f"Stok {item.name} tidak cukup ({item.stock} tersedia)")

        line_total = (it.sell_price * (1 - it.discount / 100)) * it.qty
        
        # Ambil buy_price dari master data (Aman dari manipulasi frontend)
        current_buy_price = item.buy_price or 0
        total_hpp += (current_buy_price * it.qty)
        
        # Simpan SaleItem (Kunci HPP Historis agar Laba/Rugi tidak amnesia)
        db.add(models.SaleItem(
            sale_id=sale.id, 
            item_id=it.item_id,
            qty=it.qty, 
            buy_price=current_buy_price, 
            sell_price=it.sell_price,
            discount=it.discount, 
            total=line_total
        ))
        
        # Potong Stok
        before = item.stock
        item.stock -= it.qty
        
        # Track mutasi stok (menggunakan jam lokal)
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
        
    # 5. UPDATE POIN PELANGGAN (DENGAN LOCK)
    if data.customer_id:
        cust = db.query(models.Customer).with_for_update().get(data.customer_id)
        if cust:
            cust.points += int(total / 1000)

    # 6. AUTO-JOURNAL AKUNTANSI (HANYA JIKA LUNAS)
    if sale.status == "paid":
        from .accounting import create_auto_journal
        

        jurnal_entries = [
            {"code": "1-1100", "debit": total, "credit": 0},      # Kas bertambah
            {"code": "4-1100", "debit": 0, "credit": total},      # Pendapatan bertambah
            {"code": "5-1100", "debit": total_hpp, "credit": 0},  # Beban HPP bertambah
            {"code": "1-1400", "debit": 0, "credit": total_hpp}   # Persediaan berkurang
        ]
        
        create_auto_journal(
            db=db, 
            date_val=local_date, 
            number_ref=number, 
            description=f"Penjualan Kasir {number}", 
            entries=jurnal_entries, 
            user_id=current_user.id
        )

    # 7. COMMIT FINAL
    db.commit()
    db.refresh(sale)
    return sale


@router.post("/{sid}/cancel")
def cancel_sale(sid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Sale).get(sid)
    if not obj: 
        raise HTTPException(404, "Penjualan tidak ditemukan")
    if obj.status == "cancelled":
        raise HTTPException(400, "Faktur ini sudah dibatalkan sebelumnya")

    # 1. Kembalikan Stok Barang ke Toko (Reversing Stock)
    for it in obj.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if item:
            before = item.stock
            item.stock += it.qty  # Tambah kembali stoknya
            
            # Catat mutasi masuk
            db.add(models.StockMovement(
                date=get_local_date(), 
                created_at=get_local_datetime(),
                item_id=item.id,
                type="in", # Masuk kembali
                qty=it.qty,
                qty_before=before, 
                qty_after=item.stock,
                reference=obj.number, 
                notes="Batal Penjualan"
            ))

    # 2. Tarik Kembali Poin Pelanggan (Jika ada pelanggan)
    if obj.customer_id:
        cust = db.query(models.Customer).get(obj.customer_id)
        if cust:
            poin_dibatalkan = int(obj.total / 1000)
            cust.points -= poin_dibatalkan
            if cust.points < 0: cust.points = 0

    # 3. Ubah Status Faktur (Soft Delete)
    obj.status = "cancelled"

    from .accounting import create_auto_journal
    
    # Hitung ulang modal untuk dibalik (opsional, jika Anda menyimpan total_hpp di database, panggil saja dari sana. 
    # Jika tidak, kita hitung dari item yang dibatalkan)
    total_hpp = sum((it.buy_price * it.qty) for it in obj.items)
    
    jurnal_pembalik = [
        {"code": "1-1100", "debit": 0, "credit": obj.total},      # KAS BERKURANG (Uang dikembalikan)
        {"code": "4-1100", "debit": obj.total, "credit": 0},      # PENDAPATAN BERKURANG (Batal untung)
        {"code": "5-1100", "debit": 0, "credit": total_hpp},      # HPP BERKURANG
        {"code": "1-1400", "debit": total_hpp, "credit": 0}       # PERSEDIAAN BERTAMBAH (Barang balik ke gudang)
    ]
    
    create_auto_journal(
        db=db, 
        date_val=get_local_date(), 
        number_ref=obj.number, 
        description=f"Batal Penjualan Kasir {obj.number}", 
        entries=jurnal_pembalik, 
        user_id=current_user.id
    )

    # 4. Catat di Audit Log
    from ..auth import write_audit
    write_audit(db, current_user.id, "UPDATE", "sales", obj.id, f"Membatalkan faktur penjualan {obj.number}")

    db.commit()
    return {"message": "Faktur penjualan berhasil dibatalkan dan stok dikembalikan"}



