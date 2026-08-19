from fastapi import APIRouter, Depends, HTTPException
import pytz
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import date, datetime
from ..database import get_db
from .. import models
from ..auth import get_current_user, write_audit, require_admin
from ..services.virtual_units import get_required_stock_qty, is_virtual_variant
from ..services.tax_context import purchase_tax_type

router = APIRouter()
WITA = pytz.timezone("Asia/Makassar")


def get_local_date() -> date:
    return datetime.now(WITA).date()


@router.get("/history/purchases")
def get_purchase_history_items(
    supplier_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None, # 'returned', 'not_returned', 'partial'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    query = db.query(models.PurchaseItem).join(models.Purchase).filter(
        models.Purchase.status != 'cancelled'
    )
    
    if current_user.active_branch_id:
        query = query.filter(models.Purchase.branch_id == current_user.active_branch_id)
    
    if supplier_id:
        query = query.filter(models.Purchase.supplier_id == supplier_id)
    if start_date:
        query = query.filter(models.Purchase.date >= start_date)
    if end_date:
        query = query.filter(models.Purchase.date <= end_date)
        
    items = query.order_by(models.Purchase.date.desc()).all()
    
    result = []
    for it in items:
        # Hitung berapa yang sudah diretur untuk baris ini
        # Karena model kita PurchaseReturnItem merujuk ke purchase_id + item_id
        # Kita aggregasi retur yang merujuk ke purchase yang sama dan item yang sama
        returned_qty = db.query(func.sum(models.PurchaseReturnItem.qty)).join(models.PurchaseReturn).filter(
            models.PurchaseReturn.purchase_id == it.purchase_id,
            models.PurchaseReturnItem.item_id == it.item_id
        ).scalar() or 0.0
        
        available_qty = it.qty - returned_qty
        
        # Filter berdasarkan status retur jika diminta
        item_status = "not_returned"
        if returned_qty >= it.qty: item_status = "returned"
        elif returned_qty > 0: item_status = "partial"
        
        if status and status != item_status:
            continue

        result.append({
            "item_id": it.item_id,
            "item": {
                "id": it.item_id,
                "name": it.item.name,
                "code": it.item.code,
                "barcode": it.item.barcode,
            },
            "buy_price": it.buy_price,
            "qty_bought": it.qty,
            "qty_returned": returned_qty,
            "qty_available": available_qty,
            "status": item_status,
            "purchase_date": str(it.purchase.date),
            "purchase_id": it.purchase_id,
            "purchase_number": it.purchase.number,
            "supplier_name": it.purchase.supplier.name if it.purchase.supplier else "-",
            "is_tax_included": it.purchase.is_tax_included,
            "tax_type": purchase_tax_type(it.purchase),
            "tax_percent": it.purchase.tax_percent
        })
    return result


@router.get("/history/sales")
def get_sale_history_items(
    customer_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    query = db.query(models.SaleItem).join(models.Sale).filter(
        models.Sale.status != 'cancelled'
    )
    
    if current_user.active_branch_id:
        query = query.filter(models.Sale.branch_id == current_user.active_branch_id)
    
    if customer_id:
        query = query.filter(models.Sale.customer_id == customer_id)
    if start_date:
        query = query.filter(models.Sale.date >= start_date)
    if end_date:
        query = query.filter(models.Sale.date <= end_date)

    items = query.order_by(models.Sale.date.desc()).all()
    
    result = []
    for it in items:
        returned_qty = db.query(func.sum(models.SaleReturnItem.qty)).join(models.SaleReturn).filter(
            models.SaleReturn.sale_id == it.sale_id,
            models.SaleReturnItem.item_id == it.item_id
        ).scalar() or 0.0
        
        available_qty = it.qty - returned_qty
        
        item_status = "not_returned"
        if returned_qty >= it.qty: item_status = "returned"
        elif returned_qty > 0: item_status = "partial"
        
        if status and status != item_status:
            continue
            
        result.append({
            "item_id": it.item_id,
            "item": {
                "id": it.item_id,
                "name": it.item.name,
                "code": it.item.code,
                "barcode": it.item.barcode,
            },
            "sell_price": it.sell_price,
            "qty_sold": it.qty,
            "qty_returned": returned_qty,
            "qty_available": available_qty,
            "status": item_status,
            "sale_date": str(it.sale.date),
            "sale_id": it.sale_id,
            "sale_number": it.sale.number,
            "customer_name": it.sale.customer.name if it.sale.customer else "Umum",
            "is_tax_included": it.sale.is_tax_included,
            "tax_percent": it.sale.tax_percent
        })
    return result


@router.get("/history/broken")
def get_broken_history_items(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None, # 'returned', 'not_returned', 'partial'
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Mengambil daftar barang yang rusak/broken dari transaksi Tukar Tambah.
    Ini nantinya bisa diproses untuk retur ke supplier.
    """
    query = db.query(models.TradeInReturnItem).join(models.TradeIn).filter(
        models.TradeInReturnItem.condition != 'good'
    )
    
    if current_user.active_branch_id:
        query = query.filter(models.TradeIn.branch_id == current_user.active_branch_id)
        
    if start_date:
        query = query.filter(models.TradeIn.date >= start_date)
    if end_date:
        query = query.filter(models.TradeIn.date <= end_date)

    items = query.order_by(models.TradeIn.date.desc()).all()
    
    result = []
    for it in items:
        returned_qty = it.returned_qty or 0.0
        available_qty = it.qty - returned_qty
        
        item_status = "not_returned"
        if returned_qty >= it.qty: item_status = "returned"
        elif returned_qty > 0: item_status = "partial"
        
        if status and status != item_status:
            continue

        result.append({
            "trade_in_return_item_id": it.id,
            "item_id": it.item_id,
            "item": {
                "id": it.item_id,
                "name": it.item.name,
                "code": it.item.code,
                "barcode": it.item.barcode,
            },
            "return_price": it.return_price,
            "qty": it.qty,
            "qty_returned": returned_qty,
            "qty_available": available_qty,
            "status": item_status,
            "date": str(it.trade_in.date),
            "trade_in_id": it.trade_in_id,
            "trade_in_number": it.trade_in.number,
            "customer_name": it.trade_in.customer.name if it.trade_in.customer else "Umum",
            "condition": it.condition
        })
    return result


def _next_number(db, prefix, model):
    from datetime import date as d
    today = d.today().strftime("%Y%m%d")
    pfx = f"{prefix}{today}"
    last = db.query(model).filter(model.number.like(f"{pfx}%")).order_by(model.id.desc()).first()
    seq = int(last.number[-4:]) + 1 if last else 1
    return f"{pfx}{seq:04d}"


# ══════════════════════════════════════════════════════════════════════════════
# RETUR PENJUALAN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sales")
def get_sale_returns(skip: int = 0, limit: int = 100,
                     db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.SaleReturn).join(models.Sale)
    if current_user.active_branch_id:
        query = query.filter(models.Sale.branch_id == current_user.active_branch_id)
        
    returns = query.order_by(models.SaleReturn.id.desc()).offset(skip).limit(limit).all()
    result = []
    for r in returns:
        sale = r.sale
        items_out = []
        for i in r.items:
            item = db.query(models.Item).get(i.item_id)
            items_out.append({
                "id": i.id, "item_id": i.item_id,
                "item_name": item.name if item else "-",
                "qty": i.qty, "price": i.price, "total": i.total
            })
        result.append({
            "id": r.id, "number": r.number, "date": str(r.date),
            "sale_id": r.sale_id,
            "sale_number": sale.number if sale else "-",
            "total": r.total, "reason": r.reason, "notes": r.notes,
            "items": items_out,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    return result


@router.post("/sales")
def create_sale_return(data: dict, db: Session = Depends(get_db),
                       current_user: models.User = Depends(get_current_user)):
    sale_id = data.get("sale_id")
    sale = db.query(models.Sale).get(sale_id)
    if not sale: raise HTTPException(404, "Penjualan tidak ditemukan")

    gudang_aktif = None
    if sale.branch_id:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == sale.branch_id,
            models.Warehouse.is_default == True,
        ).first()

    number = _next_number(db, "RS", models.SaleReturn)
    total_sales = 0.0
    total_tax = 0.0
    total_cogs = 0.0
    
    # Ambil info pajak dari penjualan asli jika tidak dikirim dari frontend
    is_tax_included = data.get("is_tax_included", sale.is_tax_included if hasattr(sale, 'is_tax_included') else True)
    tax_percent = data.get("tax_percent", sale.tax_percent if hasattr(sale, 'tax_percent') else 0.0)

    # 🔒 Pastikan semua akun jurnal retur tersedia SEBELUM stok dimutasi, supaya jurnal tidak
    # gagal di tengah jalan dan meninggalkan stok berpindah tanpa pencatatan GL.
    from .accounting import pastikan_akun_ada
    _akun_wajib = ["2-1300", "4-1200", "1-1400", "5-1100"]
    # Pastikan 2-1200 ada bila penjualan asli MEMUNGUT PPN — termasuk faktur tarif-campur yang
    # tax_percent header-nya 0 tetapi ada baris ber-PPN (sale.tax > 0).
    if (tax_percent and tax_percent > 0) or (getattr(sale, "tax", 0) or 0) > 0:
        _akun_wajib.append("2-1200")  # PPN Keluaran dibalik ke Hutang PPN (inklusif & eksklusif)
    pastikan_akun_ada(db, _akun_wajib)

    retur = models.SaleReturn(
        number=number,
        date=get_local_date(),
        sale_id=sale_id,
        tax_percent=tax_percent,
        is_tax_included=is_tax_included,
        reason=data.get("reason"),
        notes=data.get("notes")
    )
    db.add(retur); db.flush()

    for it in data.get("items", []):
        item = db.query(models.Item).with_for_update().get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")

        # Validasi: qty retur tidak boleh lebih dari qty jual
        sale_item = next((si for si in sale.items if si.item_id == it["item_id"]), None)
        if not sale_item:
            raise HTTPException(400, f"Item {item.name} tidak ada di faktur ini")

        # Cek total retur sebelumnya untuk item ini
        prev_returned = db.query(
            models.SaleReturnItem
        ).join(models.SaleReturn).filter(
            models.SaleReturn.sale_id == sale_id,
            models.SaleReturnItem.item_id == it["item_id"]
        ).all()
        total_prev = sum(p.qty for p in prev_returned)

        if total_prev + it["qty"] > sale_item.qty:
            raise HTTPException(400, f"Qty retur {item.name} melebihi qty terjual ({sale_item.qty - total_prev} tersisa)")

        # Prioritaskan harga dari frontend jika ada
        input_price = it.get("price")
        actual_price = input_price if input_price is not None else sale_item.sell_price

        gross_line = it["qty"] * actual_price
        # Pisahkan PPN agar KONSISTEN dgn jurnal JUAL: 4-1200 (Retur Penjualan) menerima nilai
        # EX-PPN & 2-1200 (PPN Keluaran) ikut dibalik. Tarif PER BARIS: utamakan tarif yang
        # TERSIMPAN di baris penjualan (akurat utk faktur tarif-campur); penjualan LAMA (ppn baris
        # 0) → pakai tarif header sbg cadangan. Inklusif → kupas PPN MUNDUR (mirror
        # hitung_total_penjualan di sales.py); eksklusif → PPN di atas. Tarif 0 → tanpa PPN.
        line_tarif = sale_item.ppn_percent
        if not line_tarif or line_tarif <= 0:
            line_tarif = tax_percent or 0
        if line_tarif and line_tarif > 0:
            if is_tax_included:
                line_sales = gross_line / (1 + line_tarif / 100)
                line_tax = gross_line - line_sales
            else:
                line_sales = gross_line
                line_tax = gross_line * (line_tarif / 100)
        else:
            line_sales = gross_line
            line_tax = 0.0
        total_sales += line_sales
        total_tax += line_tax
        
        # COGS yang dibalik dihitung di bawah (blok "Kembalikan stok"): dari biaya lapisan FIFO
        # yang NYATA dipulihkan (mode gudang) atau rata-rata buy_price (mode tanpa gudang).
        db.add(models.SaleReturnItem(
            return_id=retur.id, item_id=it["item_id"],
            qty=it["qty"], price=actual_price, total=line_sales + line_tax
        ))

        # Kembalikan stok
        stock_item = item
        if is_virtual_variant(item):
            stock_item = db.query(models.Item).with_for_update().get(item.parent_item_id) or item

        required_qty = get_required_stock_qty(item, it["qty"])
        before = float(stock_item.stock or 0)
        stock_item.stock += required_qty
        if gudang_aktif:
            from .warehouse import adjust_warehouse_stock
            adjust_warehouse_stock(db, gudang_aktif.id, stock_item.id, required_qty)
            # 🧱 FIFO: pulihkan lapisan yang dulu dikonsumsi penjualan ini (retur bisa
            # sebagian) agar Σ batch == stok tetap terjaga & barang siap dijual lagi
            # dengan biaya asalnya. Nilai kembalian = biaya modal NYATA lapisan yang
            # dipulihkan → dipakai sebagai COGS yang dibalik supaya GL Persediaan/HPP tetap
            # sama dengan ledger batch (anti-drift pada retur sebagian).
            from ..services.inventory_fifo import restore_sale_return
            total_cogs += restore_sale_return(
                db,
                sale_item_id=sale_item.id,
                qty=required_qty,
                item_id=stock_item.id,
                warehouse_id=gudang_aktif.id,
                fallback_cost=(sale_item.buy_price or 0),
            )
        else:
            # Mode tanpa gudang (tak ada lapisan FIFO) → rata-rata buy_price seperti semula.
            total_cogs += it["qty"] * (sale_item.buy_price or 0)
        db.add(models.StockMovement(
            date=get_local_date(),
            item_id=stock_item.id,
            branch_id=sale.branch_id,
            type="in",
            qty=required_qty,
            qty_before=before,
            qty_after=stock_item.stock,
            reference=number,
            notes=(
                f"Retur Penjualan {sale.number} - restore dari {item.name}"
                if stock_item.id != item.id
                else f"Retur Penjualan {sale.number}"
            ),
        ))

    total = total_sales + total_tax
    retur.total = total
    
    # Update Customer Deposit Balance
    if sale.customer_id:
        cust = db.query(models.Customer).with_for_update().get(sale.customer_id)
        if cust:
            cust.deposit_balance = (cust.deposit_balance or 0) + total
            
    # Buat jurnal DI DALAM transaksi yang sama dengan mutasi stok. Bila jurnal gagal,
    # seluruh retur (stok + saldo deposit) ikut rollback → tidak ada lagi "stok pindah tanpa
    # jurnal". Akun wajib sudah dipastikan ada di awal sehingga ini praktis tidak akan gagal.
    from ..services import journal_service
    journal_service.create_sale_return_journal(
        db,
        date_val=retur.date,
        number_ref=retur.number,
        customer_name=sale.customer.name if sale.customer else "Umum",
        total_sales=total_sales,
        total_tax=total_tax,
        total_cogs=total_cogs,
        is_tax_included=is_tax_included,
        user_id=current_user.id,
        branch_id=sale.branch_id
    )

    write_audit(db, current_user.id, "CREATE", "sale_returns", retur.id,
                f"Retur penjualan {sale.number} sebesar {total}")
    db.commit()
    return {"id": retur.id, "number": retur.number, "total": total, "message": "Retur penjualan berhasil"}


# ══════════════════════════════════════════════════════════════════════════════
# RETUR PEMBELIAN
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/purchases")
def get_purchase_returns(skip: int = 0, limit: int = 100,
                          db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    query = db.query(models.PurchaseReturn).join(models.Purchase)
    if current_user.active_branch_id:
        query = query.filter(models.Purchase.branch_id == current_user.active_branch_id)
        
    returns = query.order_by(models.PurchaseReturn.id.desc()).offset(skip).limit(limit).all()
    result = []
    for r in returns:
        purchase = r.purchase
        items_out = []
        for i in r.items:
            item = db.query(models.Item).get(i.item_id)
            items_out.append({
                "id": i.id, "item_id": i.item_id,
                "item_name": item.name if item else "-",
                "qty": i.qty, "price": i.price, "total": i.total
            })
        result.append({
            "id": r.id, "number": r.number, "date": str(r.date),
            "purchase_id": r.purchase_id,
            "purchase_number": purchase.number if purchase else "-",
            "supplier_name": purchase.supplier.name if purchase and purchase.supplier else "-",
            "total": r.total,
            "total_carrying": r.total_carrying, "selisih": r.selisih,
            "reason": r.reason, "notes": r.notes,
            "items": items_out
        })
    return result


@router.post("/purchases")
def create_purchase_return(data: dict, db: Session = Depends(get_db),
                            current_user: models.User = Depends(get_current_user)):
    purchase_id = data.get("purchase_id")
    purchase = db.query(models.Purchase).get(purchase_id)
    if not purchase: raise HTTPException(404, "Pembelian tidak ditemukan")

    gudang_aktif = None
    if purchase.branch_id:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == purchase.branch_id,
            models.Warehouse.is_default == True,
        ).first()

    number = _next_number(db, "RP", models.PurchaseReturn)
    total_inventory = 0.0   # nilai refund barang (qty × harga retur)
    total_carrying = 0.0    # biaya modal FIFO nyata yang keluar (untuk selisih harga)
    total_tax = 0.0
    
    # Pajak retur selalu mengikuti faktur asli. Nilai dari frontend hanya
    # dipakai untuk tampilan lama, bukan sebagai sumber perhitungan akuntansi.
    tax_type = purchase_tax_type(purchase)
    is_tax_included = tax_type == "include"
    tax_percent = float(getattr(purchase, "tax_percent", 0) or 0)

    # 🔒 Pastikan akun jurnal retur tersedia SEBELUM stok dimutasi, supaya jurnal tidak gagal
    # di tengah jalan dan meninggalkan stok berpindah tanpa pencatatan GL. (Akun selisih
    # 4-2000/5-1200 sengaja TIDAK dipaksa ada — sudah ada fallback 2-kaki di journal_service.)
    from .accounting import pastikan_akun_ada
    _akun_wajib = ["1-1600", "1-1400"]
    if getattr(purchase, "ppn_dipisah", False):
        _akun_wajib.append("1-1550")  # PKP: PPN Masukan (1-1550) ikut dibalik saat retur
    elif tax_type == "exclude" and tax_percent > 0:
        _akun_wajib.append("5-2000")
    pastikan_akun_ada(db, _akun_wajib)

    retur = models.PurchaseReturn(
        number=number,
        date=get_local_date(),
        purchase_id=purchase_id,
        tax_percent=tax_percent,
        is_tax_included=is_tax_included,
        reason=data.get("reason"),
        notes=data.get("notes")
    )
    db.add(retur); db.flush()

    for it in data.get("items", []):
        item = db.query(models.Item).with_for_update().get(it["item_id"])
        if not item: raise HTTPException(404, f"Item {it['item_id']} tidak ditemukan")

        pur_item = next((pi for pi in purchase.items if pi.item_id == it["item_id"]), None)
        if not pur_item:
            raise HTTPException(400, f"Item {item.name} tidak ada di pembelian ini")

        # Prioritaskan harga dari frontend jika ada
        input_price = it.get("price")
        actual_price = input_price if input_price is not None else pur_item.buy_price

        line_gross = it["qty"] * actual_price
        if tax_type == "include" and getattr(purchase, "ppn_dipisah", False):
            # PKP + Included: harga retur sudah termasuk PPN → kupas mundur PER-BARIS pakai tarif
            # saat beli (pur_item.ppn_percent; mundur ke tarif header bila kosong). total_inventory =
            # NET, total_tax = PPN → jurnal membalik PPN Masukan (1-1550).
            r = float(pur_item.ppn_percent) if getattr(pur_item, "ppn_percent", None) is not None else float(tax_percent or 0)
            line_inventory = line_gross / (1 + r / 100) if r > 0 else line_gross
            line_tax = line_gross - line_inventory
        elif tax_type == "include":
            # non-PKP Included: PPN melebur di modal (tak dipisah) → perilaku lama, tanpa kaki pajak.
            line_inventory = line_gross
            line_tax = 0.0
        elif tax_type == "exclude":
            # Excluded: harga ex-PPN, PPN ditambah di atas (satu tarif header).
            line_inventory = line_gross
            line_tax = line_inventory * (tax_percent / 100)
        else:
            # Tanpa PPN: harga barang dikembalikan apa adanya.
            line_inventory = line_gross
            line_tax = 0.0
        total_inventory += line_inventory
        total_tax += line_tax

        db.add(models.PurchaseReturnItem(
            return_id=retur.id, item_id=it["item_id"],
            qty=it["qty"], price=actual_price, total=line_inventory + line_tax
        ))

        # Jika retur ini berasal dari barang rusak (Tukar Tambah)
        if it.get("trade_in_return_item_id"):
            trade_in_item = db.query(models.TradeInReturnItem).with_for_update().get(it["trade_in_return_item_id"])
            if trade_in_item:
                trade_in_item.returned_qty = (trade_in_item.returned_qty or 0) + it["qty"]
        
        # Kurangi stok
        if gudang_aktif:
            from .warehouse import get_warehouse_stock, adjust_warehouse_stock
            stok_lokal = get_warehouse_stock(db, gudang_aktif.id, item.id)
            if stok_lokal < it["qty"]:
                raise HTTPException(400, f"Stok {item.name} tidak cukup untuk retur pembelian.")
        elif item.stock < it["qty"]:
            raise HTTPException(400, f"Stok {item.name} tidak cukup untuk retur pembelian.")

        before = item.stock
        item.stock -= it["qty"]
        # Default (mode global / fallback): modal = nilai refund → tanpa selisih.
        line_carrying = line_inventory
        if gudang_aktif:
            adjust_warehouse_stock(db, gudang_aktif.id, item.id, -it["qty"])
            # 🧱 FIFO: kurangi lapisan — utamakan batch dari pembelian yang diretur.
            # Bila batch itu sudah terjual (FIFO), fungsi ini mundur ke batch terbaru
            # supaya Σ batch == stok tetap terjaga (kasus koreksi biaya/"swap" lintas
            # supplier ditangani terpisah di Fase 3).
            from ..services.inventory_fifo import reduce_batches_for_reversal
            cost_consumed, leftover = reduce_batches_for_reversal(
                db,
                item_id=item.id,
                warehouse_id=gudang_aktif.id,
                qty=it["qty"],
                prefer_purchase_item_id=pur_item.id,
            )
            # Modal nyata = biaya batch yang keluar; porsi leftover (drift) dinilai =
            # harga retur agar tidak memunculkan selisih palsu pada porsi itu.
            line_carrying = cost_consumed + leftover * actual_price
        total_carrying += line_carrying
        db.add(models.StockMovement(
            date=get_local_date(),
            item_id=item.id,
            branch_id=purchase.branch_id,
            type="out",
            qty=it["qty"],
            qty_before=before, qty_after=item.stock,
            reference=number, notes=f"Retur Pembelian {purchase.number}"
        ))

    total = total_inventory + total_tax
    retur.total = total
    # Keterlacakan selisih retur (untung+/rugi−) — sejajar dgn variance jurnal di
    # journal_service.create_purchase_return_journal: mode gudang/FIFO pakai modal NYATA yang
    # keluar; mode PKP (ppn_dipisah) hitung selisih pada nilai NET; tanpa gudang → tak ada selisih.
    if gudang_aktif:
        retur.total_carrying = total_carrying
        if getattr(purchase, "ppn_dipisah", False) and total_tax > 0.005:
            retur.selisih = total_inventory - total_carrying
        else:
            retur.selisih = (total_inventory + total_tax) - total_carrying
    else:
        retur.total_carrying = total_inventory  # tanpa lapisan FIFO: modal = nilai refund
        retur.selisih = 0.0

    # Update Supplier Deposit Balance
    if purchase.supplier_id:
        supp = db.query(models.Supplier).with_for_update().get(purchase.supplier_id)
        if supp:
            supp.deposit_balance = (supp.deposit_balance or 0) + total
            
    # Buat jurnal DI DALAM transaksi yang sama dengan mutasi stok → bila jurnal gagal, seluruh
    # retur (stok + saldo supplier) ikut rollback. Akun wajib sudah dipastikan ada di awal.
    from ..services import journal_service
    journal_service.create_purchase_return_journal(
        db,
        date_val=retur.date,
        number_ref=retur.number,
        supplier_name=purchase.supplier.name if purchase.supplier else "-",
        total_inventory=total_inventory,
        # Mode gudang → kirim biaya landed nyata; mode tanpa gudang → None (pajak dibalik terpisah).
        total_carrying=(total_carrying if gudang_aktif else None),
        total_tax=total_tax,
        is_tax_included=is_tax_included,
        # Ikuti mode PPN saat faktur DIBELI (tersimpan di faktur), bukan saklar PKP saat ini.
        ppn_dipisah=bool(getattr(purchase, "ppn_dipisah", False)),
        user_id=current_user.id,
        branch_id=purchase.branch_id
    )

    write_audit(db, current_user.id, "CREATE", "purchase_returns", retur.id, f"Retur {purchase.number}")
    db.commit()
    return {
        "id": retur.id, "number": retur.number, "total": total,
        "total_carrying": round(retur.total_carrying or 0, 2),
        "selisih": round(retur.selisih or 0, 2),
        "message": "Retur pembelian berhasil",
    }


# ══════════════════════════════════════════════════════════════════════════════
# FASE 3 — SWAP BATCH ANTAR-SUPPLIER + RESTATEMENT HPP
# ══════════════════════════════════════════════════════════════════════════════
# Skenario: barang supplier A sudah "terjual" menurut FIFO (batch A habis), tapi
# pengguna ingin meretur barang A. Solusi: tukar alokasi penjualan dari batch A ke
# batch supplier lain (B) yang stoknya masih ada → barang A kembali on-hand & bisa
# diretur. Jurnal HPP penjualan lama dikoreksi (bertanggal periode asal, boleh sudah
# Tutup Buku); karena laporan dihitung live, neraca/laba-rugi lama otomatis update.

EPS_SWAP = 1e-6


@router.get("/swap-candidates")
def get_swap_candidates(
    purchase_id: int,
    item_id: int,
    qty: float,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Cek apakah retur pembelian ini butuh SWAP. Bila batch dari pembelian tsb sudah
    kurang dari qty yang mau diretur (terjual FIFO), kembalikan daftar batch item yang
    sama dari SUPPLIER LAIN yang stoknya masih ada untuk ditukar."""
    purchase = db.query(models.Purchase).get(purchase_id)
    if not purchase:
        raise HTTPException(404, "Pembelian tidak ditemukan")
    pur_item = next((pi for pi in purchase.items if pi.item_id == item_id), None)
    if not pur_item:
        raise HTTPException(400, "Item tidak ada di pembelian ini")

    gudang_aktif = None
    if purchase.branch_id:
        gudang_aktif = db.query(models.Warehouse).filter(
            models.Warehouse.branch_id == purchase.branch_id,
            models.Warehouse.is_default == True,
        ).first()
    if not gudang_aktif:
        return {"needs_swap": False, "reason": "Mode tanpa gudang — retur biasa."}

    from_batch = db.query(models.StockBatch).filter(
        models.StockBatch.purchase_item_id == pur_item.id,
        models.StockBatch.warehouse_id == gudang_aktif.id,
    ).first()
    if not from_batch:
        return {"needs_swap": False, "reason": "Pembelian ini belum punya lapisan FIFO."}

    on_hand = float(from_batch.qty_remaining or 0)
    shortfall = float(qty) - on_hand
    if shortfall <= EPS_SWAP:
        return {"needs_swap": False, "from_batch_id": from_batch.id, "on_hand": round(on_hand, 4)}

    # Hanya qty yang benar-benar "terjual" dari batch A yang bisa di-swap balik
    swappable = float(
        db.query(func.coalesce(func.sum(models.SaleItemBatch.qty), 0.0))
        .filter(models.SaleItemBatch.batch_id == from_batch.id)
        .scalar()
        or 0.0
    )

    cands = (
        db.query(models.StockBatch)
        .filter(
            models.StockBatch.item_id == item_id,
            models.StockBatch.warehouse_id == gudang_aktif.id,
            models.StockBatch.qty_remaining > EPS_SWAP,
            models.StockBatch.id != from_batch.id,
        )
        .order_by(models.StockBatch.received_date.desc(), models.StockBatch.id.desc())
        .all()
    )
    candidates = []
    for b in cands:
        supp = db.query(models.Supplier).get(b.supplier_id) if b.supplier_id else None
        candidates.append({
            "batch_id": b.id,
            "supplier_id": b.supplier_id,
            "supplier_name": supp.name if supp else "Tanpa supplier / saldo awal",
            "unit_cost": float(b.unit_cost or 0),
            "qty_remaining": float(b.qty_remaining or 0),
            "received_date": str(b.received_date),
        })

    return {
        "needs_swap": True,
        "from_batch_id": from_batch.id,
        "from_supplier_name": purchase.supplier.name if purchase.supplier else "-",
        "from_unit_cost": float(from_batch.unit_cost or 0),
        "on_hand": round(on_hand, 4),
        "shortfall": round(shortfall, 4),
        "swappable_sold_qty": round(swappable, 4),
        "candidates": candidates,
    }


@router.post("/swap-batch")
def swap_batch(
    data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    """Pindahkan alokasi penjualan sebanyak `qty` (satuan dasar) dari from_batch (A)
    ke to_batch (B), lalu posting jurnal KOREKSI HPP bertanggal di periode penjualan
    asal (boleh sudah tutup buku). Tidak mengubah WarehouseStock — Σ batch == stok
    tetap terjaga. ADMIN-ONLY + audit."""
    from .accounting import create_auto_journal, get_books_locked_until
    from ..services.journal_service import ACCOUNT_COGS, ACCOUNT_INVENTORY

    qty = float(data.get("qty") or 0)
    reason = data.get("reason")
    if qty <= 0:
        raise HTTPException(400, "qty harus > 0")

    A = db.query(models.StockBatch).with_for_update().get(data.get("from_batch_id"))
    B = db.query(models.StockBatch).with_for_update().get(data.get("to_batch_id"))
    if not A or not B:
        raise HTTPException(404, "Batch tidak ditemukan")
    if A.id == B.id:
        raise HTTPException(400, "Batch asal & tujuan tidak boleh sama")
    if A.item_id != B.item_id or A.warehouse_id != B.warehouse_id:
        raise HTTPException(400, "Batch harus item & gudang yang sama")
    if float(B.qty_remaining or 0) + EPS_SWAP < qty:
        raise HTTPException(400, f"Stok batch tujuan tidak cukup untuk swap (sisa {B.qty_remaining}).")

    allocs = (
        db.query(models.SaleItemBatch)
        .join(models.SaleItem, models.SaleItem.id == models.SaleItemBatch.sale_item_id)
        .join(models.Sale, models.Sale.id == models.SaleItem.sale_id)
        .filter(
            models.SaleItemBatch.batch_id == A.id,
            models.SaleItemBatch.qty > EPS_SWAP,
            models.Sale.status != "cancelled",
        )
        .order_by(models.Sale.date.desc(), models.SaleItem.id.desc())
        .all()
    )
    total_sold_A = sum(float(a.qty) for a in allocs)
    if total_sold_A + EPS_SWAP < qty:
        raise HTTPException(
            400,
            f"Hanya {round(total_sold_A, 4)} unit dari batch ini yang tercatat terjual; tak bisa swap {qty}.",
        )

    from_cost = float(A.unit_cost or 0)
    to_cost = float(B.unit_cost or 0)
    remaining = qty
    total_delta = 0.0
    affected = []

    for a in allocs:
        if remaining <= EPS_SWAP:
            break
        take = min(float(a.qty), remaining)
        si = a.sale_item
        sale = si.sale
        cost_delta = (to_cost - from_cost) * take

        # 1) pindahkan alokasi A -> B
        a.qty = float(a.qty) - take
        if a.qty <= EPS_SWAP:
            db.delete(a)
        b_alloc = (
            db.query(models.SaleItemBatch)
            .filter(
                models.SaleItemBatch.sale_item_id == si.id,
                models.SaleItemBatch.batch_id == B.id,
            )
            .first()
        )
        if b_alloc:
            b_alloc.qty = float(b_alloc.qty) + take
            b_alloc.unit_cost = to_cost
        else:
            db.add(models.SaleItemBatch(sale_item_id=si.id, batch_id=B.id, qty=take, unit_cost=to_cost))

        # 2) pindahkan lapisan fisik: A kembali on-hand, B dianggap terjual
        A.qty_remaining = float(A.qty_remaining) + take
        B.qty_remaining = float(B.qty_remaining) - take

        # 3) koreksi HPP baris jual (incremental → aman thd porsi fallback)
        if si.qty:
            si.buy_price = float(si.buy_price or 0) + (cost_delta / si.qty)

        # 4) jurnal koreksi bertanggal sale.date (boleh sudah tutup buku)
        jid = None
        if abs(cost_delta) > 0.005:
            if cost_delta > 0:  # HPP naik (B lebih mahal): Dr HPP / Cr Persediaan
                entries = [
                    {"code": ACCOUNT_COGS, "debit": cost_delta, "credit": 0},
                    {"code": ACCOUNT_INVENTORY, "debit": 0, "credit": cost_delta},
                ]
            else:               # HPP turun (B lebih murah): Dr Persediaan / Cr HPP
                d = -cost_delta
                entries = [
                    {"code": ACCOUNT_INVENTORY, "debit": d, "credit": 0},
                    {"code": ACCOUNT_COGS, "debit": 0, "credit": d},
                ]
            j = create_auto_journal(
                db,
                date_val=sale.date,
                number_ref=f"SWAP-{sale.number}",
                description=(
                    f"Koreksi HPP swap batch (Sale {sale.number}): "
                    f"{from_cost:.0f} -> {to_cost:.0f} x {round(take, 4)}"
                ),
                entries=entries,
                user_id=current_user.id,
                branch_id=sale.branch_id,
                allow_closed_period=True,
            )
            jid = j.id

        locked_until = get_books_locked_until(db, sale.branch_id)
        was_locked = bool(locked_until and sale.date <= locked_until)

        db.add(models.Restatement(
            sale_item_id=si.id,
            from_batch_id=A.id,
            to_batch_id=B.id,
            qty=take,
            cost_delta=cost_delta,
            correction_journal_id=jid,
            period_was_locked=was_locked,
            sale_date=sale.date,
            reason=reason,
            created_by=current_user.id,
        ))

        affected.append({
            "sale_id": sale.id,
            "sale_number": sale.number,
            "sale_date": str(sale.date),
            "qty": round(take, 4),
            "cost_delta": round(cost_delta, 2),
            "journal_id": jid,
            "period_was_locked": was_locked,
        })
        total_delta += cost_delta
        remaining -= take

    write_audit(
        db, current_user.id, "SWAP_BATCH", "stock_batches", A.id,
        f"Swap {round(qty,4)} unit batch#{A.id}->#{B.id}; koreksi HPP {round(total_delta,2)}; "
        f"{len(affected)} penjualan; sebagian periode terkunci="
        f"{any(x['period_was_locked'] for x in affected)}",
    )
    db.commit()

    return {
        "success": True,
        "swapped_qty": round(qty, 4),
        "total_cost_delta": round(total_delta, 2),
        "freed_on_hand_from_batch": round(float(A.qty_remaining or 0), 4),
        "affected_sales": affected,
        "message": (
            f"Swap berhasil: {round(qty,4)} unit dipindah dari batch#{A.id} ke batch#{B.id}. "
            f"Stok supplier asal kini bisa diretur. Koreksi HPP total Rp{round(total_delta,2)} "
            f"diposting ke {len(affected)} periode penjualan."
        ),
    }
