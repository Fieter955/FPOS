from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, String, cast
from typing import Optional
from datetime import date
from dateutil.relativedelta import relativedelta
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import pytz
from datetime import datetime

from ..database import get_db
from .. import models
from ..auth import get_current_user, get_query

# 👇 IMPORT LOGIKA DARI ACCOUNTING (SINGLE SOURCE OF TRUTH) 👇
from .accounting import get_income_statement 

router = APIRouter()

WITA = pytz.timezone("Asia/Makassar")

def get_local_date():
    return datetime.now(WITA).date()

# ─── 1. DASHBOARD KPI (UTAMA) ────────────────────────────────────────────────
@router.get("/dashboard")
def get_dashboard_data(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    today_local = get_local_date() 

    total_sales_today = get_query(db, models.Sale, current_user).filter(
        models.Sale.date == today_local,
        models.Sale.status != "cancelled"
    ).with_entities(func.sum(models.Sale.total)).scalar() or 0

    total_purchases_today = get_query(db, models.Purchase, current_user).filter(
        models.Purchase.date == today_local,
        models.Purchase.status != "cancelled"
    ).with_entities(func.sum(models.Purchase.total)).scalar() or 0

    total_tx_today = get_query(db, models.Sale, current_user).filter(
        models.Sale.date == today_local,
        models.Sale.status != "cancelled"
    ).count()

    # 👇 BARU: Ambil Laba Bersih Bulan Ini dari Modul Akuntansi
    start_month = today_local.replace(day=1)
    acc_report = get_income_statement(start_date=start_month, end_date=today_local, db=db, current_user=current_user)
    net_profit_month = acc_report.get("net_profit", 0)

    # Hitung stok menipis berdasarkan Gudang Cabang Aktif
    low_stock_count = 0
    gudang_cabang = db.query(models.Warehouse.id).filter(
        models.Warehouse.branch_id == current_user.active_branch_id
    ).all()
    warehouse_ids = [g[0] for g in gudang_cabang]

    if warehouse_ids:
        stock_per_item = db.query(
            models.WarehouseStock.item_id,
            func.sum(models.WarehouseStock.stock).label('total_local_stock')
        ).filter(
            models.WarehouseStock.warehouse_id.in_(warehouse_ids)
        ).group_by(models.WarehouseStock.item_id).subquery()

        low_stock_count = db.query(func.count(models.Item.id)).join(
            stock_per_item, models.Item.id == stock_per_item.c.item_id
        ).filter(
            models.Item.is_active == True,
            stock_per_item.c.total_local_stock <= models.Item.min_stock
        ).scalar() or 0
    elif not current_user.active_branch_id: 
        low_stock_count = db.query(func.count(models.Item.id)).filter(
            models.Item.is_active == True,
            models.Item.stock <= models.Item.min_stock
        ).scalar() or 0

    # Top 5 Produk Terlaris Bulan Ini (Filter Cabang)
    top_items_raw = get_query(db, models.Sale, current_user).join(
        models.SaleItem
    ).join(
        models.Item
    ).with_entities(
        models.Item.name,
        func.sum(models.SaleItem.qty).label("total_qty"),
        func.sum(models.SaleItem.total).label("total_amount")
    ).filter(
        cast(models.Sale.date, String).like(f"%{today_local.strftime('%Y-%m')}%"),
        models.Sale.status != "cancelled"
    ).group_by(models.Item.id).order_by(func.sum(models.SaleItem.qty).desc()).limit(5).all()
    
    top_items = [{"name": r[0], "qty": r[1], "amount": r[2]} for r in top_items_raw]

    # 5 Penjualan Terbaru (Filter Cabang)
    recent_sales = [{
        "number": s.number, 
        "date": str(s.date),
        "customer": s.customer.name if s.customer else "Umum",
        "total": s.total, 
        "status": s.status
    } for s in get_query(db, models.Sale, current_user).order_by(models.Sale.id.desc()).limit(5).all()]

    # Grafik 6 Bulan Terakhir (Filter Cabang)
    monthly = []
    for i in range(5, -1, -1):
        d = today_local - relativedelta(months=i)
        m_str = d.strftime("%Y-%m")
        amt = get_query(db, models.Sale, current_user).filter(
            cast(models.Sale.date, String).like(f"%{m_str}%"),
            models.Sale.status != "cancelled"
        ).with_entities(func.sum(models.Sale.total)).scalar() or 0
        monthly.append({"month": d.strftime("%b %Y"), "amount": float(amt)})

    return {
        "total_sales_today": float(total_sales_today),
        "total_purchases_today": float(total_purchases_today),
        "total_transactions_today": int(total_tx_today),
        "net_profit_monthly": float(net_profit_month), # 👈 Data Net Profit Akurat
        "low_stock_count": int(low_stock_count),
        "top_items": top_items,
        "recent_sales": recent_sales,
        "monthly_sales": monthly
    }

# ─── 2. LABA RUGI (SINKRON DENGAN AKUNTANSI) ─────────────────────────────────
@router.get("/profit-loss")
def profit_loss(
    start_date: Optional[date] = None, 
    end_date: Optional[date] = None, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    # 👇 REVISI TOTAL: Tanya langsung ke modul akuntansi
    acc_data = get_income_statement(start_date=start_date, end_date=end_date, db=db, current_user=current_user)
    
    return {
        # Format Baru (Lengkap)
        "total_revenue": acc_data.get("total_revenue", 0),
        "total_cogs": acc_data.get("total_cogs", 0),
        "total_operating_expense": acc_data.get("total_operating_expense", 0),
        "total_other_expense": acc_data.get("total_other_expense", 0),
        "net_profit": acc_data.get("net_profit", 0),
        "period": acc_data.get("period"),
        
        # Format Lama (Backward Compatibility untuk mencegah Frontend Error)
        "hpp": acc_data.get("total_cogs", 0), 
        "expenses": acc_data.get("total_operating_expense", 0) + acc_data.get("total_other_expense", 0),
        "transaction_count": get_query(db, models.Sale, current_user).filter(
            models.Sale.date >= (start_date or get_local_date().replace(day=1)), 
            models.Sale.date <= (end_date or get_local_date()),
            models.Sale.status != "cancelled"
        ).count()
    }

# ─── 3. HUTANG & PIUTANG ──────────────────────────────────────────────────────
@router.get("/receivables")
def receivables(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    sales = get_query(db, models.Sale, current_user).filter(
        models.Sale.status.in_(["unpaid", "partial"])
    ).all()
    return [{"number": s.number, "customer": s.customer.name if s.customer else "Umum", "remaining": s.total - s.paid} for s in sales]

@router.get("/payables")
def payables(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    purchases = get_query(db, models.Purchase, current_user).filter(
        models.Purchase.status.in_(["unpaid", "partial"])
    ).all()
    
    return [{
        "id": p.id,
        "date": p.date,
        "number": p.number, 
        "supplier": p.supplier.name if p.supplier else "-", 
        "total": p.total,
        "paid": p.paid,
        "remaining": p.total - p.paid
    } for p in purchases]

# ─── 4. EXPORT EXCEL SALES ───────────────────────────────────────────────────
@router.get("/export/sales")
def export_sales(
    start_date: Optional[date] = None, 
    end_date: Optional[date] = None, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    today_local = get_local_date()
    if not start_date: start_date = today_local.replace(day=1)
    if not end_date: end_date = today_local

    sales = get_query(db, models.Sale, current_user).filter(
        models.Sale.date >= start_date, 
        models.Sale.date <= end_date,
        models.Sale.status != "cancelled" 
    ).all()
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["No. Faktur", "Tanggal", "Cabang ID", "Pelanggan", "Total", "Status"])
    
    for s in sales:
        ws.append([s.number, str(s.date), s.branch_id or "Pusat", s.customer.name if s.customer else "Umum", s.total, s.status])
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    cbg_name = f"Cabang_{current_user.active_branch_id}" if current_user.active_branch_id else "Semua_Cabang"
    
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Laporan_Penjualan_{cbg_name}_{start_date}_to_{end_date}.xlsx"}
    )