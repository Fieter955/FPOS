from fastapi import APIRouter, Depends, HTTPException
import pytz
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import date, datetime
from ..database import get_db
from .. import models
from ..auth import get_current_user, write_audit
from ..services.virtual_units import get_required_stock_qty, is_virtual_variant

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

    retur = models.SaleReturn(
        number=number,
        date=data.get("date", str(date.today())),
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

        line_sales = it["qty"] * sale_item.sell_price
        total_sales += line_sales
        
        line_tax = 0.0
        if not is_tax_included:
            line_tax = line_sales * (tax_percent / 100)
        total_tax += line_tax
        
        # Hitung COGS (Harga Beli saat itu)
        total_cogs += it["qty"] * (sale_item.buy_price or 0)

        db.add(models.SaleReturnItem(
            return_id=retur.id, item_id=it["item_id"],
            qty=it["qty"], price=sale_item.sell_price, total=line_sales + line_tax
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
        db.add(models.StockMovement(
            date=data.get("date", str(date.today())),
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
    db.commit(); db.refresh(retur)
    
    # Buat Jurnal
    try:
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
    except Exception as e:
        print(f"⚠ Gagal buat jurnal retur: {e}")

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
            "total": r.total, "reason": r.reason, "notes": r.notes,
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
    total_inventory = 0.0
    total_tax = 0.0
    
    # Ambil info pajak dari pembelian asli jika tidak dikirim dari frontend
    is_tax_included = data.get("is_tax_included", purchase.is_tax_included if hasattr(purchase, 'is_tax_included') else True)
    tax_percent = data.get("tax_percent", purchase.tax_percent if hasattr(purchase, 'tax_percent') else 0.0)

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

        line_inventory = it["qty"] * pur_item.buy_price
        total_inventory += line_inventory
        
        line_tax = 0.0
        if not is_tax_included:
            line_tax = line_inventory * (tax_percent / 100)
        total_tax += line_tax

        db.add(models.PurchaseReturnItem(
            return_id=retur.id, item_id=it["item_id"],
            qty=it["qty"], price=pur_item.buy_price, total=line_inventory + line_tax
        ))
        
        # ... rest of stock reduction logic ...

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
        if gudang_aktif:
            adjust_warehouse_stock(db, gudang_aktif.id, item.id, -it["qty"])
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
    db.commit(); db.refresh(retur)
    
    # Buat Jurnal
    try:
        from ..services import journal_service
        journal_service.create_purchase_return_journal(
            db,
            date_val=retur.date,
            number_ref=retur.number,
            supplier_name=purchase.supplier.name if purchase.supplier else "-",
            total_inventory=total_inventory,
            total_tax=total_tax,
            is_tax_included=is_tax_included,
            user_id=current_user.id,
            branch_id=purchase.branch_id
        )
    except Exception as e:
        print(f"⚠ Gagal buat jurnal retur: {e}")

    write_audit(db, current_user.id, "CREATE", "purchase_returns", retur.id, f"Retur {purchase.number}")
    db.commit()
    return {"id": retur.id, "number": retur.number, "total": total, "message": "Retur pembelian berhasil"}
