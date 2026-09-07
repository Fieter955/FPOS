"""
iPos 5.0 — Tukar Tambah
Pelanggan kembalikan barang + bayar selisih untuk barang baru.
Umum di toko bangunan: tukar ukuran salah, tukar karena rusak, dll.

Flow:
  1. Catat barang yang dikembalikan (dengan kondisi & harga retur)
  2. Catat barang baru yang diambil
  3. Hitung selisih
  4. Stok: barang kembali + ke stok, barang baru - dari stok
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models
from .accounting import create_auto_journal, pastikan_akun_ada, resolve_active_branch_id
from ..services.receipt_renderer import build_trade_in_receipt, settings_from_branch
from .print_queue import enqueue_print_job

router = APIRouter()


def next_trade_in_number(db: Session) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"TT{today}"
    last = db.query(models.TradeIn).filter(
        models.TradeIn.number.like(f"{prefix}%")
    ).order_by(models.TradeIn.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


class ReturnItemIn(BaseModel):
    item_id: int
    qty: float
    return_price: float
    condition: str = "good"    # good | damaged


class NewItemIn(BaseModel):
    item_id: int
    qty: float
    sell_price: float


class TradeInCreate(BaseModel):
    date: date
    customer_id: int  # Mandatory
    notes: Optional[str] = None
    payment_method: str = "cash"
    cash_amount: float = 0
    bank_amount: float = 0
    deposit_amount: float = 0
    return_items: List[ReturnItemIn]
    new_items: List[NewItemIn]


def validate_trade_in_tenders(cash_amount: float, bank_amount: float,
                              deposit_amount: float, deposit_balance: float):
    cash = float(cash_amount or 0)
    bank = float(bank_amount or 0)
    deposit = float(deposit_amount or 0)
    balance = float(deposit_balance or 0)
    if min(cash, bank, deposit) < 0:
        raise HTTPException(400, "Nilai pembayaran tidak boleh negatif")
    if deposit > balance + 0.01:
        raise HTTPException(400, "Saldo pelanggan tidak cukup")
    return cash, bank, deposit


def trade_in_customer_balance_delta(return_subtotal: float, new_subtotal: float,
                                    cash_amount: float, bank_amount: float,
                                    deposit_amount: float) -> float:
    final_surplus = (
        float(return_subtotal or 0)
        + float(cash_amount or 0)
        + float(bank_amount or 0)
        + float(deposit_amount or 0)
        - float(new_subtotal or 0)
    )
    return -float(deposit_amount or 0) + max(final_surplus, 0)


@router.get("/")
def get_trade_ins(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    skip: int = 0, limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    branch_id = resolve_active_branch_id(db, current_user)
    q = db.query(models.TradeIn).filter(models.TradeIn.branch_id == branch_id)
    if start_date: q = q.filter(models.TradeIn.date >= start_date)
    if end_date:   q = q.filter(models.TradeIn.date <= end_date)
    trades = q.order_by(models.TradeIn.id.desc()).offset(skip).limit(limit).all()

    return [{
        "id": t.id, "number": t.number, "date": str(t.date),
        "customer": t.customer.name if t.customer else "-",
        "return_subtotal": t.return_subtotal,
        "new_subtotal": t.new_subtotal,
        "difference": t.difference,
        "payment_method": t.payment_method,
        "cash_amount": t.cash_amount,
        "bank_amount": t.bank_amount,
        "deposit_amount": t.deposit_amount,
        "status": t.status,
        "creator": t.creator.username if t.creator else "-",
    } for t in trades]


@router.get("/{trade_id}")
def get_trade_in(
    trade_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    branch_id = resolve_active_branch_id(db, current_user)
    t = db.query(models.TradeIn).filter(
        models.TradeIn.id == trade_id,
        models.TradeIn.branch_id == branch_id,
    ).first()
    if not t:
        raise HTTPException(404, "Tukar tambah tidak ditemukan")

    return {
        "id": t.id, "number": t.number, "date": str(t.date),
        "customer": {"id": t.customer.id, "name": t.customer.name} if t.customer else None,
        "notes": t.notes,
        "return_subtotal": t.return_subtotal,
        "new_subtotal": t.new_subtotal,
        "difference": t.difference,
        "payment_method": t.payment_method,
        "cash_amount": t.cash_amount,
        "bank_amount": t.bank_amount,
        "deposit_amount": t.deposit_amount,
        "status": t.status,
        "return_items": [{
            "item_name": ri.item.name if ri.item else "-",
            "item_code": ri.item.code if ri.item else "-",
            "qty": ri.qty, "return_price": ri.return_price,
            "condition": ri.condition, "total": ri.total,
        } for ri in t.return_items],
        "new_items": [{
            "item_name": ni.item.name if ni.item else "-",
            "item_code": ni.item.code if ni.item else "-",
            "qty": ni.qty, "sell_price": ni.sell_price, "total": ni.total,
        } for ni in t.new_items],
    }


@router.post("/")
def create_trade_in(
    data: TradeInCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if not data.return_items and not data.new_items:
        raise HTTPException(400, "Harus ada barang yang dikembalikan atau diambil")

    customer = (
        db.query(models.Customer)
        .filter(models.Customer.id == data.customer_id)
        .with_for_update()
        .first()
    )
    if not customer:
        raise HTTPException(404, "Pelanggan wajib dipilih")

    cash_amount, bank_amount, deposit_amount = validate_trade_in_tenders(
        data.cash_amount,
        data.bank_amount,
        data.deposit_amount,
        customer.deposit_balance,
    )

    b_id = resolve_active_branch_id(db, current_user)

    # Validasi & hitung subtotal barang kembali
    return_subtotal = 0.0
    total_modal_return = 0.0
    good_return_subtotal = 0.0
    damaged_return_subtotal = 0.0
    for ri in data.return_items:
        item = db.query(models.Item).get(ri.item_id)
        if not item:
            raise HTTPException(404, f"Item return {ri.item_id} tidak ditemukan")
        if ri.qty <= 0:
            raise HTTPException(400, f"Qty {item.name} harus > 0")
        if ri.return_price < 0:
            raise HTTPException(400, f"Harga retur {item.name} tidak boleh negatif")
        if ri.condition not in {"good", "damaged"}:
            raise HTTPException(400, "Kondisi barang hanya boleh Bagus atau Rusak")
        line_return = ri.qty * ri.return_price
        return_subtotal += line_return
        if ri.condition == "damaged":
            damaged_return_subtotal += line_return
        else:
            good_return_subtotal += line_return
        # Modal return tetap dicatat untuk inventory, tapi jurnal menggunakan return_price? 
        # Sesuai rencana: Debit Inventory senilai return_price (karena itu nilai 'beli' dari pelanggan)
        # Jadi total_modal_return tidak terlalu kritikal untuk jurnal tapi ri.qty * ri.return_price yang dipakai.

    # Validasi & hitung subtotal barang baru
    new_subtotal = 0.0
    total_modal_new = 0.0
    for ni in data.new_items:
        item = db.query(models.Item).get(ni.item_id)
        if not item:
            raise HTTPException(404, f"Item baru {ni.item_id} tidak ditemukan")
        if ni.qty <= 0:
            raise HTTPException(400, f"Qty {item.name} harus > 0")
        if item.stock < ni.qty:
            raise HTTPException(400, f"Stok {item.name} tidak cukup ({item.stock} tersedia)")
        new_subtotal += ni.qty * ni.sell_price
        total_modal_new += ni.qty * (item.buy_price or 0)

    # difference > 0: pelanggan harus bayar
    # difference < 0: toko kembalikan uang ke pelanggan (masuk saldo)
    difference = new_subtotal - return_subtotal

    total_tender = cash_amount + bank_amount + deposit_amount
    if difference <= 0.01 and total_tender > 0.01:
        raise HTTPException(400, "Pembayaran tidak diperlukan untuk transaksi ini")

    required_accounts = ["4-1100", "1-1400", "5-1100", "1-1100", "1-1200", "2-1300"]
    if damaged_return_subtotal > 0:
        required_accounts.append("1-1450")
    pastikan_akun_ada(db, required_accounts)

    number = next_trade_in_number(db)
    trade = models.TradeIn(
        number=number,
        date=data.date,
        customer_id=data.customer_id,
        notes=data.notes,
        payment_method=data.payment_method,
        return_subtotal=return_subtotal,
        new_subtotal=new_subtotal,
        difference=difference,
        created_by=current_user.id,
        branch_id=b_id,
        cash_amount=cash_amount,
        bank_amount=bank_amount,
        deposit_amount=deposit_amount,
    )
    db.add(trade)
    db.flush()

    # Total tender pelanggan: kas, bank, dan pemakaian saldo pelanggan.
    total_paid = cash_amount + bank_amount + deposit_amount

    # 1. Hitung total nilai yang diberikan pelanggan (barang lama + seluruh tender)
    total_provided_by_customer = return_subtotal + total_paid
    
    # 2. Selisih akhir: Jika > 0 artinya pelanggan memberikan nilai lebih (masuk saldo)
    # Jika < 0 artinya pelanggan masih kurang bayar (asumsikan lunas masuk kas)
    final_surplus = total_provided_by_customer - new_subtotal

    deposit_delta = trade_in_customer_balance_delta(
        return_subtotal, new_subtotal, cash_amount, bank_amount, deposit_amount,
    )
    if abs(deposit_delta) > 0.005:
        from ..services.customer_deposits import apply_customer_deposit_change
        apply_customer_deposit_change(
            db, customer, deposit_delta, event_type="trade_in", transaction_date=data.date,
            branch_id=b_id, source_type="trade_in", source_id=trade.id,
            reference=number, description="Dampak saldo transaksi tukar tambah",
            created_by=current_user.id,
        )

    # Proses barang yang dikembalikan → tambah ke stok
    for ri in data.return_items:
        item = db.query(models.Item).get(ri.item_id)
        total = ri.qty * ri.return_price

        db.add(models.TradeInReturnItem(
            trade_in_id=trade.id, item_id=ri.item_id,
            qty=ri.qty, return_price=ri.return_price,
            condition=ri.condition, total=total,
        ))

        if ri.condition == "good":
            # Hanya barang bagus yang kembali menjadi stok jual.
            before = item.stock
            item.stock += ri.qty
            db.add(models.StockMovement(
                branch_id=b_id,
                date=data.date, item_id=item.id,
                type="in", qty=ri.qty,
                qty_before=before, qty_after=item.stock,
                reference=number, notes="Tukar tambah - barang kembali (bagus)"
            ))

    # Proses barang baru → kurangi stok
    for ni in data.new_items:
        item = db.query(models.Item).get(ni.item_id)
        total = ni.qty * ni.sell_price

        db.add(models.TradeInNewItem(
            trade_in_id=trade.id, item_id=ni.item_id,
            qty=ni.qty, sell_price=ni.sell_price, total=total,
        ))

        # Kurangi stok
        before = item.stock
        item.stock -= ni.qty
        db.add(models.StockMovement(
            branch_id=b_id,
            date=data.date, item_id=item.id,
            type="out", qty=ni.qty,
            qty_before=before, qty_after=item.stock,
            reference=number, notes="Tukar tambah - barang keluar"
        ))

    # AKUNTANSI: Jurnal Otomatis
    entries = []
    # 1. Penjualan & Modal Barang Baru
    if new_subtotal > 0:
        entries.append({"code": "4-1100", "debit": 0, "credit": new_subtotal})  # Penjualan
        entries.append({"code": "1-1400", "debit": 0, "credit": total_modal_new}) # Stok Keluar
        entries.append({"code": "5-1100", "debit": total_modal_new, "credit": 0}) # HPP

    # 2. Barang Kembali (Nilai Terima & Stok Masuk)
    if good_return_subtotal > 0:
        entries.append({"code": "1-1400", "debit": good_return_subtotal, "credit": 0})
    if damaged_return_subtotal > 0:
        entries.append({"code": "1-1450", "debit": damaged_return_subtotal, "credit": 0})

    # 3. Pembayaran Fisik (Kas/Bank)
    if cash_amount > 0:
        entries.append({"code": "1-1100", "debit": cash_amount, "credit": 0})
    if bank_amount > 0:
        entries.append({"code": "1-1200", "debit": bank_amount, "credit": 0})
    if deposit_amount > 0:
        entries.append({"code": "2-1300", "debit": deposit_amount, "credit": 0})

    # 4. Keseimbangan Jurnal & Saldo
    if final_surplus > 0.01:
        # Kelebihan nilai (Barang Lama + Bayar Fisik > Barang Baru) -> Masuk ke Titipan/Deposit
        entries.append({"code": "2-1300", "debit": 0, "credit": final_surplus})
    elif final_surplus < -0.01:
        # Kurang bayar (Barang Baru > Barang Lama + Bayar Fisik) -> Asumsikan sisa masuk Kas
        entries.append({"code": "1-1100", "debit": abs(final_surplus), "credit": 0})

    if entries:
        create_auto_journal(
            db=db, date_val=data.date, number_ref=number,
            description=f"Tukar tambah {number} - {customer.name}",
            entries=entries, user_id=current_user.id, branch_id=b_id
        )

    db.commit()
    write_audit(db, current_user.id, "CREATE", "trade_ins", trade.id,
                f"Tukar tambah {number}: selisih Rp {abs(difference):,.0f} "
                f"({'dibayar pelanggan' if difference > 0 else 'masuk saldo pelanggan'})")
    db.commit()

    return {
        "id": trade.id,
        "number": number,
        "return_subtotal": return_subtotal,
        "new_subtotal": new_subtotal,
        "difference": difference,
        "difference_label": "Pelanggan bayar" if difference > 0 else "Masuk saldo pelanggan",
        "message": f"Tukar tambah {number} berhasil diproses",
    }


@router.delete("/{trade_id}")
def delete_trade_in(
    trade_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Batalkan tukar tambah — kembalikan stok ke posisi semula"""
    branch_id = resolve_active_branch_id(db, current_user)
    t = db.query(models.TradeIn).filter(
        models.TradeIn.id == trade_id,
        models.TradeIn.branch_id == branch_id,
    ).with_for_update().first()
    if not t:
        raise HTTPException(404, "Tukar tambah tidak ditemukan")
    if t.status == "cancelled":
        raise HTTPException(400, "Tukar tambah sudah dibatalkan")

    # Balik stok: barang yang kembali → kurangi, barang baru → tambah
    for ri in t.return_items:
        item = db.query(models.Item).get(ri.item_id)
        if item and ri.condition == "good":
            item.stock -= ri.qty

    for ni in t.new_items:
        item = db.query(models.Item).get(ni.item_id)
        if item:
            item.stock += ni.qty

    # Pulihkan dampak saldo pelanggan: pemakaian saldo dikembalikan, sedangkan
    # refund/kelebihan yang dulu masuk saldo ditarik kembali.
    if t.customer:
        customer = db.query(models.Customer).filter(models.Customer.id == t.customer_id).with_for_update().first()
        reverse_delta = -trade_in_customer_balance_delta(
            t.return_subtotal, t.new_subtotal, t.cash_amount, t.bank_amount, t.deposit_amount,
        )
        if abs(reverse_delta) > 0.005:
            from ..services.customer_deposits import apply_customer_deposit_change
            apply_customer_deposit_change(
                db, customer, reverse_delta, event_type="trade_in_cancel",
                transaction_date=date.today(), branch_id=t.branch_id,
                source_type="trade_in", source_id=t.id, reference=t.number,
                description="Pembalikan saldo karena tukar tambah dibatalkan",
                created_by=current_user.id,
            )

    original_journal = db.query(models.Journal).filter(
        models.Journal.reference == t.number,
        models.Journal.branch_id == t.branch_id,
    ).order_by(models.Journal.id.asc()).first()
    if original_journal:
        reversal_entries = []
        for line in original_journal.lines:
            if line.debit_account:
                reversal_entries.append({"code": line.debit_account.code, "debit": 0, "credit": line.amount})
            if line.credit_account:
                reversal_entries.append({"code": line.credit_account.code, "debit": line.amount, "credit": 0})
        if reversal_entries:
            create_auto_journal(
                db=db, date_val=date.today(), number_ref=f"BATAL-{t.number}",
                description=f"Pembatalan tukar tambah {t.number}", entries=reversal_entries,
                user_id=current_user.id, branch_id=t.branch_id,
            )

    write_audit(db, current_user.id, "DELETE", "trade_ins", t.id,
                f"Batalkan tukar tambah {t.number}")
    t.status = "cancelled"
    t.cancelled_at = datetime.now()
    db.commit()
    return {"message": "Tukar tambah dibatalkan, stok dikembalikan"}


@router.post("/print/{trade_id}")
def print_trade_in_receipt(
    trade_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    branch_id = resolve_active_branch_id(db, current_user)
    trade = db.query(models.TradeIn).filter(
        models.TradeIn.id == trade_id,
        models.TradeIn.branch_id == branch_id,
    ).first()
    if not trade:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    branch = db.query(models.Branch).filter(models.Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(400, "Cabang transaksi tidak valid")
    job = enqueue_print_job(
        db,
        branch_id=branch_id,
        content=build_trade_in_receipt(trade, settings_from_branch(branch)),
        document_type="trade_in",
        document_id=trade.id,
        created_by=current_user.id,
    )
    db.commit()
    return {"status": "queued", "job_id": job.id, "branch_id": branch_id}
