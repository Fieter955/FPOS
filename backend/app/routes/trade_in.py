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
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date, datetime
import pytz
import unicodedata
import textwrap
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models
from .accounting import create_auto_journal, resolve_active_branch_id

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
    condition: str = "good"    # good | damaged | partial


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
    return_items: List[ReturnItemIn]
    new_items: List[NewItemIn]


@router.get("/")
def get_trade_ins(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    skip: int = 0, limit: int = 50,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.TradeIn)
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
        "creator": t.creator.username if t.creator else "-",
    } for t in trades]


@router.get("/{trade_id}")
def get_trade_in(
    trade_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    t = db.query(models.TradeIn).get(trade_id)
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

    customer = db.query(models.Customer).get(data.customer_id)
    if not customer:
        raise HTTPException(404, "Pelanggan wajib dipilih")

    b_id = resolve_active_branch_id(db, current_user)

    # Validasi & hitung subtotal barang kembali
    return_subtotal = 0.0
    total_modal_return = 0.0
    for ri in data.return_items:
        item = db.query(models.Item).get(ri.item_id)
        if not item:
            raise HTTPException(404, f"Item return {ri.item_id} tidak ditemukan")
        if ri.qty <= 0:
            raise HTTPException(400, f"Qty {item.name} harus > 0")
        if ri.return_price < 0:
            raise HTTPException(400, f"Harga retur {item.name} tidak boleh negatif")
        return_subtotal += ri.qty * ri.return_price
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
    )
    db.add(trade)
    db.flush()

    # Total yang dibayarkan pelanggan secara fisik (Kas/Bank)
    total_paid_physical = data.cash_amount + data.bank_amount

    # 1. Hitung total nilai yang diberikan pelanggan (Barang Lama + Bayar Fisik)
    total_provided_by_customer = return_subtotal + total_paid_physical
    
    # 2. Selisih akhir: Jika > 0 artinya pelanggan memberikan nilai lebih (masuk saldo)
    # Jika < 0 artinya pelanggan masih kurang bayar (asumsikan lunas masuk kas)
    final_surplus = total_provided_by_customer - new_subtotal

    if final_surplus > 0.01:
        customer.deposit_balance += final_surplus

    # Proses barang yang dikembalikan → tambah ke stok
    for ri in data.return_items:
        item = db.query(models.Item).get(ri.item_id)
        total = ri.qty * ri.return_price

        db.add(models.TradeInReturnItem(
            trade_in_id=trade.id, item_id=ri.item_id,
            qty=ri.qty, return_price=ri.return_price,
            condition=ri.condition, total=total,
        ))

        # Tambah stok
        before = item.stock
        item.stock += ri.qty
        db.add(models.StockMovement(
            branch_id=b_id,
            date=data.date, item_id=item.id,
            type="in", qty=ri.qty,
            qty_before=before, qty_after=item.stock,
            reference=number, notes=f"Tukar tambah - barang kembali (kondisi: {ri.condition})"
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
    if return_subtotal > 0:
        entries.append({"code": "1-1400", "debit": return_subtotal, "credit": 0}) # Stok Masuk (Nilai Terima)

    # 3. Pembayaran Fisik (Kas/Bank)
    if data.cash_amount > 0:
        entries.append({"code": "1-1100", "debit": data.cash_amount, "credit": 0})
    if data.bank_amount > 0:
        entries.append({"code": "1-1200", "debit": data.bank_amount, "credit": 0})

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
    t = db.query(models.TradeIn).get(trade_id)
    if not t:
        raise HTTPException(404, "Tukar tambah tidak ditemukan")

    # Balik stok: barang yang kembali → kurangi, barang baru → tambah
    for ri in t.return_items:
        item = db.query(models.Item).get(ri.item_id)
        if item:
            item.stock -= ri.qty

    for ni in t.new_items:
        item = db.query(models.Item).get(ni.item_id)
        if item:
            item.stock += ni.qty

    write_audit(db, current_user.id, "DELETE", "trade_ins", t.id,
                f"Batalkan tukar tambah {t.number}")
    db.delete(t)
    db.commit()
    return {"message": "Tukar tambah dibatalkan, stok dikembalikan"}


# ── Helper Printer ────────────────────────────────────────────────────────────
WITA = pytz.timezone("Asia/Makassar")

def printer_safe(text: str, max_len: int = None) -> str:
    """Konversi string agar aman untuk printer thermal ESC/POS."""
    if not text:
        return ""
    text = str(text).replace('\xa0', ' ')
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', errors='ignore').decode('ascii')
    text = text.strip()
    if max_len:
        text = text[:max_len]
    return text


@router.post("/print/{trade_id}")
async def print_trade_in_receipt(
    trade_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        try:
            data = await request.json()
        except Exception:
            data = {}

        settings_toko = data.get("settings", {})

        trade = db.query(models.TradeIn).get(trade_id)
        if not trade:
            return JSONResponse(status_code=404, content={"detail": "Transaksi tidak ditemukan"})

        branch_id = trade.branch_id or 1

        # ── Helper Formatter ──────────────────────────────────────────────────
        def format_rp(val):
            try:
                return f"{int(float(val)):,}".replace(",", ".") + ",00"
            except Exception:
                return "0,00"

        def format_qty(val):
            try:
                v = float(val)
                return f"{int(v)},00" if v.is_integer() else f"{v}".replace(".", ",")
            except Exception:
                return "0,00"

        W = 48
        def lr(left, right):
            spaces = W - len(left) - len(right)
            if spaces < 1: spaces = 1
            return f"{left}{' ' * spaces}{right}\n"

        # ── Header & Footer ───────────────────────────────────────────────────
        nama_toko = printer_safe(settings_toko.get("storeName", "TOKO")).upper()
        alamat    = printer_safe(settings_toko.get("storeAddr", ""))
        footer    = printer_safe(settings_toko.get("storeFooter", "Terima Kasih!"))

        try:
            parsed_date = trade.date.strftime("%d-%m-%Y") if hasattr(trade.date, 'strftime') else str(trade.date)
        except Exception:
            parsed_date = datetime.now(WITA).strftime("%d-%m-%Y")

        try:
            time_str = trade.created_at.strftime("%H:%M:%S") if hasattr(trade.created_at, 'strftime') else datetime.now(WITA).strftime("%H:%M:%S")
        except Exception:
            time_str = "-"

        no_str    = str(trade.number)
        kasir     = trade.creator.username.upper() if trade.creator else "ADMIN"
        pelanggan = trade.customer.name.upper() if trade.customer else "UMUM"

        # ── Rakit Struk ───────────────────────────────────────────────────────
        struk  = "\x1B\x61\x01\x1D\x21\x11"
        struk += f"{nama_toko}\n\n"
        struk += "\x1D\x21\x00\x1B\x61\x00"

        for line in alamat.split('\n'):
            struk += f"{line}\n"
        struk += "\n"

        struk += "\x1B\x61\x01" # Center
        struk += "NOTA TUKAR TAMBAH\n"
        struk += "\x1B\x61\x00" # Left

        struk += lr(f"No.  : {no_str}", parsed_date)
        struk += lr(f"Kasir: {kasir}", time_str)
        struk += f"Pel. : {pelanggan}\n"

        garis  = "-" * W
        struk += f"{garis}\n"

        # === BARANG KEMBALI ===
        if trade.return_items:
            struk += "\x1B\x61\x01" # Center
            struk += "=== BARANG KEMBALI ===\n"
            struk += "\x1B\x61\x00" # Left
            for item in trade.return_items:
                nama_barang = printer_safe(item.item.name).upper() if item.item else "BARANG"
                wrapped     = textwrap.wrap(nama_barang, width=W)
                struk += "\n".join(wrapped) + "\n"
                
                qty        = float(item.qty)
                harga_str  = format_rp(item.return_price)
                qty_str    = format_qty(qty)
                total_str  = format_rp(item.total)
                cond_str   = f"({item.condition})"
                left_part  = f"{harga_str:<12} x {qty_str:<5} {cond_str:<10} ="
                struk     += lr(left_part, total_str)
            struk += f"{garis}\n"
            struk += lr("TOTAL KEMBALI", format_rp(trade.return_subtotal))
            struk += f"{garis}\n\n"

        # === BARANG BARU ===
        if trade.new_items:
            struk += "\x1B\x61\x01" # Center
            struk += "=== BARANG BARU ===\n"
            struk += "\x1B\x61\x00" # Left
            for item in trade.new_items:
                nama_barang = printer_safe(item.item.name).upper() if item.item else "BARANG"
                wrapped     = textwrap.wrap(nama_barang, width=W)
                struk += "\n".join(wrapped) + "\n"
                
                qty        = float(item.qty)
                harga_str  = format_rp(item.sell_price)
                qty_str    = format_qty(qty)
                total_str  = format_rp(item.total)
                left_part  = f"{harga_str:<14} x {qty_str:<5} ="
                struk     += lr(left_part, total_str)
            struk += f"{garis}\n"
            struk += lr("TOTAL BARU", format_rp(trade.new_subtotal))
            struk += f"{garis}\n\n"

        # SUMMARY SELISIH
        diff = trade.difference
        struk += lr("SELISIH", format_rp(abs(diff)))
        if diff > 0:
            struk += lr("", "PELANGGAN BAYAR")
        elif diff < 0:
            struk += lr("", "MASUK SALDO PELANGGAN")
        else:
            struk += lr("", "IMPAS")

        struk += "\n\x1B\x61\x01"
        struk += f"{footer}\n\n\n"

        # ── Simpan ke Print Queue ─────────────────────────────────────────────
        db.add(models.PrintJob(
            branch_id=branch_id,
            content=struk,
            content_type="raw",
            status="pending"
        ))
        db.commit()

        return {"status": "success", "message": f"Struk masuk ke antrean cetak!"}

    except Exception as e:
        print(f"🔥 ERROR PRINT TRADE-IN: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Gagal mencetak: {str(e)}"})

