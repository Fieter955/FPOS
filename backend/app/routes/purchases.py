from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date
import pytz

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, get_query
from ..services.virtual_units import is_virtual_variant

router = APIRouter()

# --- Setup Zona Waktu Lokal (WITA / Bali) ---
WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    return datetime.now(WITA).date()

def get_local_datetime():
    return datetime.now(WITA)


def _next_number(db: Session, prefix: str, model, current_user: models.User) -> str:
    today = get_local_date()
    
    # 👇 PERBAIKAN 1: Ambil ID Cabang aktif (0 jika akun Pusat)
    cabang_id = current_user.active_branch_id or 0
    
    # 👇 PERBAIKAN 2: Selipkan ID Cabang ke dalam Prefix! 
    # Hasilnya akan menjadi: PUR-C1-20260413 (C1 = Cabang 1)
    prefix_full = f"{prefix}-C{cabang_id}-{today.strftime('%Y%m%d')}"
    
    # 👇 PERBAIKAN: Gunakan db.query langsung, jangan get_query (karena branch_id bisa berubah saat fulfillment)
    # Kita tetap aman karena prefix_full sudah mengandung ID Cabang (C1, C2, dst)
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
    is_branch_request: Optional[bool] = None,
    target_branch_id: Optional[int] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    # Jika User adalah Pusat (ID=1), dan dia tidak sedang memfilter is_branch_request secara spesifik,
    # Maka dia bisa melihat request yang ditujukan padanya.
    q = db.query(models.Purchase)
    
    # Logic filtering:
    # 1. Jika Pusat (branch_id 1 atau active_branch_id 1):
    #    - Bisa lihat semua pembelian miliknya sendiri.
    #    - Bisa lihat request yang target_branch_id == 1.
    # 2. Jika Cabang:
    #    - Hanya lihat pembelian milik sendiri (branch_id == miliknya).
    #    - Hanya lihat request yang dia buat (branch_id == miliknya).
    
    my_branch_id = current_user.active_branch_id
    
    if my_branch_id == 1:
        # Pusat: Pembelian saya ATAU Request ke saya
        q = q.filter((models.Purchase.branch_id == 1) | (models.Purchase.target_branch_id == 1))
    else:
        # Cabang: Pembelian saya ATAU Pembelian yang ditujukan ke saya (fulfillment dari pusat)
        q = q.filter((models.Purchase.branch_id == my_branch_id) | (models.Purchase.target_branch_id == my_branch_id))

    if start_date: q = q.filter(models.Purchase.date >= start_date)
    if end_date: q = q.filter(models.Purchase.date <= end_date)
    if supplier_id: q = q.filter(models.Purchase.supplier_id == supplier_id)
    
    # 👇 PERBAIKAN: Secara default SEMBUNYIKAN status 'pending' (request cabang) 
    # Agar tidak membingungkan di daftar pembelian utama.
    if status: 
        q = q.filter(models.Purchase.status == status)
    else:
        q = q.filter(models.Purchase.status != "pending")
        
    if is_branch_request is not None: q = q.filter(models.Purchase.is_branch_request == is_branch_request)
    if target_branch_id: q = q.filter(models.Purchase.target_branch_id == target_branch_id)
    
    return q.order_by(models.Purchase.id.desc()).offset(skip).limit(limit).all()


@router.get("/items/")
def get_items_for_purchase(
    supplier_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    query = db.query(models.Item).filter(
        models.Item.is_active == True,
        (models.Item.is_virtual_variant == False) | (models.Item.is_virtual_variant == None),
    )
    
    # Ambil list item
    if supplier_id:
        # Filter: Hanya ambil barang yang memang terhubung dengan supplier ini
        query = query.join(models.ItemSupplier).filter(models.ItemSupplier.supplier_id == supplier_id)
    
    items = query.all()
    
    results = []
    for item in items:
        # Serialisasi dasar
        it_dict = {
            "id": item.id,
            "code": item.code,
            "barcode": item.barcode,
            "name": item.name,
            "buy_price": item.buy_price,
            "sell_price": item.sell_price,
            "profit_margin": item.profit_margin,
            "unit_name": item.unit.name if item.unit else "pcs"
        }
        
        # JIKA ADA supplier_id, cari harga khusus dari supplier tersebut
        if supplier_id:
            spec = db.query(models.ItemSupplier).filter(
                models.ItemSupplier.item_id == item.id,
                models.ItemSupplier.supplier_id == supplier_id
            ).first()
            if spec:
                it_dict["buy_price"] = spec.buy_price
                if spec.barcode:
                    it_dict["barcode"] = spec.barcode
        
        results.append(it_dict)
        
    return results


@router.get("/{pid}", response_model=schemas.PurchaseOut)
def get_purchase(pid: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    obj = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).first()
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    return obj


@router.post("/")
def create_purchase(
    data: schemas.PurchaseCreate, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    tanggal_faktur = data.date if data.date else get_local_date()
    local_datetime = get_local_datetime()

    from .accounting import assert_books_open
    assert_books_open(db, current_user.active_branch_id, tanggal_faktur, "Pembelian")
     
    prefix = "REQ" if data.is_branch_request else "PUR"
    number = data.number or _next_number(db, prefix, models.Purchase, current_user)

    subtotal = 0
    for it in data.items:
        harga_neto = (it.buy_price * (1 - (it.disc1/100))) * (1 - (it.disc2/100))
        subtotal += harga_neto * it.qty

    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount
    
    if data.is_branch_request:
        status = "pending"
    elif data.status == "draft":
        status = "draft"
    else:
        status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    purchase = models.Purchase(
        number=number,
        date=tanggal_faktur,
        branch_id=current_user.active_branch_id,
        created_at=local_datetime,
        supplier_id=data.supplier_id,
        subtotal=subtotal,
        discount=disc_amount,
        tax=tax_amount,
        total=total,
        paid=data.paid,
        status=status,
        notes=data.notes,
        created_by=current_user.id,
        is_branch_request=data.is_branch_request,
        target_branch_id=data.target_branch_id
    )
    db.add(purchase)
    db.flush()

    # 👇 JIKA INI ADALAH FULFILLMENT DARI PO / REQUEST CABANG
    if data.from_po_id:
        po_source = db.query(models.Purchase).get(data.from_po_id)
        if po_source:
            po_source.status = "completed"
            db.add(po_source)

    # 👇 CEK GUDANG TUJUAN (Bisa gudang cabang pemesan atau gudang lokal)
    target_bid = data.target_branch_id or current_user.active_branch_id
    is_inter_branch_fulfillment = (current_user.active_branch_id == 1 and data.target_branch_id and data.target_branch_id != 1 and data.from_po_id)
    
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == target_bid
    ).first()

    for it in data.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if not item:
            raise HTTPException(404, f"Barang ID {it.item_id} tidak ada")
        if is_virtual_variant(item):
            raise HTTPException(
                400,
                f"Barang multi-satuan {item.name} tidak boleh dibeli langsung. Beli barang induknya.",
            )

        line_qty = it.qty_received if it.qty_received > 0 else it.qty
        harga_neto = (it.buy_price * (1 - (it.disc1/100))) * (1 - (it.disc2/100))
        line_total = harga_neto * line_qty
        
        db.add(models.PurchaseItem(
            purchase_id=purchase.id,
            item_id=it.item_id,
            qty=line_qty,
            qty_ordered=it.qty_ordered or it.qty,
            qty_received=it.qty_received or line_qty,
            buy_price=it.buy_price,
            disc1=it.disc1,
            disc2=it.disc2,
            discount=it.buy_price - harga_neto,
            total=line_total
        ))

        # Hanya update stok dan harga jika BUKAN draft
        if status != "draft":
            # 👇 CEK STOK SEBELUM (Total Stok Cabang Tujuan)
            from .warehouse import get_total_branch_stock
            qty_before = get_total_branch_stock(db, target_bid, item.id)

            # 👇 UPDATE STOK: Hanya update item.stock jika tujuan adalah PUSAT (ID 1)
            # Jika tujuan adalah CABANG, maka item.stock (Pusat) TIDAK berubah.
            if target_bid == 1:
                item.stock += line_qty
            
            # 🔄 Tambah Stok Lokal Gudang Cabang (Selalu dilakukan ke gudang default cabang tersebut)
            if gudang_aktif:
                from .warehouse import adjust_warehouse_stock
                adjust_warehouse_stock(db, gudang_aktif.id, item.id, line_qty)

            # 🔄 Update / Create Harga Khusus Supplier Otomatis
            spec = db.query(models.ItemSupplier).filter(
                models.ItemSupplier.item_id == item.id,
                models.ItemSupplier.supplier_id == data.supplier_id
            ).first()
            if spec:
                spec.buy_price = it.buy_price
            else:
                db.add(models.ItemSupplier(
                    item_id=item.id,
                    supplier_id=data.supplier_id,
                    buy_price=it.buy_price,
                    barcode=item.barcode 
                ))

            item.buy_price = it.buy_price
            item.sell_price = it.sell_price
            item.profit_margin = it.profit_margin

            db.add(models.StockMovement(
                date=tanggal_faktur,
                created_at=local_datetime,
                item_id=item.id,
                branch_id=target_bid, 
                type="in",
                qty=line_qty,
                qty_before=qty_before,
                qty_after=qty_before + line_qty,
                reference=number,
                notes=f"Pembelian dari Supplier (Fulfillment PO)" if data.from_po_id else "Pembelian dari Supplier"
            ))

    # AUTO-JOURNAL PEMBELIAN (AKRUAL) - HANYA JIKA BUKAN DRAFT
    if status != "draft":
        from .accounting import create_auto_journal
        
        supplier = db.query(models.Supplier).get(data.supplier_id)
        supplier_name = supplier.name if supplier else "Supplier"
        
        if is_inter_branch_fulfillment:
            # 🏢 KASUS KHUSUS: PUSAT BELI UNTUK CABANG (INTER-BRANCH)
            # 1. Jurnal di Pusat: Kirim Barang ke Cabang (Debit) vs Kas/Hutang (Kredit)
            jurnal_pusat = []
            jurnal_pusat.append({"code": "3-2200", "debit": total, "credit": 0})
            if data.paid > 0:
                jurnal_pusat.append({"code": "1-1100", "debit": 0, "credit": data.paid})
            sisa_hutang = total - data.paid
            if sisa_hutang > 0:
                jurnal_pusat.append({"code": "2-1100", "debit": 0, "credit": sisa_hutang})
            
            create_auto_journal(
                db=db, date_val=tanggal_faktur, number_ref=number,
                description=f"Pusat beli untuk Cabang ({target_bid}) - {supplier_name}",
                entries=jurnal_pusat, user_id=current_user.id, branch_id=1
            )

            # 2. Jurnal di Cabang: Persediaan (Debit) vs Transfer dari Pusat (Kredit)
            jurnal_cabang = [
                {"code": "1-1400", "debit": total, "credit": 0},
                {"code": "3-2100", "debit": 0, "credit": total}
            ]
            create_auto_journal(
                db=db, date_val=tanggal_faktur, number_ref=number,
                description=f"Terima Persediaan dari Pusat (Via Supplier {supplier_name})",
                entries=jurnal_cabang, user_id=current_user.id, branch_id=target_bid
            )
        else:
            # 🛒 KASUS NORMAL: BELI SENDIRI
            jurnal_entries = []
            jurnal_entries.append({"code": "1-1400", "debit": total, "credit": 0})
            if data.paid > 0:
                jurnal_entries.append({"code": "1-1100", "debit": 0, "credit": data.paid})
            
            sisa_hutang = total - data.paid
            if sisa_hutang > 0:
                jurnal_entries.append({"code": "2-1100", "debit": 0, "credit": sisa_hutang})

            create_auto_journal(
                db=db,
                date_val=tanggal_faktur,
                number_ref=number,
                description=f"Pembelian {number} - {supplier_name}",
                entries=jurnal_entries,
                user_id=current_user.id,
                branch_id=current_user.active_branch_id
            )

    db.commit()
    db.refresh(purchase)
    return purchase


@router.post("/{pid}/pay")
def pay_purchase(
    pid: int, 
    payment: schemas.PurchasePayment,
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    obj = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).with_for_update().first()
    if not obj: raise HTTPException(404, "Pembelian tidak ditemukan")
    
    if obj.status == "cancelled":
        raise HTTPException(status_code=400, detail="DITOLAK: Tidak bisa membayar faktur yang sudah dibatalkan!")
    if obj.status == "paid":
        raise HTTPException(status_code=400, detail="DITOLAK: Faktur ini sudah lunas sepenuhnya!")
        
    cash_amount = float(payment.cash_amount or 0)
    bank_amount = float(payment.bank_amount or 0)
    legacy_amount = float(payment.amount or 0)

    if cash_amount < 0 or bank_amount < 0 or legacy_amount < 0:
        raise HTTPException(400, "DITOLAK: Nominal pembayaran tidak boleh negatif!")

    if cash_amount <= 0 and bank_amount <= 0 and legacy_amount > 0:
        cash_amount = legacy_amount

    total_payment = cash_amount + bank_amount
    if total_payment <= 0:
        raise HTTPException(400, "DITOLAK: Isi nominal pembayaran kas, bank, atau keduanya.")

    if legacy_amount > 0 and abs(legacy_amount - total_payment) > 0.01:
        raise HTTPException(400, "DITOLAK: Total pembayaran tidak cocok dengan rincian kas/bank.")

    sisa_hutang = obj.total - obj.paid
    if total_payment > sisa_hutang:
        raise HTTPException(
            status_code=400, 
            detail=f"DITOLAK: Jumlah bayar (Rp {total_payment:,.0f}) melebihi sisa hutang (Rp {sisa_hutang:,.0f})!"
        )

    from .accounting import create_auto_journal, get_account_balance

    cash_acc = db.query(models.Account).filter(models.Account.code == "1-1100").first()
    bank_acc = db.query(models.Account).filter(models.Account.code == "1-1200").first()
    branch_id = obj.branch_id or current_user.active_branch_id

    if cash_amount > 0:
        cash_balance = get_account_balance(db, cash_acc.id, branch_id=branch_id) if cash_acc else 0.0
        if cash_amount - cash_balance > 0.01:
            raise HTTPException(
                400,
                f"DITOLAK: Saldo kas tidak cukup. Tersedia Rp {cash_balance:,.0f}, diminta Rp {cash_amount:,.0f}."
            )

    if bank_amount > 0:
        bank_balance = get_account_balance(db, bank_acc.id, branch_id=branch_id) if bank_acc else 0.0
        if bank_amount - bank_balance > 0.01:
            raise HTTPException(
                400,
                f"DITOLAK: Saldo bank tidak cukup. Tersedia Rp {bank_balance:,.0f}, diminta Rp {bank_amount:,.0f}."
            )

    obj.paid += total_payment
    obj.status = "paid" if obj.paid >= obj.total else "partial"

    catatan = payment.notes or f"Cicilan Hutang untuk {obj.number}"
    
    jurnal_pembayaran = [{"code": "2-1100", "debit": total_payment, "credit": 0}]
    if cash_amount > 0:
        jurnal_pembayaran.append({"code": "1-1100", "debit": 0, "credit": cash_amount})
    if bank_amount > 0:
        jurnal_pembayaran.append({"code": "1-1200", "debit": 0, "credit": bank_amount})
    
    create_auto_journal(
        db=db,
        date_val=get_local_date(),
        number_ref=obj.number,
        description=f"PAY: {obj.number} - {catatan}",
        entries=jurnal_pembayaran,
        user_id=current_user.id,
        branch_id=obj.branch_id # ✅ JURNAL PEMBAYARAN MASUK KE CABANG ASAL FAKTUR
    )
        
    db.commit()
    return {
        "message": "Pembayaran berhasil dicatat", 
        "current_paid": obj.paid, 
        "remaining": obj.total - obj.paid,
        "status": obj.status,
        "amount": total_payment,
        "cash_amount": cash_amount,
        "bank_amount": bank_amount,
    }


@router.post("/{pid}/cancel")
def cancel_purchase(
    pid: int, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    obj = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).with_for_update().first()
    
    if not obj: 
        raise HTTPException(404, "Pembelian tidak ditemukan")
    if obj.status == "cancelled":
        raise HTTPException(400, "Faktur pembelian ini sudah dibatalkan sebelumnya")

    local_date = get_local_date()
    local_datetime = get_local_datetime()

    from .accounting import get_books_locked_until
    locked_until = get_books_locked_until(db, obj.branch_id)
    if locked_until and obj.date and obj.date <= locked_until:
        raise HTTPException(
            status_code=400,
            detail=f"DITOLAK: Pembelian tanggal {obj.date} berada di periode tutup buku (sampai {locked_until}). Tidak bisa dibatalkan.",
        )

    # 👇 CARI GUDANG DARI CABANG ASAL FAKTUR PEMBELIAN
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == obj.branch_id
    ).first()

    for it in obj.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if item:
            # 🛡️ VALIDASI: Apakah stok lokal masih cukup untuk dikembalikan ke Supplier?
            if gudang_aktif:
                from .warehouse import get_warehouse_stock, adjust_warehouse_stock
                stok_lokal = get_warehouse_stock(db, gudang_aktif.id, item.id)
                if stok_lokal < it.qty:
                    raise HTTPException(400, f"Gagal dibatalkan! Stok {item.name} di Gudang Cabang ini sudah tidak cukup (Sisa: {stok_lokal}) karena sudah ada yang terjual.")
                
                # Kurangi stok lokal
                adjust_warehouse_stock(db, gudang_aktif.id, item.id, -it.qty)
            else:
                if item.stock < it.qty:
                    raise HTTPException(400, f"Gagal dibatalkan! Stok Global {item.name} tidak cukup karena sudah terjual.")

            before = item.stock
            # Kurangi stok global
            item.stock -= it.qty 
            
            db.add(models.StockMovement(
                date=local_date, 
                created_at=local_datetime,
                item_id=item.id,
                branch_id=obj.branch_id, 
                type="out", 
                qty=it.qty,
                qty_before=before, 
                qty_after=item.stock,
                reference=obj.number, 
                notes=f"Pembatalan Pembelian {obj.number}"
            ))

    from .accounting import create_auto_journal
    
    sisa_hutang = obj.total - obj.paid
    jurnal_entries = []

    jurnal_entries.append({"code": "1-1400", "debit": 0, "credit": obj.total})
    if obj.paid > 0:
        jurnal_entries.append({"code": "1-1100", "debit": obj.paid, "credit": 0})
    if sisa_hutang > 0:
        jurnal_entries.append({"code": "2-1100", "debit": sisa_hutang, "credit": 0})

    create_auto_journal(
        db=db,
        date_val=local_date,
        number_ref=obj.number,
        description=f"PEMBATALAN PEMBELIAN: {obj.number}",
        entries=jurnal_entries,
        user_id=current_user.id,
        branch_id=obj.branch_id # ✅ JURNAL PEMBALIK MASUK KE CABANG ASAL FAKTUR
    )

    obj.status = "cancelled"
    
    try:
        from ..auth import write_audit
        write_audit(
            db, 
            current_user.id, 
            "CANCEL", 
            "purchases", 
            obj.id, 
            f"Membatalkan Pembelian {obj.number} (Total: {obj.total})"
        )
    except: pass

    db.commit()
    return {
        "message": "Pembelian dibatalkan. Stok dikurangi dan Jurnal Akuntansi telah dibalik.",
        "number": obj.number,
        "status": obj.status
    }


@router.put("/{pid}")
def update_purchase(
    pid: int,
    data: schemas.PurchaseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    purchase = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).with_for_update().first()
    if not purchase: raise HTTPException(404, "Pembelian tidak ditemukan")
    
    # 👇 PERBAIKAN: Izinkan status 'pending' agar request dari cabang bisa diproses (finalisasi)
    if purchase.status not in ["draft", "pending"]:
        raise HTTPException(400, f"Faktur dengan status '{purchase.status}' tidak dapat diubah.")

    tanggal_faktur = data.date if data.date else get_local_date()
    local_datetime = get_local_datetime()

    subtotal = 0
    for it in data.items:
        harga_neto = (it.buy_price * (1 - (it.disc1/100))) * (1 - (it.disc2/100))
        subtotal += harga_neto * it.qty

    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount

    if data.status == "draft":
        status = "draft"
    else:
        status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    purchase.date = tanggal_faktur
    purchase.supplier_id = data.supplier_id
    purchase.subtotal = subtotal
    purchase.discount = disc_amount
    purchase.tax = tax_amount
    purchase.total = total
    purchase.paid = data.paid
    purchase.status = status
    purchase.notes = data.notes
    purchase.is_branch_request = data.is_branch_request
    purchase.target_branch_id = data.target_branch_id
    
    # 👇 PINDAH KEPEMILIKAN: Jika diproses oleh cabang lain (misal: Pusat), 
    # maka record ini sekarang menjadi milik cabang yang memprosesnya.
    purchase.branch_id = current_user.active_branch_id

    # Remove old items
    db.query(models.PurchaseItem).filter(models.PurchaseItem.purchase_id == pid).delete()
    
    # 👇 CEK GUDANG TUJUAN (Bisa gudang cabang pemesan atau gudang lokal)
    target_bid = data.target_branch_id or current_user.active_branch_id
    is_inter_branch_fulfillment = (current_user.active_branch_id == 1 and data.target_branch_id and data.target_branch_id != 1 and data.from_po_id)
    
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == target_bid
    ).first()

    for it in data.items:
        item = db.query(models.Item).with_for_update().get(it.item_id)
        if not item:
            raise HTTPException(404, f"Barang ID {it.item_id} tidak ada")

        line_qty = it.qty_received if it.qty_received > 0 else it.qty
        harga_neto = (it.buy_price * (1 - (it.disc1/100))) * (1 - (it.disc2/100))
        line_total = harga_neto * line_qty
        
        db.add(models.PurchaseItem(
            purchase_id=purchase.id,
            item_id=it.item_id,
            qty=line_qty,
            qty_ordered=it.qty_ordered or it.qty,
            qty_received=it.qty_received or line_qty,
            buy_price=it.buy_price,
            disc1=it.disc1,
            disc2=it.disc2,
            discount=it.buy_price - harga_neto,
            total=line_total
        ))

        # Jika sudah BUKAN draft, update stok
        if status != "draft":
            # 👇 CEK STOK SEBELUM (Total Stok Cabang Tujuan)
            from .warehouse import get_total_branch_stock
            qty_before = get_total_branch_stock(db, target_bid, item.id)

            # 👇 UPDATE STOK: Hanya update item.stock jika tujuan adalah PUSAT (ID 1)
            if target_bid == 1:
                item.stock += line_qty
            
            # 🔄 Tambah Stok Lokal Gudang Cabang
            if gudang_aktif:
                from .warehouse import adjust_warehouse_stock
                adjust_warehouse_stock(db, gudang_aktif.id, item.id, line_qty)

            spec = db.query(models.ItemSupplier).filter(
                models.ItemSupplier.item_id == item.id,
                models.ItemSupplier.supplier_id == data.supplier_id
            ).first()
            if spec:
                spec.buy_price = it.buy_price
            else:
                db.add(models.ItemSupplier(
                    item_id=item.id,
                    supplier_id=data.supplier_id,
                    buy_price=it.buy_price,
                    barcode=item.barcode
                ))

            item.buy_price = it.buy_price
            item.sell_price = it.sell_price
            item.profit_margin = it.profit_margin

            db.add(models.StockMovement(
                date=tanggal_faktur,
                created_at=local_datetime,
                item_id=item.id,
                branch_id=target_bid, 
                type="in",
                qty=line_qty,
                qty_before=qty_before,
                qty_after=qty_before + line_qty,
                reference=purchase.number,
                notes=f"Pembelian dari Supplier (Fulfillment PO)" if data.from_po_id else "Pembelian dari Supplier"
            ))

    if status != "draft":
        from .accounting import create_auto_journal
        supplier = db.query(models.Supplier).get(data.supplier_id)
        supplier_name = supplier.name if supplier else "Supplier"
        
        if is_inter_branch_fulfillment:
            # 🏢 KASUS KHUSUS: PUSAT BELI UNTUK CABANG (INTER-BRANCH)
            # 1. Jurnal di Pusat: Kirim Barang ke Cabang (Debit) vs Kas/Hutang (Kredit)
            jurnal_pusat = []
            jurnal_pusat.append({"code": "3-2200", "debit": total, "credit": 0})
            if data.paid > 0:
                jurnal_pusat.append({"code": "1-1100", "debit": 0, "credit": data.paid})
            sisa_hutang = total - data.paid
            if sisa_hutang > 0:
                jurnal_pusat.append({"code": "2-1100", "debit": 0, "credit": sisa_hutang})
            
            create_auto_journal(
                db=db, date_val=tanggal_faktur, number_ref=purchase.number,
                description=f"Pusat beli untuk Cabang ({target_bid}) - {supplier_name}",
                entries=jurnal_pusat, user_id=current_user.id, branch_id=1
            )

            # 2. Jurnal di Cabang: Persediaan (Debit) vs Transfer dari Pusat (Kredit)
            jurnal_cabang = [
                {"code": "1-1400", "debit": total, "credit": 0},
                {"code": "3-2100", "debit": 0, "credit": total}
            ]
            create_auto_journal(
                db=db, date_val=tanggal_faktur, number_ref=purchase.number,
                description=f"Terima Persediaan dari Pusat (Via Supplier {supplier_name})",
                entries=jurnal_cabang, user_id=current_user.id, branch_id=target_bid
            )
        else:
            jurnal_entries = []
            jurnal_entries.append({"code": "1-1400", "debit": total, "credit": 0})
            if data.paid > 0:
                jurnal_entries.append({"code": "1-1100", "debit": 0, "credit": data.paid})
            
            sisa_hutang = total - data.paid
            if sisa_hutang > 0:
                jurnal_entries.append({"code": "2-1100", "debit": 0, "credit": sisa_hutang})

            create_auto_journal(
                db=db,
                date_val=tanggal_faktur,
                number_ref=purchase.number,
                description=f"Pembelian {purchase.number} - {supplier_name}",
                entries=jurnal_entries,
                user_id=current_user.id,
                branch_id=current_user.active_branch_id
            )

    db.commit()
    db.refresh(purchase)
    return purchase


@router.post("/{pid}/reorder-missing")
def reorder_missing_items(
    pid: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    source = get_query(db, models.Purchase, current_user).filter(models.Purchase.id == pid).first()
    if not source: raise HTTPException(404, "Pembelian tidak ditemukan")
    
    missing_items = [it for it in source.items if (it.qty_ordered or 0) > (it.qty_received or 0)]
    if not missing_items:
        raise HTTPException(400, "Tidak ada item yang kurang (Mismatch tidak ditemukan)")
        
    # Create new draft purchase
    new_number = _next_number(db, "PUR", models.Purchase, current_user)
    new_purchase = models.Purchase(
        number=new_number,
        date=get_local_date(),
        branch_id=current_user.active_branch_id,
        supplier_id=source.supplier_id,
        status="draft",
        notes=f"Pesanan kekurangan dari {source.number}",
        created_by=current_user.id
    )
    db.add(new_purchase)
    db.flush()
    
    subtotal = 0
    for it in missing_items:
        qty_missing = (it.qty_ordered or 0) - (it.qty_received or 0)
        line_total = qty_missing * it.buy_price
        subtotal += line_total
        db.add(models.PurchaseItem(
            purchase_id=new_purchase.id,
            item_id=it.item_id,
            qty=qty_missing,
            qty_ordered=qty_missing,
            qty_received=0,
            buy_price=it.buy_price,
            total=line_total
        ))
    
    new_purchase.subtotal = subtotal
    new_purchase.total = subtotal
    
    db.commit()
    db.refresh(new_purchase)
    return new_purchase
