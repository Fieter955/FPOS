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
from datetime import date
from pydantic import BaseModel

from ..database import get_db
from ..auth import get_current_user, write_audit
from .. import models

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
    customer_id: Optional[int] = None
    notes: Optional[str] = None
    payment_method: str = "cash"
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

    # Validasi & hitung subtotal barang kembali
    return_subtotal = 0.0
    for ri in data.return_items:
        item = db.query(models.Item).get(ri.item_id)
        if not item:
            raise HTTPException(404, f"Item return {ri.item_id} tidak ditemukan")
        if ri.qty <= 0:
            raise HTTPException(400, f"Qty {item.name} harus > 0")
        if ri.return_price < 0:
            raise HTTPException(400, f"Harga retur {item.name} tidak boleh negatif")
        return_subtotal += ri.qty * ri.return_price

    # Validasi & hitung subtotal barang baru
    new_subtotal = 0.0
    for ni in data.new_items:
        item = db.query(models.Item).get(ni.item_id)
        if not item:
            raise HTTPException(404, f"Item baru {ni.item_id} tidak ditemukan")
        if ni.qty <= 0:
            raise HTTPException(400, f"Qty {item.name} harus > 0")
        if item.stock < ni.qty:
            raise HTTPException(400, f"Stok {item.name} tidak cukup ({item.stock} tersedia)")
        new_subtotal += ni.qty * ni.sell_price

    # difference > 0: pelanggan harus bayar
    # difference < 0: toko kembalikan uang ke pelanggan
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
    )
    db.add(trade)
    db.flush()

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
            date=data.date, item_id=item.id,
            type="out", qty=ni.qty,
            qty_before=before, qty_after=item.stock,
            reference=number, notes="Tukar tambah - barang keluar"
        ))

    db.commit()
    write_audit(db, current_user.id, "CREATE", "trade_ins", trade.id,
                f"Tukar tambah {number}: selisih Rp {abs(difference):,.0f} "
                f"({'dibayar pelanggan' if difference > 0 else 'dikembalikan ke pelanggan'})")
    db.commit()

    return {
        "id": trade.id,
        "number": number,
        "return_subtotal": return_subtotal,
        "new_subtotal": new_subtotal,
        "difference": difference,
        "difference_label": "Pelanggan bayar" if difference > 0 else "Toko kembalikan uang",
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
