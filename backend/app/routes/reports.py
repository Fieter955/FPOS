from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import Optional
from datetime import date
from dateutil.relativedelta import relativedelta
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from ..database import get_db
from .. import models
from ..auth import get_current_user

router = APIRouter()

# ─── 1. DASHBOARD KPI (UTAMA) ────────────────────────────────────────────────
@router.get("/dashboard")
def get_dashboard_data(db: Session = Depends(get_db), _=Depends(get_current_user)):
    today = date.today()
    today_str = today.isoformat() # "YYYY-MM-DD"

    # Perbaikan Filter Tanggal untuk SQLite (Gunakan .contains)
    total_sales_today = db.query(func.sum(models.Sale.total)).filter(
        models.Sale.date.contains(today_str),
        models.Sale.status != "cancelled"
    ).scalar() or 0

    total_purchases_today = db.query(func.sum(models.Purchase.total)).filter(
        models.Purchase.date.contains(today_str)
    ).scalar() or 0

    total_tx_today = db.query(func.count(models.Sale.id)).filter(
        models.Sale.date.contains(today_str),
        models.Sale.status != "cancelled"
    ).scalar() or 0

    low_stock = db.query(func.count(models.Item.id)).filter(
        models.Item.is_active == True, 
        models.Item.stock <= models.Item.min_stock
    ).scalar() or 0

    # Top 5 Produk Terlaris Bulan Ini
    top_items_raw = db.query(
        models.Item.name,
        func.sum(models.SaleItem.qty).label("total_qty"),
        func.sum(models.SaleItem.total).label("total_amount")
    ).join(models.SaleItem).join(models.Sale).filter(
        models.Sale.date.contains(today.strftime("%Y-%m")),
        models.Sale.status != "cancelled"
    ).group_by(models.Item.id).order_by(func.sum(models.SaleItem.qty).desc()).limit(5).all()
    
    top_items = [{"name": r[0], "qty": r[1], "amount": r[2]} for r in top_items_raw]

    # 5 Penjualan Terbaru
    recent_sales = [{
        "number": s.number, 
        "date": str(s.date),
        "customer": s.customer.name if s.customer else "Umum",
        "total": s.total, 
        "status": s.status
    } for s in db.query(models.Sale).order_by(models.Sale.id.desc()).limit(5).all()]

    # Grafik 6 Bulan Terakhir
    monthly = []
    for i in range(5, -1, -1):
        d = today - relativedelta(months=i)
        m_str = d.strftime("%Y-%m")
        amt = db.query(func.sum(models.Sale.total)).filter(
            models.Sale.date.contains(m_str),
            models.Sale.status != "cancelled"
        ).scalar() or 0
        monthly.append({"month": d.strftime("%b %Y"), "amount": float(amt)})

    return {
        "total_sales_today": float(total_sales_today),
        "total_purchases_today": float(total_purchases_today),
        "total_transactions_today": int(total_tx_today),
        "low_stock_count": int(low_stock),
        "top_items": top_items,
        "recent_sales": recent_sales,
        "monthly_sales": monthly
    }

# ─── 2. LABA RUGI ────────────────────────────────────────────────────────────
@router.get("/profit-loss")
def profit_loss(start_date: Optional[date] = None, end_date: Optional[date] = None, db: Session = Depends(get_db)):
    if not start_date: start_date = date.today().replace(day=1)
    if not end_date: end_date = date.today()

    sales = db.query(models.Sale).filter(
        models.Sale.date >= start_date, 
        models.Sale.date <= end_date,
        models.Sale.status != "cancelled"
    ).all()

    total_revenue = sum(s.total for s in sales)
    total_discount = sum(s.discount for s in sales)
    
    # Hitung HPP
    hpp = 0.0
    for sale in sales:
        for si in sale.items:
            item = db.query(models.Item).filter(models.Item.id == si.item_id).first()
            if item: hpp += (si.qty * (item.buy_price or 0))

    expenses = db.query(func.sum(models.CashTransaction.amount)).filter(
        models.CashTransaction.type == "out",
        models.CashTransaction.date >= start_date,
        models.CashTransaction.date <= end_date
    ).scalar() or 0

    net_profit = (total_revenue - hpp) - expenses

    return {
        "total_revenue": float(total_revenue),
        "hpp": float(hpp),
        "expenses": float(expenses),
        "net_profit": float(net_profit),
        "transaction_count": len(sales)
    }

# ─── 3. HUTANG & PIUTANG ──────────────────────────────────────────────────────
@router.get("/receivables")
def receivables(db: Session = Depends(get_db)):
    sales = db.query(models.Sale).filter(models.Sale.status.in_(["unpaid", "partial"])).all()
    return [{"number": s.number, "customer": s.customer.name if s.customer else "Umum", "remaining": s.total - s.paid} for s in sales]

@router.get("/payables")
def payables(db: Session = Depends(get_db)):
    purchases = db.query(models.Purchase).filter(models.Purchase.status.in_(["unpaid", "partial"])).all()
    return [{"number": p.number, "supplier": p.supplier.name if p.supplier else "-", "remaining": p.total - p.paid} for p in purchases]

# ─── 4. EXPORT EXCEL SALES ───────────────────────────────────────────────────
@router.get("/export/sales")
def export_sales(start_date: Optional[date] = None, end_date: Optional[date] = None, db: Session = Depends(get_db)):
    if not start_date: start_date = date.today().replace(day=1)
    if not end_date: end_date = date.today()

    sales = db.query(models.Sale).filter(models.Sale.date >= start_date, models.Sale.date <= end_date).all()
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["No. Faktur", "Tanggal", "Pelanggan", "Total", "Status"])
    
    for s in sales:
        ws.append([s.number, str(s.date), s.customer.name if s.customer else "Umum", s.total, s.status])
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=sales_{start_date}.xlsx"}
    )