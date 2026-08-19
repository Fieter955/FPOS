from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from ..database import get_db
from .. import models
from ..auth import get_current_user

router = APIRouter()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def next_number(db, prefix, model):
    from datetime import date as d
    today = d.today().strftime("%Y%m%d")
    pfx = f"{prefix}{today}"
    last = db.query(model).filter(model.number.like(f"{pfx}%")).order_by(model.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{pfx}{seq:04d}"


# ══════════════════════════════════════════════════════════════════════════════
# KONSINYASI MASUK (barang titipan dari supplier)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/in")
def list_consignment_in(
    status: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(models.ConsignmentIn)
    if status: q = q.filter(models.ConsignmentIn.status == status)
    rows = q.order_by(models.ConsignmentIn.id.desc()).offset(skip).limit(limit).all()
    
    result = []
    for r in rows:
        # Ambil data supplier
        supplier = db.query(models.Supplier).get(r.supplier_id)
        
        items_out = []
        total_nilai = 0 # Variabel bantu untuk menghitung total uang
        
        for i in r.items:
            item = db.query(models.Item).get(i.item_id)
            # Hitung total nilai baris ini
            row_total = i.qty_received * i.consign_price
            total_nilai += row_total
            
            items_out.append({
                "id": i.id, 
                "item_id": i.item_id,
                "item_name": item.name if item else "-",
                "qty_received": i.qty_received,
                "consign_price": i.consign_price,
                "sell_price": i.sell_price
            })
            
        result.append({
            "id": r.id, 
            "number": r.number, 
            "date": str(r.date),
            "supplier_id": r.supplier_id,
            "supplier": {"name": supplier.name if supplier else "-"}, # Agar frontend c.supplier?.name jalan
            "status": r.status, 
            "notes": r.notes,
            "items": items_out,
            "total_amount": total_nilai # Sekarang frontend bisa baca Rp sekian
        })
    return result


@router.get("/in/{cid}")
def get_consignment_in(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    r = db.query(models.ConsignmentIn).get(cid)
    if not r: raise HTTPException(404, "Konsinyasi tidak ditemukan")
    supplier = db.query(models.Supplier).get(r.supplier_id)
    items_out = []
    for i in r.items:
        item = db.query(models.Item).get(i.item_id)
        items_out.append({
            "id": i.id, "item_id": i.item_id,
            "item_name": item.name if item else "-",
            "item_code": item.code if item else "-",
            "qty_received": i.qty_received, "qty_sold": i.qty_sold,
            "qty_returned": i.qty_returned,
            "qty_remaining": i.qty_received - i.qty_sold - i.qty_returned,
            "sell_price": i.sell_price, "consign_price": i.consign_price
        })
    bills = []
    for b in r.bills:
        bills.append({
            "id": b.id, "number": b.number, "date": str(b.date),
            "amount": b.amount, "paid": b.paid, "status": b.status, "notes": b.notes
        })
    return {
        "id": r.id, "number": r.number, "date": str(r.date),
        "supplier_id": r.supplier_id,
        "supplier_name": supplier.name if supplier else "-",
        "status": r.status, "notes": r.notes,
        "items": items_out, "bills": bills
    }


@router.post("/in")
def create_consignment_in(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    number = data.get("number") or next_number(db, "KI", models.ConsignmentIn)
    tanggal = date.fromisoformat(data["date"])
    rec = models.ConsignmentIn(
        number=number,
        date=tanggal,
        supplier_id=data["supplier_id"],
        status="active",
        notes=data.get("notes")
    )
    db.add(rec); db.flush()

    for it in data.get("items", []):
        item = db.query(models.Item).get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")
        db.add(models.ConsignmentInItem(
            consignment_id=rec.id,
            item_id=it["item_id"],
            qty_received=it["qty"],
            sell_price=it["sell_price"],
            consign_price=it["consign_price"]
        ))
        # Tambah stok — barang titipan masuk ke gudang
        before = item.stock
        item.stock += it["qty"]
        db.add(models.StockMovement(
            date=tanggal, item_id=item.id,
            type="in", qty=it["qty"],
            qty_before=before, qty_after=item.stock,
            reference=number, notes="Konsinyasi Masuk"
        ))

    db.commit(); db.refresh(rec)
    return {"id": rec.id, "number": rec.number, "message": "Konsinyasi masuk disimpan"}


@router.post("/in/{cid}/sell")
def record_consignment_in_sale(cid: int, data: dict,
                                db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Catat barang konsinyasi yang terjual"""
    rec = db.query(models.ConsignmentIn).get(cid)
    if not rec: raise HTTPException(404, "Konsinyasi tidak ditemukan")
    if rec.status == "closed": raise HTTPException(400, "Konsinyasi sudah ditutup")

    for it in data.get("items", []):
        ci_item = db.query(models.ConsignmentInItem).filter(
            models.ConsignmentInItem.consignment_id == cid,
            models.ConsignmentInItem.item_id == it["item_id"]
        ).first()
        if not ci_item: raise HTTPException(404, f"Item tidak ada di konsinyasi ini")
        remaining = ci_item.qty_received - ci_item.qty_sold - ci_item.qty_returned
        if it["qty"] > remaining:
            raise HTTPException(400, f"Qty melebihi sisa barang ({remaining})")
        ci_item.qty_sold += it["qty"]

        # Kurangi stok
        item = db.query(models.Item).get(it["item_id"])
        tanggal = date.fromisoformat(data.get("date", str(date.today()))) if isinstance(data.get("date"), str) else data.get("date", date.today())
        if item:
            before = item.stock
            item.stock -= it["qty"]
            db.add(models.StockMovement(
                date=tanggal,
                item_id=item.id, type="out", qty=it["qty"],
                qty_before=before, qty_after=item.stock,
                reference=rec.number, notes="Penjualan Konsinyasi Masuk"
            ))

    db.commit()
    return {"message": "Penjualan konsinyasi dicatat"}


@router.post("/in/{cid}/return")
def return_consignment_in(cid: int, data: dict,
                           db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Retur barang konsinyasi ke supplier"""
    rec = db.query(models.ConsignmentIn).get(cid)
    if not rec: raise HTTPException(404, "Konsinyasi tidak ditemukan")

    for it in data.get("items", []):
        ci_item = db.query(models.ConsignmentInItem).filter(
            models.ConsignmentInItem.consignment_id == cid,
            models.ConsignmentInItem.item_id == it["item_id"]
        ).first()
        if not ci_item: raise HTTPException(404, "Item tidak ditemukan di konsinyasi")
        remaining = ci_item.qty_received - ci_item.qty_sold - ci_item.qty_returned
        if it["qty"] > remaining:
            raise HTTPException(400, f"Qty retur melebihi sisa ({remaining})")
        ci_item.qty_returned += it["qty"]

        tanggal = date.fromisoformat(data.get("date"))
        # Kurangi stok
        item = db.query(models.Item).get(it["item_id"])
        if item:
            before = item.stock
            item.stock -= it["qty"]
            db.add(models.StockMovement(
                date=tanggal,
                item_id=item.id, type="out", qty=it["qty"],
                qty_before=before, qty_after=item.stock,
                reference=rec.number, notes="Retur Konsinyasi Masuk"
            ))

    db.commit()
    return {"message": "Retur konsinyasi dicatat"}


@router.post("/in/{cid}/bill")
def create_consignment_in_bill(cid: int, data: dict, db: Session = Depends(get_db)):
    rec = db.query(models.ConsignmentIn).get(cid)
    if not rec: raise HTTPException(404, "Konsinyasi tidak ditemukan")

    # 1. Update qty_sold di setiap item yang dilaporkan laku
    items_reported = data.get("items", [])
    for it in items_reported:
        db_item = db.query(models.ConsignmentInItem).filter(
            models.ConsignmentInItem.consignment_id == cid,
            models.ConsignmentInItem.item_id == it["item_id"]
        ).first()
        if db_item:
            # Pastikan tidak melebihi sisa stok
            db_item.qty_sold += it["qty"]

    # 2. Buat Dokumen Tagihan
    new_bill = models.ConsignmentInBill(
        number=next_number(db, "TKI", models.ConsignmentInBill),
        date=date.today(),
        consignment_id=cid,
        amount=data.get("amount", 0),
        notes=data.get("notes")
    )
    db.add(new_bill)
    db.commit()
    return {"message": "Tagihan berhasil diproses"}


@router.post("/in/bill/{bid}/pay")
def pay_consignment_in_bill(bid: int, data: dict,
                             db: Session = Depends(get_db), _=Depends(get_current_user)):
    bill = db.query(models.ConsignmentInBill).get(bid)
    if not bill: raise HTTPException(404, "Tagihan tidak ditemukan")
    bill.paid += data["amount"]
    bill.status = "paid" if bill.paid >= bill.amount else "partial"
    db.commit()
    return {"message": "Pembayaran dicatat", "status": bill.status}


# ══════════════════════════════════════════════════════════════════════════════
# KONSINYASI KELUAR (barang kita titipkan ke toko lain)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/out")
def list_consignment_out(
    status: Optional[str] = None,
    skip: int = 0, limit: int = 100,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(models.ConsignmentOut)
    if status: q = q.filter(models.ConsignmentOut.status == status)
    rows = q.order_by(models.ConsignmentOut.id.desc()).offset(skip).limit(limit).all()
    
    result = []
    for r in rows:
        # Ambil data customer (Toko Mitra)
        customer = db.query(models.Customer).get(r.customer_id) if r.customer_id else None
        
        items_out = []
        total_nilai = 0
        
        for i in r.items:
            item = db.query(models.Item).get(i.item_id)
            # Hitung total: Qty Keluar x Harga Jual
            row_total = i.qty_sent * i.sell_price
            total_nilai += row_total
            
            items_out.append({
                "id": i.id,
                "item_name": item.name if item else "-",
                "qty_sent": i.qty_sent,
                "sell_price": i.sell_price,
                "total": row_total
            })
            
        result.append({
            "id": r.id,
            "number": r.number,
            "date": str(r.date),
            "customer_id": r.customer_id,
            "customer_name": customer.name if customer else r.notes or "-", # Fallback ke notes jika customer_id kosong
            "status": r.status,
            "notes": r.notes,
            "items": items_out,
            "total_amount": total_nilai # Ini yang bikin tabel gak Rp 0 lagi
        })
    return result


@router.get("/out/{cid}")
def get_consignment_out(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    r = db.query(models.ConsignmentOut).get(cid)
    if not r: raise HTTPException(404, "Konsinyasi keluar tidak ditemukan")
    customer = db.query(models.Customer).get(r.customer_id) if r.customer_id else None
    items_out = []
    for i in r.items:
        item = db.query(models.Item).get(i.item_id)
        items_out.append({
            "id": i.id, "item_id": i.item_id,
            "item_name": item.name if item else "-",
            "item_code": item.code if item else "-",
            "qty_sent": i.qty_sent, "qty_sold": i.qty_sold,
            "qty_returned": i.qty_returned,
            "qty_remaining": i.qty_sent - i.qty_sold - i.qty_returned,
            "sell_price": i.sell_price
        })
    bills = [{"id": b.id, "number": b.number, "date": str(b.date),
              "amount": b.amount, "paid": b.paid, "status": b.status}
             for b in r.bills]
    return {
        "id": r.id, "number": r.number, "date": str(r.date),
        "customer_id": r.customer_id,
        "customer_name": customer.name if customer else "-",
        "status": r.status, "notes": r.notes,
        "items": items_out, "bills": bills
    }


@router.post("/out")
def create_consignment_out(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    number = data.get("number") or next_number(db, "KO", models.ConsignmentOut)
    # Tambahkan konversi tanggal agar tidak error SQLite Date
    tanggal = date.fromisoformat(data["date"]) if isinstance(data["date"], str) else data["date"]
    
    rec = models.ConsignmentOut(
        number=number,
        date=tanggal,
        customer_id=data.get("customer_id"),
        status="active",
        notes=data.get("notes")
    )
    db.add(rec); db.flush()

    for it in data.get("items", []):
        item = db.query(models.Item).get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")
        if item.stock < it["qty"]:
            raise HTTPException(400, f"Stok {item.name} tidak cukup ({item.stock})")

        db.add(models.ConsignmentOutItem(
            consignment_id=rec.id,
            item_id=it["item_id"],
            qty_sent=it["qty"],
            sell_price=it["sell_price"]
        ))
        # Kurangi stok — barang keluar ke toko lain
        before = item.stock
        item.stock -= it["qty"]
        tanggal = date.fromisoformat(data["date"])
        db.add(models.StockMovement(
            date=tanggal, item_id=item.id,
            type="out", qty=it["qty"],
            qty_before=before, qty_after=item.stock,
            reference=number, notes="Konsinyasi Keluar"
        ))

    db.commit(); db.refresh(rec)
    return {"id": rec.id, "number": rec.number, "message": "Konsinyasi keluar disimpan"}


@router.post("/out/{cid}/report-sold")
def report_consignment_out_sold(cid: int, data: dict,
                                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Laporan barang yang terjual dari toko konsinyasi"""
    rec = db.query(models.ConsignmentOut).get(cid)
    if not rec: raise HTTPException(404, "Konsinyasi tidak ditemukan")
    if rec.status == "closed": raise HTTPException(400, "Konsinyasi sudah ditutup")

    for it in data.get("items", []):
        co_item = db.query(models.ConsignmentOutItem).filter(
            models.ConsignmentOutItem.consignment_id == cid,
            models.ConsignmentOutItem.item_id == it["item_id"]
        ).first()
        if not co_item: raise HTTPException(404, "Item tidak ada di konsinyasi ini")
        remaining = co_item.qty_sent - co_item.qty_sold - co_item.qty_returned
        if it["qty"] > remaining:
            raise HTTPException(400, f"Qty melebihi sisa ({remaining})")
        co_item.qty_sold += it["qty"]

    db.commit()
    return {"message": "Laporan penjualan dicatat"}


@router.post("/out/{cid}/return")
def return_consignment_out(cid: int, data: dict,
                            db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Terima retur barang dari toko konsinyasi"""
    rec = db.query(models.ConsignmentOut).get(cid)
    if not rec: raise HTTPException(404, "Konsinyasi tidak ditemukan")

    for it in data.get("items", []):
        co_item = db.query(models.ConsignmentOutItem).filter(
            models.ConsignmentOutItem.consignment_id == cid,
            models.ConsignmentOutItem.item_id == it["item_id"]
        ).first()
        if not co_item: raise HTTPException(404, "Item tidak ditemukan")
        remaining = co_item.qty_sent - co_item.qty_sold - co_item.qty_returned
        if it["qty"] > remaining:
            raise HTTPException(400, f"Qty melebihi sisa ({remaining})")
        co_item.qty_returned += it["qty"]

        # Kembalikan stok
        item = db.query(models.Item).get(it["item_id"])
        if item:
            before = item.stock
            item.stock += it["qty"]
            tanggal = date.fromisoformat(data.get("date"))
            db.add(models.StockMovement(
                date=tanggal,
                item_id=item.id, type="in", qty=it["qty"],
                qty_before=before, qty_after=item.stock,
                reference=rec.number, notes="Retur Konsinyasi Keluar"
            ))

    db.commit()
    return {"message": "Retur diterima, stok dikembalikan"}


@router.post("/out/{cid}/bill")
def create_consignment_out_bill(cid: int, data: dict,
                                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Buat tagihan piutang ke toko konsinyasi untuk barang yang terjual"""
    rec = db.query(models.ConsignmentOut).get(cid)
    if not rec: raise HTTPException(404, "Konsinyasi tidak ditemukan")

    total = sum(i.qty_sold * i.sell_price for i in rec.items)
    if total <= 0: raise HTTPException(400, "Tidak ada barang terjual untuk ditagih")

    number = next_number(db, "TKO", models.ConsignmentOutBill)
    bill = models.ConsignmentOutBill(
        number=number,
        date=data.get("date", str(date.today())),
        consignment_id=cid,
        amount=total,
        status="unpaid",
        notes=data.get("notes")
    )
    db.add(bill); db.commit(); db.refresh(bill)
    return {"id": bill.id, "number": bill.number, "amount": total, "message": "Tagihan piutang dibuat"}


@router.post("/out/bill/{bid}/pay")
def pay_consignment_out_bill(bid: int, data: dict,
                              db: Session = Depends(get_db), _=Depends(get_current_user)):
    bill = db.query(models.ConsignmentOutBill).get(bid)
    if not bill: raise HTTPException(404, "Tagihan tidak ditemukan")
    bill.paid += data["amount"]
    bill.status = "paid" if bill.paid >= bill.amount else "partial"
    db.commit()
    return {"message": "Pembayaran diterima", "status": bill.status}


@router.post("/in/{cid}/cancel")
def cancel_consignment_in(cid: int, db: Session = Depends(get_db)):
    rec = db.query(models.ConsignmentIn).get(cid)
    if not rec: raise HTTPException(404, "Data tidak ditemukan")
    if rec.status == "cancelled": raise HTTPException(400, "Sudah dibatalkan")
    tanggal = rec.date
    # 1. Balikkan Stok (Reverse Stock)
    for i in rec.items:
        item = db.query(models.Item).get(i.item_id)
        if item:
            before = item.stock
            # Karena ini Konsinyasi Masuk (awalnya nambah), maka batal = kurang
            item.stock -= i.qty_received 
            
            # Catat mutasi stok kebalikan
            db.add(models.StockMovement(
                date=tanggal, item_id=item.id,
                type="out", qty=i.qty_received,
                qty_before=before, qty_after=item.stock,
                reference=rec.number, notes="PEMBATALAN Konsinyasi Masuk"
            ))

    # 2. Ubah Status saja, JANGAN DI-DELETE
    rec.status = "cancelled"
    
    db.commit()
    return {"message": "Transaksi berhasil dibatalkan dan stok dikembalikan"}

@router.post("/out/{cid}/cancel")
def cancel_consignment_out(cid: int, db: Session = Depends(get_db)):
    # 1. Cari data Konsinyasi Keluar
    rec = db.query(models.ConsignmentOut).get(cid)
    
    if not rec: 
        raise HTTPException(404, "Data konsinyasi keluar tidak ditemukan")
    if rec.status == "cancelled": 
        raise HTTPException(400, "Transaksi ini sudah dibatalkan sebelumnya")
    
    # Ambil tanggal asli dokumen agar laporan stok sinkron
    tanggal_efektif = rec.date
    
    # 2. Balikkan Stok (Restock)
    for i in rec.items:
        item = db.query(models.Item).get(i.item_id)
        if item:
            before = item.stock
            # Karena Konsinyasi Keluar (awalnya barang keluar/kurang), 
            # maka pembatalan = barang masuk kembali (tambah)
            item.stock += i.qty_sent 
            
            # Catat mutasi stok kebalikan di StockMovement
            db.add(models.StockMovement(
                date=tanggal_efektif, 
                item_id=item.id,
                type="in", # Masuk kembali ke gudang
                qty=i.qty_sent,
                qty_before=before,
                qty_after=item.stock,
                reference=rec.number,
                notes="PEMBATALAN Konsinyasi Keluar"
            ))

    # 3. Ubah Status saja
    rec.status = "cancelled"
    
    db.commit()
    return {"message": "Konsinyasi keluar berhasil dibatalkan dan barang kembali ke stok"}