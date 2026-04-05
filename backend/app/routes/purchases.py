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
    return datetime.now(WITA).date()

def get_local_datetime():
    return datetime.now(WITA)


def _next_number(db: Session, prefix: str, model) -> str:
    today = get_local_date()
    prefix_full = f"{prefix}{today.strftime('%Y%m%d')}"
    
    last = db.query(model).filter(model.number.like(f"{prefix_full}%")).order_by(model.id.desc()).first()
    
    if last and len(last.number) >= 4:
        try:
            seq = int(last.number[-4:]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
        
    return f"{prefix_full}{seq:04d}"


@router.get("/", response_model=list[schemas.PurchaseOut])
def get_purchases(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    supplier_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    q = db.query(models.Purchase)
    if start_date: q = q.filter(models.Purchase.date >= start_date)
    if end_date: q = q.filter(models.Purchase.date <= end_date)
    if supplier_id: q = q.filter(models.Purchase.supplier_id == supplier_id)
    if status: q = q.filter(models.Purchase.status == status)
    return q.order_by(models.Purchase.id.desc()).offset(skip).limit(limit).all()


@router.get("/{pid}", response_model=schemas.PurchaseOut)
def get_purchase(pid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = db.query(models.Purchase).get(pid)
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    return obj

@router.post("/")
def create_purchase(
    data: schemas.PurchaseCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    # 1. SETUP WAKTU & NOMOR (WITA)
    from .sales import get_local_date, get_local_datetime # Re-use fungsi yang sudah ada
    local_date = get_local_date()
    local_datetime = get_local_datetime()
    
    # Gunakan fungsi penomoran purchase Anda (asumsi: _next_number)
    number = data.number or _next_number(db, "PUR", models.Purchase)

    # 2. HITUNG TOTAL BELI
    subtotal = sum(it.buy_price * it.qty for it in data.items)
    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount
    
    # Tentukan Status (Lunas / Hutang / Parsial)
    status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    # 3. SIMPAN HEADER PEMBELIAN
    purchase = models.Purchase(
        number=number,
        date=local_date,
        created_at=local_datetime,
        supplier_id=data.supplier_id,
        subtotal=subtotal,
        discount=disc_amount,
        tax=tax_amount,
        total=total,
        paid=data.paid,
        status=status,
        notes=data.notes,
        created_by=current_user.id
    )
    db.add(purchase)
    db.flush()

    # 4. SIMPAN ITEM & UPDATE STOK (DENGAN LOCK)
    for it in data.items:
        # Lock item agar stok tidak bentrok
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if not item:
            raise HTTPException(404, f"Barang ID {it.item_id} tidak ada")

        line_total = it.buy_price * it.qty
        db.add(models.PurchaseItem(
            purchase_id=purchase.id,
            item_id=it.item_id,
            qty=it.qty,
            buy_price=it.buy_price,
            total=line_total
        ))

        # Update Stok (Pembelian = Stok Bertambah)
        before = item.stock
        item.stock += it.qty
        
        # Update Harga Beli Terakhir di Master Barang (Penting untuk HPP nanti)
        item.buy_price = it.buy_price

        # Catat Mutasi Stok
        db.add(models.StockMovement(
            date=local_date,
            created_at=local_datetime,
            item_id=item.id,
            type="in",
            qty=it.qty,
            qty_before=before,
            qty_after=item.stock,
            reference=number,
            notes=f"Pembelian dari Supplier"
        ))

    # 5. AUTO-JOURNAL PEMBELIAN (AKRUAL)
    from .accounting import create_auto_journal
    
    supplier = db.query(models.Supplier).get(data.supplier_id)
    supplier_name = supplier.name if supplier else "Supplier"
    
    jurnal_entries = []
    
    # A. Persediaan Barang Bertambah (DEBIT) - Sebesar total nilai barang
    jurnal_entries.append({"code": "1-1400", "debit": total, "credit": 0})
    
    # B. Kas Berkurang (KREDIT) - Sebesar yang dibayar tunai (DP/Lunas)
    if data.paid > 0:
        jurnal_entries.append({"code": "1-1100", "debit": 0, "credit": data.paid})
    
    # C. Hutang Usaha Bertambah (KREDIT) - Sebesar sisa yang belum dibayar
    sisa_hutang = total - data.paid
    if sisa_hutang > 0:
        jurnal_entries.append({"code": "2-1100", "debit": 0, "credit": sisa_hutang})

    # Eksekusi Jurnal
    create_auto_journal(
        db=db,
        date_val=local_date,
        number_ref=number,
        description=f"Pembelian {number} - {supplier_name}",
        entries=jurnal_entries,
        user_id=current_user.id
    )

    db.commit()
    db.refresh(purchase)
    return purchase

@router.post("/{pid}/pay")
def pay_purchase(
    pid: int, 
    payment: schemas.PurchasePayment, # Menggunakan schema Anda
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    # Lock data agar tidak terjadi double payment di detik yang sama
    obj = db.query(models.Purchase).with_for_update().get(pid)
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    
    # 🛡️ --- SATPAM MULAI DI SINI --- 🛡️
    
    # 1. Tolak jika faktur sudah dibatalkan
    if obj.status == "cancelled":
        raise HTTPException(status_code=400, detail="DITOLAK: Tidak bisa membayar faktur yang sudah dibatalkan!")
    
    # 2. Tolak jika faktur sudah lunas
    if obj.status == "paid":
        raise HTTPException(status_code=400, detail="DITOLAK: Faktur ini sudah lunas sepenuhnya!")
        
    # 3. Tolak jika nominal bayar melebihi sisa hutang (Mencegah Kasir Salah Ketik)
    sisa_hutang = obj.total - obj.paid
    if payment.amount > sisa_hutang:
        raise HTTPException(
            status_code=400, 
            detail=f"DITOLAK: Jumlah bayar (Rp {payment.amount:,.0f}) melebihi sisa hutang (Rp {sisa_hutang:,.0f})!"
        )
        
    # 🛡️ --- SATPAM SELESAI --- 🛡️

    # 1. Update Akumulasi Pembayaran
    # Misal: paid lama 2jt + cicilan baru 3jt = total paid 5jt
    obj.paid += payment.amount
    
    # 2. Update Status Berdasarkan Total
    if obj.paid >= obj.total:
        obj.status = "paid"
    else:
        obj.status = "partial"

    # 3. Catat di Jurnal (Hutang berkurang, Kas berkurang)
    from .accounting import create_auto_journal
    
    # Gunakan notes dari schema Anda jika ada, jika tidak pakai default
    catatan = payment.notes or f"Cicilan Hutang untuk {obj.number}"
    
    jurnal_pembayaran = [
        {"code": "2-1100", "debit": payment.amount, "credit": 0}, # Hutang Berkurang
        {"code": "1-1100", "debit": 0, "credit": payment.amount}  # Kas Berkurang
    ]
    
    create_auto_journal(
        db=db,
        date_val=get_local_date(),
        number_ref=obj.number,
        description=f"PAY: {obj.number} - {catatan}",
        entries=jurnal_pembayaran,
        user_id=current_user.id
    )
        
    db.commit()
    
    return {
        "message": "Pembayaran berhasil dicatat", 
        "current_paid": obj.paid, 
        "remaining": obj.total - obj.paid,
        "status": obj.status
    }



@router.post("/{pid}/cancel")
def cancel_purchase(
    pid: int, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    # 1. AMBIL DATA DENGAN LOCK (with_for_update)
    # Ini penting agar tidak ada dua orang yang membatalkan faktur yang sama di saat bersamaan
    obj = db.query(models.Purchase).with_for_update().get(pid)
    
    if not obj: 
        raise HTTPException(404, "Pembelian tidak ditemukan")
    if obj.status == "cancelled":
        raise HTTPException(400, "Faktur pembelian ini sudah dibatalkan sebelumnya")

    # Ambil data waktu lokal (WITA)
    local_date = get_local_date()
    local_datetime = get_local_datetime()

    # 2. PROSES PENGEMBALIAN STOK (REVERSING STOCK)
    for it in obj.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if item:
            before = item.stock
            # Karena batal BELI, maka barang yang masuk harus KELUAR lagi ke supplier
            item.stock -= it.qty 
            
            # Catat mutasi stok sebagai "out" (keluar)
            db.add(models.StockMovement(
                date=local_date, 
                created_at=local_datetime,
                item_id=item.id,
                type="out", 
                qty=it.qty,
                qty_before=before, 
                qty_after=item.stock,
                reference=obj.number, 
                notes=f"Pembatalan Pembelian {obj.number}"
            ))

    # 3. PROSES JURNAL PEMBALIK (REVERSING JOURNAL)
    # Menghapus catatan aset dan hutang di buku besar akuntansi
    from .accounting import create_auto_journal
    
    sisa_hutang = obj.total - obj.paid
    jurnal_entries = []

    # A. Persediaan Barang Berkurang (KREDIT)
    # Kita keluarkan kembali nilai aset yang tadi sempat masuk
    jurnal_entries.append({"code": "1-1400", "debit": 0, "credit": obj.total})

    # B. Kas Kembali / Refund (DEBIT)
    # Jika sudah ada uang yang sempat dibayar (DP/Lunas), maka uang itu dianggap balik ke laci
    if obj.paid > 0:
        jurnal_entries.append({"code": "1-1100", "debit": obj.paid, "credit": 0})

    # C. Hutang Usaha Dibatalkan (DEBIT)
    # Jika masih ada sisa hutang, maka sisa tersebut dihapus/nol-kan
    if sisa_hutang > 0:
        jurnal_entries.append({"code": "2-1100", "debit": sisa_hutang, "credit": 0})

    # Kirim ke sistem Akuntansi
    create_auto_journal(
        db=db,
        date_val=local_date,
        number_ref=obj.number,
        description=f"PEMBATALAN PEMBELIAN: {obj.number}",
        entries=jurnal_entries,
        user_id=current_user.id
    )

    # 4. UPDATE STATUS & AUDIT LOG
    obj.status = "cancelled"
    
    from ..auth import write_audit
    write_audit(
        db, 
        current_user.id, 
        "CANCEL", 
        "purchases", 
        obj.id, 
        f"Membatalkan Pembelian {obj.number} (Total: {obj.total})"
    )

    db.commit()
    return {
        "message": "Pembelian dibatalkan. Stok dikurangi dan Jurnal Akuntansi telah dibalik.",
        "number": obj.number,
        "status": obj.status
    }