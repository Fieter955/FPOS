from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date
import pytz

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user, get_query

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
    
    last = get_query(db, model, current_user).filter(model.number.like(f"{prefix_full}%")).order_by(model.id.desc()).first()
    
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
    q = get_query(db, models.Purchase, current_user)
    
    if start_date: q = q.filter(models.Purchase.date >= start_date)
    if end_date: q = q.filter(models.Purchase.date <= end_date)
    if supplier_id: q = q.filter(models.Purchase.supplier_id == supplier_id)
    if status: q = q.filter(models.Purchase.status == status)
    
    return q.order_by(models.Purchase.id.desc()).offset(skip).limit(limit).all()


@router.get("/items/")
def get_items_for_purchase(
    supplier_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    query = db.query(models.Item).filter(models.Item.is_active == True)
    if supplier_id:
        query = query.join(models.item_supplier_link).filter(
            models.item_supplier_link.c.supplier_id == supplier_id
        )
    items = query.all()
    return items


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
     
    number = data.number or _next_number(db, "PUR", models.Purchase, current_user)

    subtotal = sum(it.buy_price * it.qty for it in data.items)
    disc_amount = subtotal * (data.discount / 100)
    tax_amount = (subtotal - disc_amount) * (data.tax / 100)
    total = subtotal - disc_amount + tax_amount
    status = "paid" if data.paid >= total else ("partial" if data.paid > 0 else "unpaid")

    purchase = models.Purchase(
        number=number,
        date=tanggal_faktur,
        branch_id=current_user.active_branch_id, # STEMPEL CABANG!
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

    # 👇 CEK GUDANG CABANG AKTIF UNTUK MENERIMA BARANG
    gudang_aktif = db.query(models.Warehouse).filter(
        models.Warehouse.branch_id == current_user.active_branch_id
    ).first()

    for it in data.items:
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

        before = item.stock
        # Tambah Stok Global
        item.stock += it.qty
        
        # 🔄 Tambah Stok Lokal Gudang Cabang
        if gudang_aktif:
            from .warehouse import adjust_warehouse_stock
            adjust_warehouse_stock(db, gudang_aktif.id, item.id, it.qty)

        item.buy_price = it.buy_price

        db.add(models.StockMovement(
            date=tanggal_faktur,
            created_at=local_datetime,
            item_id=item.id,
            branch_id=current_user.active_branch_id, 
            type="in",
            qty=it.qty,
            qty_before=before,
            qty_after=item.stock,
            reference=number,
            notes=f"Pembelian dari Supplier"
        ))

    # AUTO-JOURNAL PEMBELIAN (AKRUAL)
    from .accounting import create_auto_journal
    
    supplier = db.query(models.Supplier).get(data.supplier_id)
    supplier_name = supplier.name if supplier else "Supplier"
    
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
        branch_id=current_user.active_branch_id # ✅ STEMPEL JURNAL PEMBELIAN BARU
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
