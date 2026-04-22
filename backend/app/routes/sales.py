from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date
import pytz
import unicodedata
import textwrap  # 👈 Tambahkan baris ini


from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, get_query

router = APIRouter()

# --- Setup Zona Waktu Lokal (WITA / Bali) ---
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    """Mengambil tanggal akurat berdasarkan zona waktu toko"""
    return datetime.now(WITA).date()

def get_local_datetime():
    """Mengambil tanggal & jam akurat berdasarkan zona waktu toko"""
    return datetime.now(WITA)


def _next_number(db: Session, current_user: models.User) -> str:
    today = get_local_date()
    
    # Selipkan ID Cabang ke dalam Prefix!
    # Jika active_branch_id ada isinya, pakai itu. Jika kosong (Pusat), pakai 0.
    cabang_id = current_user.active_branch_id or 0 
    
    # Prefix baru: INV-C1-20260413 (C1 = Cabang 1)
    prefix = f"INV-C{cabang_id}-{today.strftime('%Y%m%d')}"
    
    # Gunakan get_query agar sistem mencari nomor terakhir di cabang yang aktif saja
    last = get_query(db, models.Sale, current_user).filter(
        models.Sale.number.like(f"{prefix}%")
    ).order_by(models.Sale.id.desc()).first()
    
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
    current_user: models.User = Depends(get_current_user)
):
    # Gunakan get_query pengganti db.query
    q = get_query(db, models.Sale, current_user)
    
    if start_date: q = q.filter(models.Sale.date >= start_date)
    if end_date: q = q.filter(models.Sale.date <= end_date)
    if customer_id: q = q.filter(models.Sale.customer_id == customer_id)
    if status: q = q.filter(models.Sale.status == status)
    
    return q.order_by(models.Sale.id.desc()).offset(skip).limit(limit).all()


@router.get("/{sid}", response_model=schemas.SaleOut)
def get_sale(sid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = get_query(db, models.Sale, current_user).filter(models.Sale.id == sid).first()
    if not obj: raise HTTPException(404, "Penjualan tidak ditemukan")
    return obj


@router.post("/")
def create_sale(
    data: schemas.SaleCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    local_date = get_local_date()
    local_datetime = get_local_datetime()

    # Cek Shift Kasir
    active_shift = get_query(db, models.Shift, current_user).filter(
        models.Shift.user_id == current_user.id, 
        models.Shift.status == "open"
    ).first()
    
    if not active_shift:
        raise HTTPException(400, "Anda belum membuka shift kasir hari ini.")

    # PENOMORAN & KALKULASI
    number = data.number or _next_number(db, current_user)

    subtotal = sum((it.sell_price * (1 - it.discount / 100)) * it.qty for it in data.items)
    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount
    change = max(0, data.paid - total)
    status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    # SIMPAN HEADER
    sale = models.Sale(
        number=number, 
        date=local_date, 
        branch_id=current_user.active_branch_id,
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
    db.flush() 

    # 👇 PERBAIKAN: CEK GUDANG (Pastikan jualan HANYA memotong Gudang Utama/Default cabang)
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id,
        models.Warehouse.is_default == True  # ✅ KUNCI AGAR TIDAK MENGAMBIL DARI GUDANG PENYIMPANAN
    ).first()

    total_hpp = 0.0
    receipt_lines = []  # tampung nama item untuk print

    # LOOP ITEM
    for it in data.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if not item:
            raise HTTPException(404, f"Item {it.item_id} tidak ditemukan")

        # VALIDASI STOK
        if gudang_aktif:
            from .warehouse import get_warehouse_stock, adjust_warehouse_stock
            stok_lokal = get_warehouse_stock(db, gudang_aktif.id, item.id)
            if stok_lokal < it.qty:
                raise HTTPException(400, f"Stok {item.name} di Gudang Etalase tidak cukup (Sisa: {stok_lokal})")
            adjust_warehouse_stock(db, gudang_aktif.id, item.id, -it.qty)
        else:
            if item.stock < it.qty:
                raise HTTPException(400, f"Stok {item.name} tidak cukup (Sisa: {item.stock})")

        line_total = (it.sell_price * (1 - it.discount / 100)) * it.qty
        current_buy_price = item.buy_price or 0
        total_hpp += (current_buy_price * it.qty)

        # SIMPAN ITEM
        db.add(models.SaleItem(
            sale_id=sale.id, 
            item_id=it.item_id,
            qty=it.qty, 
            buy_price=current_buy_price, 
            sell_price=it.sell_price,
            discount=it.discount, 
            total=line_total
        ))

        # POTONG STOK
        before = item.stock
        item.stock -= it.qty

        db.add(models.StockMovement(
            date=local_date, 
            created_at=local_datetime,
            item_id=item.id,
            branch_id=current_user.active_branch_id, 
            type="out", 
            qty=it.qty,
            qty_before=before, 
            qty_after=item.stock,
            reference=number, 
            notes="Penjualan Kasir"
        ))

        # FORMAT STRUK (RAPI)
        name = item.name[:20]  
        line = f"{name}\n"
        line += f"  {it.qty} x {int(it.sell_price):,} = {int(line_total):,}\n"
        receipt_lines.append(line)

    # CUSTOMER POINT
    if data.customer_id:
        cust = db.query(models.Customer).with_for_update().get(data.customer_id)
        if cust:
            cust.points += int(total / 1000)

    # AUTO JOURNAL
    if sale.status == "paid":
        from .accounting import create_auto_journal
        
        jurnal_entries = [
            {"code": "1-1100", "debit": total, "credit": 0},
            {"code": "4-1100", "debit": 0, "credit": total},
            {"code": "5-1100", "debit": total_hpp, "credit": 0},
            {"code": "1-1400", "debit": 0, "credit": total_hpp}
        ]
        
        create_auto_journal(
            db=db, 
            date_val=local_date, 
            number_ref=number, 
            description=f"Penjualan Kasir {number}", 
            entries=jurnal_entries, 
            user_id=current_user.id,
            branch_id=current_user.active_branch_id
        )
    # COMMIT
    db.commit()
    db.refresh(sale)

    return sale

@router.post("/{sid}/cancel")
def cancel_sale(sid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = get_query(db, models.Sale, current_user).filter(models.Sale.id == sid).with_for_update().first()
    
    if not obj: 
        raise HTTPException(404, "Penjualan tidak ditemukan")
    if obj.status == "cancelled":
        raise HTTPException(400, "Faktur penjualan ini sudah dibatalkan sebelumnya")

    local_date = get_local_date()
    local_datetime = get_local_datetime()

    # 👇 PERBAIKAN: CEK GUDANG DARI CABANG ASAL FAKTUR (Pastikan kembali ke Gudang Utama/Default)
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == obj.branch_id,
        models.Warehouse.is_default == True  # ✅ KUNCI AGAR BARANG BATAL KEMBALI KE GUDANG ETALASE
    ).first()

    # PROSES PENGEMBALIAN STOK (REVERSING STOCK)
    for it in obj.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if item:
            before = item.stock
            
            # Tambah kembali stok Global
            item.stock += it.qty  
            
            # 🔄 Tambah kembali stok Lokal (Gudang Etalase Cabang Asal)
            if gudang_aktif:
                from .warehouse import adjust_warehouse_stock
                adjust_warehouse_stock(db, gudang_aktif.id, item.id, it.qty)

            # Catat mutasi masuk ke tabel StockMovement di Cabang Asal
            db.add(models.StockMovement(
                date=local_date, 
                created_at=local_datetime,
                item_id=item.id,
                branch_id=obj.branch_id,
                type="in", 
                qty=it.qty,
                qty_before=before, 
                qty_after=item.stock,
                reference=obj.number, 
                notes=f"Batal Penjualan {obj.number}"
            ))

    # TARIK KEMBALI POIN PELANGGAN
    if obj.customer_id:
        cust = db.query(models.Customer).with_for_update().get(obj.customer_id)
        if cust:
            poin_dibatalkan = int(obj.total / 1000)
            cust.points -= poin_dibatalkan
            if cust.points < 0: 
                cust.points = 0

    # PROSES JURNAL PEMBALIK (REVERSING JOURNAL) AKUNTANSI
    from .accounting import create_auto_journal
    
    total_hpp = sum((it.buy_price * it.qty) for it in obj.items)
    
    jurnal_pembalik = [
        {"code": "1-1100", "debit": 0, "credit": obj.total},      
        {"code": "4-1100", "debit": obj.total, "credit": 0},      
        {"code": "5-1100", "debit": 0, "credit": total_hpp},      
        {"code": "1-1400", "debit": total_hpp, "credit": 0}       
    ]
    
    create_auto_journal(
        db=db, 
        date_val=local_date, 
        number_ref=obj.number, 
        description=f"Batal Penjualan Kasir {obj.number}", 
        entries=jurnal_pembalik, 
        user_id=current_user.id,
        branch_id=obj.branch_id 
    )

    # UBAH STATUS FAKTUR & CATAT AUDIT LOG
    obj.status = "cancelled"

    try:
        from ..auth import write_audit
        write_audit(
            db, 
            current_user.id, 
            "CANCEL", 
            "sales", 
            obj.id, 
            f"Membatalkan faktur penjualan {obj.number} (Total: {obj.total})"
        )
    except: pass # Abaikan jika gagal nulis audit

    db.commit()
    return {
        "message": "Faktur penjualan berhasil dibatalkan. Stok dikembalikan dan poin ditarik.",
        "number": obj.number,
        "status": obj.status
    }

import unicodedata

# Taruh fungsi ini di bagian atas sales.py, di luar route manapun
def printer_safe(text: str, max_len: int = None) -> str:
    """
    Konversi string agar aman untuk printer thermal ESC/POS.
    - Hapus non-breaking space
    - Normalisasi Unicode: ó→o, é→e, ã→a, dst (bukan diganti '?')
    - Karakter yang benar-benar tidak bisa dikonversi → dihapus (bukan '?')
    """
    if not text:
        return ""
    text = str(text).replace('\xa0', ' ')
    # NFKD decompose dulu: "ó" jadi "o" + combining accent
    # lalu encode ascii ignore: buang accent-nya, sisakan huruf dasarnya
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', errors='ignore').decode('ascii')
    text = text.strip()
    if max_len:
        text = text[:max_len]
    return text


import unicodedata
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime
import pytz

# ... (Pastikan import lain dan kode di atasnya tetap ada) ...

def printer_safe(text: str, max_len: int = None) -> str:
    """
    Konversi string agar aman untuk printer thermal ESC/POS.
    - Hapus non-breaking space
    - Normalisasi Unicode: ó→o, é→e, ã→a, dst (bukan diganti '?')
    - Karakter yang benar-benar tidak bisa dikonversi → dihapus (bukan '?')
    """
    if not text:
        return ""
    text = str(text).replace('\xa0', ' ')
    # NFKD decompose dulu: "ó" jadi "o" + combining accent
    # lalu encode ascii ignore: buang accent-nya, sisakan huruf dasarnya
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', errors='ignore').decode('ascii')
    text = text.strip()
    if max_len:
        text = text[:max_len]
    return text


import unicodedata
import textwrap
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime
import pytz

# ... (Pastikan import lain dan kode di atasnya tetap ada) ...

def printer_safe(text: str, max_len: int = None) -> str:
    """
    Konversi string agar aman untuk printer thermal ESC/POS.
    - Hapus non-breaking space
    - Normalisasi Unicode: ó→o, é→e, ã→a, dst (bukan diganti '?')
    - Karakter yang benar-benar tidak bisa dikonversi → dihapus (bukan '?')
    """
    if not text:
        return ""
    text = str(text).replace('\xa0', ' ')
    # NFKD decompose dulu: "ó" jadi "o" + combining accent
    # lalu encode ascii ignore: buang accent-nya, sisakan huruf dasarnya
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', errors='ignore').decode('ascii')
    text = text.strip()
    if max_len:
        text = text[:max_len]
    return text


@router.post("/print/{sale_id}")
async def print_receipt_api(
    sale_id: int,
    request: Request,
    db: Session = Depends(get_db) 
):
    try:
        try:
            data = await request.json()
        except:
            data = {}
            
        settings_toko = data.get("settings", {})

        sale = db.query(models.Sale).get(sale_id)
        if not sale:
            return JSONResponse(status_code=404, content={"detail": "Transaksi tidak ditemukan"})

        if not sale.branch_id:
            return JSONResponse(
                status_code=400,
                content={"detail": "Branch ID tidak valid di transaksi ini"}
            )

        branch_id = sale.branch_id
        
        # ════════════════════════════════════════════════════════════════
        # HELPER FORMATTER 
        # ════════════════════════════════════════════════════════════════
        def format_rp(val):
            try: return f"{int(float(val)):,}".replace(",", ".") + ",00"
            except: return "0,00"

        def format_qty(val):
            try:
                v = float(val)
                return f"{int(v)},00" if v.is_integer() else f"{v}".replace(".", ",")
            except: return "0,00"

        # 👇 PERBAIKAN MARGIN: Lebar setruk disesuaikan menjadi 48 karakter
        W = 48 
        def lr(left, right):
            spaces = W - len(left) - len(right)
            if spaces < 1: spaces = 1
            return f"{left}{' ' * spaces}{right}\n"

        # ════════════════════════════════════════════════════════════════
        # PARSING HEADER & FOOTER
        # ════════════════════════════════════════════════════════════════
        nama_toko = printer_safe(settings_toko.get("storeName", "TEJA CAHAYA")).upper()
        alamat = printer_safe(settings_toko.get("storeAddr", "Banjar Dinas Desa, Desa Bebetin\nBULELENG\nTelp: 082266365673 Wa: 082266365673 Email:\nevasudayana27@gmail.com"))
        footer = printer_safe(settings_toko.get("storeFooter", "Terima Kasih telah berbelanja di toko kami."))

        try: parsed_date = sale.date.strftime("%d-%m-%Y") if hasattr(sale.date, 'strftime') else str(sale.date)
        except: parsed_date = datetime.now(WITA).strftime("%d-%m-%Y")
            
        try: time_str = sale.created_at.strftime("%H:%M:%S") if hasattr(sale.created_at, 'strftime') else datetime.now(WITA).strftime("%H:%M:%S")
        except: time_str = "-"
            
        no_str = str(sale.number)
        
        kasir = "ADMIN"
        if getattr(sale, 'created_by', None):
            user = db.query(models.User).filter(models.User.id == sale.created_by).first()
            if user: kasir = printer_safe(user.username).upper()
                
        pelanggan = "UMUM"
        if getattr(sale, 'customer_id', None):
            cust = db.query(models.Customer).filter(models.Customer.id == sale.customer_id).first()
            if cust: pelanggan = printer_safe(cust.name).upper()
                
        payment = str(getattr(sale, 'payment_method', 'CASH')).upper()

        # ════════════════════════════════════════════════════════════════
        # MERAKIT STRING STRUK
        # ════════════════════════════════════════════════════════════════
        struk = ""
        # ESC a 1 (\x31) Rata Tengah | GS ! 11 (\x11) Huruf Besar
        struk += "\x1B\x61\x01\x1D\x21\x11" 
        struk += f"{nama_toko}\n\n"
        
        # Reset font normal & kiri align
        struk += "\x1D\x21\x00\x1B\x61\x00"
        
        for line in alamat.split('\n'):
            struk += f"{line}\n"
        struk += "\n"

        struk += lr(f"No.  : {no_str}", parsed_date)
        struk += lr(f"Kasir: {kasir}", time_str)
        struk += f"Pel. : {pelanggan}/{payment}\n"
        
        garis = "-" * W
        struk += f"{garis}\n"

        # LOOPING ITEM 
        brs = len(sale.items) if getattr(sale, 'items', None) else 0
        total_qty = 0.0

        if brs > 0:
            for item in sale.items:
                nama_barang = "BARANG"
                unit_name = "PCS"
                if getattr(item, 'item', None):
                    # Ambil nama aman tanpa dipotong
                    raw_nama = printer_safe(item.item.name).upper()
                    
                    # 👇 PERBAIKAN TEXTWRAP: Bungkus teks sesuai lebar kertas (W)
                    wrapped_nama = textwrap.wrap(raw_nama, width=W)
                    nama_barang = "\n".join(wrapped_nama)

                    if getattr(item.item, 'unit', None):
                        unit_name = printer_safe(item.item.unit.name).upper()
                        
                struk += f"{nama_barang}\n"
                
                qty = float(item.qty)
                total_qty += qty
                
                harga_satuan = float(item.sell_price)
                total_line = float(item.total)

                harga_str = format_rp(harga_satuan)
                qty_str = format_qty(qty)
                total_str = format_rp(total_line)

                left_part = f"{harga_str:<14} x {qty_str:<5} {unit_name:<4} ="
                struk += lr(left_part, total_str)

        struk += f"{garis}\n"

        qty_tot_str = format_qty(total_qty)
        total_belanja_str = format_rp(getattr(sale, 'total', 0))
        struk += lr(f"BRS={brs}  , QTY={qty_tot_str}", total_belanja_str)
        
        paid_str = format_rp(getattr(sale, 'paid', 0))
        struk += lr("Tunai    =", paid_str)
        
        # Garis kecil untuk kolom nominal
        struk += f"{'-' * 22:>{W}}\n"
        
        change_str = format_rp(getattr(sale, 'change', 0))
        struk += lr("Kembali  =", change_str)
        
        struk += "\n\x1B\x61\x01" # Rata tengah
        struk += f"{footer}\n\n\n"

        # ════════════════════════════════════════════════════════════════
        # SIMPAN KE PRINT QUEUE 
        # ════════════════════════════════════════════════════════════════
        new_job = models.PrintJob(
            branch_id=branch_id,
            content=struk,
            content_type="raw",
            status="pending"
        )
        db.add(new_job)
        db.commit()

        return {"status": "success", "message": f"Struk masuk ke antrean cetak Cabang {branch_id}!"}

    except Exception as e:
        print(f"🔥 ERROR PRINT FATAL: {str(e)}") 
        return JSONResponse(status_code=500, content={"detail": f"Gagal mencetak: {str(e)}"})