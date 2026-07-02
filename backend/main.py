import os
import sys

# 👇 PENAWAR ANTI-ERROR WINDOWS STARTUP 👇
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
# 👆 BATAS PENAWAR 👆

# 👇 AMANKAN STDOUT/STDERR (sebelum print apa pun di modul ini) 👇
# Frozen windowed: sys.stdout/stderr = None → print() menggagalkan seluruh startup.
# Frozen console : encoding default cp1252 → print emoji (✓ 👑 ⚠️) → UnicodeEncodeError → crash.
# Solusi: windowed → arahkan ke error_log.txt (UTF-8); console → reconfigure ke UTF-8.
if getattr(sys, 'frozen', False):
    if sys.stdout is None or sys.stderr is None:
        _logf = open(os.path.join(BASE_DIR, "error_log.txt"), "w", encoding="utf-8")
        sys.stdout = _logf
        sys.stderr = _logf
    else:
        for _s in (sys.stdout, sys.stderr):
            try:
                _s.reconfigure(encoding="utf-8")
            except Exception:
                pass
# 👆 BATAS AMAN STDOUT 👆
import subprocess
import os, threading, time, sys
import winreg
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
import uvicorn
from contextlib import asynccontextmanager

from app.database import Base, engine, SessionLocal
from app.auth import get_password_hash, verify_password
from app.config import settings
from app import models
from app.routes import (
    auth, items, customers, suppliers, purchases, sales,
    inventory, reports, accounting, consignment,
    shifts, returns, backup, ai_advisor,
    email_backup, updater,
    license, warehouse, assembly, notification, discounts, onboarding,
    unit_conversion, barcode_gen, delivery, trade_in, ai_bangunan, branches, employees, print_queue, sticker_gen, po
)

# ─── Create all DB tables ──────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─── Seed admin & Super Admin Fieter ──────────────────────────────────────────
def seed_admin():
    """Seed akun awal HANYA jika belum ada.

    PENTING: JANGAN me-reset password akun yang sudah ada di sini. Versi lama
    menimpa password 'admin'/'Fieter' tiap boot, sehingga client tidak pernah
    bisa benar-benar mengganti password (selalu balik tiap restart).
    Password vendor 'Fieter' diambil dari env VENDOR_BOOTSTRAP_PASSWORD bila ada.
    """
    db = SessionLocal()
    try:
        if not db.query(models.User).filter(models.User.username == "admin").first():
            db.add(models.User(
                username="admin", full_name="Administrator",
                hashed_password=get_password_hash("admin123"),
                role="admin", is_active=True
            ))
            print("✓ Akun 'admin' dibuat (default: admin123 — SEGERA GANTI password).")

        if not db.query(models.User).filter(models.User.username == "Fieter").first():
            vendor_pw = os.environ.get("VENDOR_BOOTSTRAP_PASSWORD") or "Fieter098"
            db.add(models.User(
                username="Fieter", full_name="Super Admin",
                hashed_password=get_password_hash(vendor_pw),
                role="admin", is_active=True
            ))
            print("👑 ✓ Akun Fieter dibuat.")

        db.commit()
    except Exception as e:
        print(f"⚠️ Gagal membuat akun otomatis: {e}")
    finally:
        db.close()

seed_admin()

# ─── Safe Migration ───────────────────────────────────────────────────────────
def run_migrations():
    import sqlite3
    db_path = "ipos.db"
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    def col_exists(table, col):
        try:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
            return col in cols
        except Exception:
            return False

    def add_col(table, col, definition):
        if not col_exists(table, col):
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                print(f"  ✓ Migration: {table}.{col} ditambahkan")
            except Exception as e:
                print(f"  ⚠ Skip {table}.{col}: {e}")

    add_col("customers", "credit_limit", "REAL DEFAULT 0")
    add_col("customers", "points",       "REAL DEFAULT 0")
    add_col("suppliers", "credit_limit", "REAL DEFAULT 0")
    add_col("items", "barcode",     "TEXT")
    add_col("items", "description", "TEXT")
    add_col("items", "updated_at",  "TEXT")
    add_col("items", "brand_id",   "INTEGER")
    add_col("items", "parent_item_id", "INTEGER")
    add_col("items", "conversion_factor_to_parent", "REAL DEFAULT 1")
    add_col("items", "is_virtual_variant", "INTEGER DEFAULT 0")
    add_col("items", "image_path", "TEXT")
    add_col("sales", "created_by", "INTEGER")
    add_col("sales", "change",     "REAL DEFAULT 0")
    add_col("sales", "salesperson_id",  "INTEGER")
    add_col("sales", "shift_id",       "INTEGER")
    add_col("sales", "notes",          "TEXT")
    add_col("customers", "loyalty_points", "REAL DEFAULT 0")
    add_col("customers", "group_id",       "INTEGER")
    add_col("customers", "address",        "TEXT")
    add_col("customers", "email",          "TEXT")
    add_col("purchases", "created_by", "INTEGER")
    add_col("purchases", "paid", "REAL DEFAULT 0")
    add_col("purchases", "status", "TEXT DEFAULT 'unpaid'")
    add_col("warehouses", "is_default", "INTEGER DEFAULT 0")
    add_col("unit_conversions", "buy_price",  "REAL DEFAULT 0")
    add_col("unit_conversions", "sell_price", "REAL DEFAULT 0")
    add_col("unit_conversions", "is_active",  "INTEGER DEFAULT 1")
    add_col("unit_conversions", "child_item_id", "INTEGER")
    add_col("delivery_notes", "signed_at",      "TEXT")
    add_col("delivery_notes", "recipient_name", "TEXT")
    add_col("licenses", "max_users",    "INTEGER DEFAULT 3")
    add_col("licenses", "owner_name",   "TEXT")
    add_col("licenses", "owner_email",  "TEXT")
    add_col("licenses", "billing_status", "TEXT DEFAULT 'ok'")
    add_col("licenses", "billing_message", "TEXT DEFAULT 'Aplikasi berjalan normal.'")
    add_col("users", "branch_id", "INTEGER")
    add_col("sales", "branch_id", "INTEGER DEFAULT 1") 
    add_col("purchases", "branch_id", "INTEGER DEFAULT 1")
    add_col("purchases", "is_branch_request", "INTEGER DEFAULT 0")
    add_col("purchases", "target_branch_id", "INTEGER")
    add_col("purchase_items", "qty_ordered", "FLOAT DEFAULT 0")
    add_col("purchase_items", "qty_received", "FLOAT DEFAULT 0")
    add_col("purchase_items", "ppn_percent", "REAL")  # tarif PPN per-baris beli; NULL → ikut tarif barang/toko (Included/PKP)
    add_col("shifts", "branch_id", "INTEGER DEFAULT 1")
    add_col("warehouses", "branch_id", "INTEGER DEFAULT 1")
    add_col("print_jobs", "content_type", "TEXT DEFAULT 'text'")
    add_col("item_supplier", "ppn_type", "TEXT DEFAULT 'included'")
    add_col("item_supplier", "ppn_percent", "REAL DEFAULT 0")
    add_col("items", "ppn_percent", "REAL")               # tarif PPN per-barang; NULL → ikut tarif toko (data lama tak berubah)
    add_col("sale_items", "ppn_percent", "REAL DEFAULT 0")  # tarif PPN baris penjualan → untuk balik PPN per-baris saat retur
    add_col("sales", "other_cost", "REAL DEFAULT 0")  # biaya lain ditagihkan ke pelanggan → Pendapatan Lain-lain (4-1500)
    add_col("suppliers", "ppn_type", "TEXT")
    add_col("branches", "is_pkp", "INTEGER DEFAULT 0")
    add_col("branches", "tarif_ppn", "REAL DEFAULT 11")
    add_col("purchases", "ppn_dipisah", "INTEGER DEFAULT 0")
    add_col("purchases", "due_date", "DATE")  # tanggal jatuh tempo pembayaran (data lama tetap NULL)
    add_col("purchase_returns", "total_carrying", "REAL DEFAULT 0")
    add_col("purchase_returns", "selisih", "REAL DEFAULT 0")

    # ─── Index performa (idempotent; aman dijalankan berulang) ─────────────────
    # SQLite TIDAK meng-index foreign key otomatis → tanpa ini, filter/join jadi
    # full table scan. Nama mengikuti konvensi SQLAlchemy "ix_<tabel>_<kolom>"
    # agar konsisten dengan index yang dibuat create_all pada instalasi baru.
    def add_index(name, table, cols):
        try:
            c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})")
        except Exception as e:
            print(f"  ⚠ Skip index {name}: {e}")

    # Penjualan (tabel tersibuk)
    add_index("ix_sales_branch_id", "sales", "branch_id")
    add_index("ix_sales_customer_id", "sales", "customer_id")
    add_index("ix_sales_salesperson_id", "sales", "salesperson_id")
    add_index("ix_sales_shift_id", "sales", "shift_id")
    add_index("ix_sales_created_by", "sales", "created_by")
    add_index("ix_sales_branch_date", "sales", "branch_id, date")
    add_index("ix_sale_items_sale_id", "sale_items", "sale_id")
    add_index("ix_sale_items_item_id", "sale_items", "item_id")
    add_index("ix_sale_returns_sale_id", "sale_returns", "sale_id")
    add_index("ix_sale_return_items_return_id", "sale_return_items", "return_id")
    add_index("ix_sale_return_items_item_id", "sale_return_items", "item_id")
    # Pembelian
    add_index("ix_purchases_branch_id", "purchases", "branch_id")
    add_index("ix_purchases_supplier_id", "purchases", "supplier_id")
    add_index("ix_purchases_target_branch_id", "purchases", "target_branch_id")
    add_index("ix_purchases_from_po_id", "purchases", "from_po_id")
    add_index("ix_purchases_created_by", "purchases", "created_by")
    add_index("ix_purchases_branch_date", "purchases", "branch_id, date")
    add_index("ix_purchase_items_purchase_id", "purchase_items", "purchase_id")
    add_index("ix_purchase_items_item_id", "purchase_items", "item_id")
    add_index("ix_purchase_returns_purchase_id", "purchase_returns", "purchase_id")
    add_index("ix_purchase_return_items_return_id", "purchase_return_items", "return_id")
    add_index("ix_purchase_return_items_item_id", "purchase_return_items", "item_id")
    # Item & master
    add_index("ix_items_category_id", "items", "category_id")
    add_index("ix_items_brand_id", "items", "brand_id")
    add_index("ix_items_unit_id", "items", "unit_id")
    add_index("ix_items_parent_item_id", "items", "parent_item_id")
    add_index("ix_items_barcode", "items", "barcode")
    add_index("ix_item_prices_item_id", "item_prices", "item_id")
    add_index("ix_item_group_discounts_item_id", "item_group_discounts", "item_id")
    add_index("ix_item_group_discounts_group_id", "item_group_discounts", "group_id")
    add_index("ix_item_supplier_supplier_id", "item_supplier", "supplier_id")
    add_index("ix_customers_group_id", "customers", "group_id")
    # Persediaan & gudang
    add_index("ix_stock_movements_branch_id", "stock_movements", "branch_id")
    add_index("ix_stock_movements_item_id", "stock_movements", "item_id")
    add_index("ix_stock_movements_item_date", "stock_movements", "item_id, date")
    add_index("ix_warehouse_stocks_warehouse_id", "warehouse_stocks", "warehouse_id")
    add_index("ix_warehouse_stocks_item_id", "warehouse_stocks", "item_id")
    add_index("ix_warehouse_stocks_wh_item", "warehouse_stocks", "warehouse_id, item_id")
    add_index("ix_warehouses_branch_id", "warehouses", "branch_id")
    add_index("ix_warehouse_transfers_from_wh", "warehouse_transfers", "from_warehouse_id")
    add_index("ix_warehouse_transfers_to_wh", "warehouse_transfers", "to_warehouse_id")
    add_index("ix_wh_transfer_items_transfer_id", "warehouse_transfer_items", "transfer_id")
    add_index("ix_wh_transfer_items_item_id", "warehouse_transfer_items", "item_id")
    # Akuntansi
    add_index("ix_cash_transactions_account_id", "cash_transactions", "account_id")
    add_index("ix_cash_transactions_branch_id", "cash_transactions", "branch_id")
    add_index("ix_journals_branch_id", "journals", "branch_id")
    add_index("ix_journals_created_by", "journals", "created_by")
    add_index("ix_jel_journal_id", "journal_entry_lines", "journal_id")
    add_index("ix_jel_debit_account_id", "journal_entry_lines", "debit_account_id")
    add_index("ix_jel_credit_account_id", "journal_entry_lines", "credit_account_id")
    add_index("ix_branch_deposits_branch_id", "branch_deposits", "branch_id")
    # Shift, audit, lisensi, print
    add_index("ix_shifts_user_id", "shifts", "user_id")
    add_index("ix_shifts_branch_id", "shifts", "branch_id")
    add_index("ix_audit_logs_user_id", "audit_logs", "user_id")
    add_index("ix_users_branch_id", "users", "branch_id")
    add_index("ix_login_attempts_username", "login_attempts", "username")
    add_index("ix_print_jobs_status", "print_jobs", "status")
    add_index("ix_license_payments_license_id", "license_payments", "license_id")
    # Konsinyasi
    add_index("ix_cons_in_items_cons_id", "consignment_in_items", "consignment_id")
    add_index("ix_cons_in_items_item_id", "consignment_in_items", "item_id")
    add_index("ix_cons_out_items_cons_id", "consignment_out_items", "consignment_id")
    add_index("ix_cons_out_items_item_id", "consignment_out_items", "item_id")
    # Surat jalan, tukar tambah, proyek, konversi
    add_index("ix_delivery_notes_sale_id", "delivery_notes", "sale_id")
    add_index("ix_delivery_notes_customer_id", "delivery_notes", "customer_id")
    add_index("ix_delivery_note_items_delivery_id", "delivery_note_items", "delivery_id")
    add_index("ix_trade_ins_customer_id", "trade_ins", "customer_id")
    add_index("ix_trade_ins_branch_id", "trade_ins", "branch_id")
    add_index("ix_ti_return_items_trade_in_id", "trade_in_return_items", "trade_in_id")
    add_index("ix_ti_new_items_trade_in_id", "trade_in_new_items", "trade_in_id")
    add_index("ix_unit_conversions_item_id", "unit_conversions", "item_id")

    conn.commit()
    conn.close()

run_migrations()


# ─── Seed Batch Pembukaan FIFO (idempotent) ────────────────────────────────────
def seed_opening_stock_batches():
    """Sekali jalan: buat 1 batch pembukaan FIFO untuk tiap (gudang, item) yang
    punya stok > 0 tapi BELUM punya lapisan batch sama sekali. unit_cost diambil
    dari item.buy_price (harga modal terakhir yang diketahui). received_date jauh
    di masa lalu agar stok lama ini keluar lebih dulu (FIFO). Idempotent: aman
    dijalankan tiap boot — (gudang,item) yang sudah punya batch dilewati."""
    from datetime import date as _date
    from sqlalchemy import inspect as _sa_inspect
    db = SessionLocal()
    try:
        if not _sa_inspect(engine).has_table("stock_batches"):
            return
        existing = {
            (wid, iid)
            for (wid, iid) in db.query(
                models.StockBatch.warehouse_id, models.StockBatch.item_id
            ).distinct().all()
        }
        buy_map = {
            iid: float(bp or 0)
            for (iid, bp) in db.query(models.Item.id, models.Item.buy_price).all()
        }
        rows = db.query(
            models.WarehouseStock.warehouse_id,
            models.WarehouseStock.item_id,
            models.WarehouseStock.stock,
        ).filter(models.WarehouseStock.stock > 0).all()

        created = 0
        seed_date = _date(2000, 1, 1)
        for wid, iid, stock in rows:
            if stock and stock > 0 and (wid, iid) not in existing:
                db.add(models.StockBatch(
                    item_id=iid,
                    warehouse_id=wid,
                    supplier_id=None,
                    purchase_item_id=None,
                    unit_cost=buy_map.get(iid, 0.0),
                    qty_received=float(stock),
                    qty_remaining=float(stock),
                    received_date=seed_date,
                ))
                created += 1
        if created:
            db.commit()
            print(f"✓ FIFO: {created} batch pembukaan dibuat dari stok berjalan.")
    except Exception as e:
        db.rollback()
        print(f"⚠️ Gagal seed batch pembukaan FIFO: {e}")
    finally:
        db.close()

seed_opening_stock_batches()

# ─── Background Tasks ─────────────────────────────────────────────────────────
def local_backup_loop():
    import shutil
    from datetime import datetime
    while True:
        time.sleep(86400)
        try:
            db_path = os.path.abspath("ipos.db")
            if os.path.exists(db_path):
                d = os.path.abspath("backups")
                os.makedirs(d, exist_ok=True)
                dest = os.path.join(d, f"ipos_{datetime.now().strftime('%Y%m%d_%H%M')}.db")
                shutil.copy2(db_path, dest)
                files = sorted(f for f in os.listdir(d) if f.endswith(".db") and not f.startswith("pre_"))
                while len(files) > 30:
                    os.remove(os.path.join(d, files.pop(0)))
                print(f"✓ Local backup: {os.path.basename(dest)}")
        except Exception as e:
            print(f"⚠ Backup failed: {e}")

def email_backup_scheduler():
    while True:
        time.sleep(60)
        try:
            from app.routes.email_backup import check_and_run_auto_backup
            check_and_run_auto_backup()
        except Exception:
            pass

threading.Thread(target=local_backup_loop, daemon=True).start()
threading.Thread(target=email_backup_scheduler, daemon=True).start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        cek_cabang = db.query(models.Branch).first()
        if not cek_cabang:
            print("⏳ Menyiapkan 'Toko Pusat' perdana...")
            cabang_utama = models.Branch(
                code="HQ-01",         
                name="Toko Pusat (HQ)", 
                address="Kantor Pusat",
                is_active=True
            )
            db.add(cabang_utama)
            db.commit()
            db.refresh(cabang_utama)
            
            gudang_pusat = models.Warehouse(
                code="WH-HQ-01",
                name="Gudang Utama Pusat",
                branch_id=cabang_utama.id,
                is_active=True,
                is_default=True
            )
            db.add(gudang_pusat)
            db.commit()
            
            users_tanpa_cabang = db.query(models.User).filter(models.User.active_branch_id == None).all()
            for user in users_tanpa_cabang:
                user.active_branch_id = cabang_utama.id
                if hasattr(user, 'branch_id'):
                    user.branch_id = cabang_utama.id
                
            db.commit()
            print(f"✅ 'Toko Pusat' dan Gudangnya berhasil dibuat. Akun Admin telah ditetapkan!")
            
        # 👇 TAMBAHAN: Pastikan akun Setoran ke Pusat & Cabang ada 👇
        codes_to_ensure = [
            ("3-2300", "Setoran ke Pusat", "equity", "capital", "credit"),
            ("3-2400", "Setoran dari Cabang", "equity", "capital", "credit"),
            ("1-1600", "Saldo di Supplier", "asset", "current_asset", "debit"),
            ("2-1300", "Saldo di Customer", "liability", "current_liability", "credit"),
            ("5-2000", "Beban Pajak", "expense", "operating", "debit")
        ]
        for code, name, acc_type, subtype, balance in codes_to_ensure:
            acc = db.query(models.Account).filter(models.Account.code == code).first()
            if not acc:
                db.add(models.Account(
                    code=code, 
                    name=name, 
                    type=acc_type, 
                    subtype=subtype, 
                    normal_balance=balance
                ))
                db.commit()
                print(f"✅ Akun '{code} {name}' berhasil disiapkan.")

        # 👇 TAMBAHAN SEED DATA MASTER (Agar tidak error saat awal) 👇
        if not db.query(models.Category).first():
            db.add(models.Category(name="Umum", description="Kategori Default"))
            print("📦 ✓ Seed Category: Umum")
        if not db.query(models.Brand).first():
            db.add(models.Brand(name="Tanpa Merek", description="Default Brand"))
            print("📦 ✓ Seed Brand: Tanpa Merek")
        if not db.query(models.Unit).first():
            db.add(models.Unit(name="Pcs", abbreviation="pcs"))
            print("📦 ✓ Seed Unit: Pcs")
        db.commit()

        # ── Guard keamanan: password default + akses publik (Funnel) ──────────
        # Di mode publik, kredensial bawaan bisa ditebak SEKALI coba dari internet;
        # throttle login (429) tidak menolong bila tebakan pertama sudah benar.
        # CATATAN: sengaja TIDAK pakai messagebox di sini — lifespan jalan di thread
        # server (bukan main thread); Tk tidak thread-safe & modal akan memblok startup.
        # Peringatan diarahkan ke stdout → error_log.txt (mode frozen windowed).
        if settings.TAILSCALE_PUBLIC:
            lemah = []
            admin_u = db.query(models.User).filter(models.User.username == "admin").first()
            if admin_u and verify_password("admin123", admin_u.hashed_password):
                lemah.append("admin/admin123")
            fieter_u = db.query(models.User).filter(models.User.username == "Fieter").first()
            if fieter_u and verify_password("Fieter098", fieter_u.hashed_password):
                lemah.append("Fieter/Fieter098 (vendor)")
            if lemah:
                garis = "!" * 70
                print(garis)
                print("⚠️  BAHAYA: AKSES PUBLIK (Tailscale Funnel) AKTIF DENGAN PASSWORD DEFAULT")
                print(f"    Akun masih memakai password bawaan: {', '.join(lemah)}")
                print("    Siapa pun yang menemukan URL bisa login. GANTI PASSWORD SEKARANG,")
                print("    atau matikan Funnel (TAILSCALE_PUBLIC=false) sampai password diganti.")
                print(garis)

    except Exception as e:
        print(f"⚠️ Gagal inisialisasi data awal: {e}")
    finally:
        db.close()
        
    yield 
    print("Server mematikan proses...")

# Saat mode Funnel (TAILSCALE_PUBLIC=True) server terbuka ke internet publik.
# Tutup dokumentasi interaktif agar peta API (endpoint, schema, contoh) tidak
# bocor ke publik. Di mode privat (serve/tailnet) docs tetap aktif untuk debug.
_public = settings.TAILSCALE_PUBLIC
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=None if _public else "/docs",
    redoc_url=None if _public else "/redoc",
    openapi_url=None if _public else "/openapi.json",
)

# CORS: auth memakai Bearer token di header Authorization (BUKAN cookie), maka
# allow_credentials=False — ini menghapus kombinasi berbahaya ["*"] + credentials.
# Origin dibaca dari settings.ALLOWED_ORIGINS (pisah koma). Tanpa credentials,
# header Authorization tetap berfungsi lintas-origin meski origin "*".
_allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🗜️ Kompresi GZip — pangkas ukuran transfer JSON/JS/CSS/HTML (sangat membantu via Tailscale).
# minimum_size=1024 agar respons kecil tidak ikut dikompres (overhead tak sepadan).
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(items.router, prefix="/api/items", tags=["Items"])
app.include_router(customers.router, prefix="/api/customers", tags=["Customers"])
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["Suppliers"])
app.include_router(purchases.router, prefix="/api/purchases", tags=["Purchases"])
app.include_router(sales.router, prefix="/api/sales", tags=["Sales"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
app.include_router(accounting.router, prefix="/api/accounting", tags=["Accounting"])
app.include_router(consignment.router, prefix="/api/consignment", tags=["Consignment"])
app.include_router(shifts.router, prefix="/api/shifts", tags=["Shifts"])
app.include_router(returns.router, prefix="/api/returns", tags=["Returns"])
app.include_router(backup.router, prefix="/api/backup", tags=["Backup"])
app.include_router(ai_advisor.router, prefix="/api/ai", tags=["AI Advisor"])
app.include_router(email_backup.router, prefix="/api/email-backup", tags=["Email Backup"])
app.include_router(updater.router, prefix="/api/updater", tags=["Updater"])
app.include_router(license.router, prefix="/api/license", tags=["License"])
app.include_router(warehouse.router, prefix="/api/warehouses", tags=["Warehouse"])
app.include_router(assembly.router, prefix="/api/assembly", tags=["Assembly"])
app.include_router(notification.router, prefix="/api/notification", tags=["Notification"])
app.include_router(discounts.router, prefix="/api/discounts", tags=["Discounts"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["Onboarding"])
app.include_router(unit_conversion.router, prefix="/api/unit-conversion", tags=["Unit Conversion"])
app.include_router(barcode_gen.router, prefix="/api/barcode", tags=["Barcode"])
app.include_router(delivery.router, prefix="/api/delivery", tags=["Delivery"])
app.include_router(trade_in.router, prefix="/api/trade-in", tags=["Trade In"])
app.include_router(ai_bangunan.router, prefix="/api/ai-bangunan", tags=["AI Bangunan"])
app.include_router(branches.router, prefix="/api/branches", tags=["Branches"])
app.include_router(employees.router, prefix="/api/employees", tags=["Employees"])
app.include_router(print_queue.router, prefix="/api/print", tags=["Print Queue"])
app.include_router(sticker_gen.router, prefix="/api/sticker", tags=["Sticker Generation"])
app.include_router(sticker_gen.router, prefix="/api/stiker", tags=["Sticker Generation (Legacy)"])
app.include_router(po.router, prefix="/api/po", tags=["Purchase Orders / Requests"])



if getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

# Saat frozen (exe) pakai hasil build (frontend-dist) bila ada. Saat dev (jalan dari sumber)
# tetap pakai folder sumber agar edit langsung kebaca — kecuali FPOS_USE_BUILD=1 untuk uji build.
_BUILT_FRONTEND = ROOT_DIR / "frontend-dist"
_USE_BUILT = _BUILT_FRONTEND.exists() and (
    getattr(sys, "frozen", False) or os.getenv("FPOS_USE_BUILD") == "1"
)
FRONTEND_DIR = _BUILT_FRONTEND if _USE_BUILT else (ROOT_DIR / "frontend")
# Aset di frontend-dist ber-hash (immutable) → boleh cache 1 tahun; sumber dev cache pendek (1 jam).
_ASSET_MAX_AGE = 31536000 if _USE_BUILT else 3600
_ASSET_IMMUTABLE = _USE_BUILT
MANIFEST_PATH = FRONTEND_DIR / "manifest.json"

class CachedStaticFiles(StaticFiles):
    """StaticFiles + header Cache-Control agar aset (js/css/gambar) tidak di-download ulang
    setiap kunjungan. Hemat round-trip — terasa banget via Tailscale.
    Catatan: bila disajikan dari frontend-dist, nama file sudah ber-hash → aman pakai
    max_age panjang + immutable. Dari sumber (dev) max_age moderat agar update cepat kebaca."""
    def __init__(self, *args, max_age: int = 3600, immutable: bool = False, **kwargs):
        self.max_age = max_age
        self.immutable = immutable
        super().__init__(*args, **kwargs)

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Hanya cache respons sukses (200/304) — jangan cache error seperti 404.
        if response.status_code < 400:
            cc = f"public, max-age={self.max_age}"
            if self.immutable:
                cc += ", immutable"
            response.headers["Cache-Control"] = cc
        return response


UPLOADS_DIR = ROOT_DIR / "uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)
# Gambar upload jarang berubah → boleh cache lebih lama (1 minggu).
app.mount("/uploads", CachedStaticFiles(directory=str(UPLOADS_DIR), max_age=604800), name="uploads")

@app.get("/manifest.json", tags=["PWA"])
def get_manifest():
    if MANIFEST_PATH.exists():
        return FileResponse(str(MANIFEST_PATH))
    return {"error": "Manifest not found"}

@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}

if FRONTEND_DIR.exists():
    if (FRONTEND_DIR / "js").exists():
        app.mount("/js", CachedStaticFiles(directory=str(FRONTEND_DIR / "js"), max_age=_ASSET_MAX_AGE, immutable=_ASSET_IMMUTABLE), name="js")
    if (FRONTEND_DIR / "css").exists():
        app.mount("/css", CachedStaticFiles(directory=str(FRONTEND_DIR / "css"), max_age=_ASSET_MAX_AGE, immutable=_ASSET_IMMUTABLE), name="css")

    # Halaman HTML harus selalu divalidasi ulang ke server (ETag tetap → 304 bila tak berubah),
    # supaya hasil edit frontend tidak tertutup cache lama webview/browser.
    _HTML_NO_CACHE = {"Cache-Control": "no-cache"}

    @app.get("/")
    async def root():
        return FileResponse(str(FRONTEND_DIR / "index.html"), headers=_HTML_NO_CACHE)

    HTML_PAGES = [
        "index", "dashboard", "pos", "pos_2", "sales", "returns",
        "customers", "suppliers", "inventory", "reports",
        "accounting", "shifts", "konsinyasi", "ai_advisor",
        "settings", "warehouse", "assembly", "discounts", "onboarding",
        "unit_conversion", "delivery", "trade_in", "ai_bangunan", "branches", "users", "barcode", "po", "setor", "setoran", "pembayaran",
    ]

    for page in HTML_PAGES:
        html_file = FRONTEND_DIR / f"{page}.html"
        if html_file.exists():
            def make_handler(p):
                async def handler():
                    return FileResponse(str(FRONTEND_DIR / f"{p}.html"), headers=_HTML_NO_CACHE)
                handler.__name__ = f"page_{p}"
                return handler
            app.add_api_route(f"/{page}", make_handler(page), methods=["GET"])
            app.add_api_route(f"/{page}.html", make_handler(page), methods=["GET"])

    # Serve pages in item subdirectory
    ITEM_PAGES = ["dashboard", "satuan", "levelHarga", "levelJumlah", "items", "popUp", "kategori", "units", "merek", "potonganHargaJual"]
    for page in ITEM_PAGES:
        html_file = FRONTEND_DIR / "item" / f"{page}.html"
        if html_file.exists():
            def make_item_handler(p):
                async def handler():
                    return FileResponse(str(FRONTEND_DIR / "item" / f"{p}.html"), headers=_HTML_NO_CACHE)
                handler.__name__ = f"page_item_{p}"
                return handler
            app.add_api_route(f"/item/{page}", make_item_handler(page), methods=["GET"])
            app.add_api_route(f"/item/{page}.html", make_item_handler(page), methods=["GET"])

    # Serve pages in purchase subdirectory
    PURCHASE_PAGES = ["purchases", "catat-pembelian", "detail_item"]
    for page in PURCHASE_PAGES:
        html_file = FRONTEND_DIR / "purchase" / f"{page}.html"
        if html_file.exists():
            def make_purchase_handler(p):
                async def handler():
                    return FileResponse(str(FRONTEND_DIR / "purchase" / f"{p}.html"), headers=_HTML_NO_CACHE)
                handler.__name__ = f"page_purchase_{p}"
                return handler
            app.add_api_route(f"/purchase/{page}", make_purchase_handler(page), methods=["GET"])
            app.add_api_route(f"/purchase/{page}.html", make_purchase_handler(page), methods=["GET"])
            # alias tanpa prefix subdirektori (misal /purchases → purchase/purchases.html)
            app.add_api_route(f"/{page}", make_purchase_handler(page), methods=["GET"])
            app.add_api_route(f"/{page}.html", make_purchase_handler(page), methods=["GET"])

    # Serve pages in supplier subdirectory
    SUPPLIER_PAGES = ["dashboard", "tambahSuplier"]
    for page in SUPPLIER_PAGES:
        html_file = FRONTEND_DIR / "supplier" / f"{page}.html"
        if html_file.exists():
            def make_supplier_handler(p):
                async def handler():
                    return FileResponse(str(FRONTEND_DIR / "supplier" / f"{p}.html"), headers=_HTML_NO_CACHE)
                handler.__name__ = f"page_supplier_{p}"
                return handler
            app.add_api_route(f"/supplier/{page}", make_supplier_handler(page), methods=["GET"])
            app.add_api_route(f"/supplier/{page}.html", make_supplier_handler(page), methods=["GET"])

def cari_tailscale_exe() -> str | None:
    kandidat = [
        r"C:\Program Files\Tailscale\tailscale.exe",
        r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tailscale\tailscale.exe"),
    ]
    for path in kandidat:
        if os.path.exists(path):
            return path
    return None

def run_cmd(args: list[str]) -> tuple[int, str, str]:
    ts_exe = cari_tailscale_exe()
    if args and args[0] == "tailscale" and ts_exe:
        args = [ts_exe] + args[1:]
    try:
        result = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=10
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def cek_tailscale_status() -> bool:
    code, out, _ = run_cmd(["tailscale", "status"])
    return code == 0

def reset_serve(publik: bool):
    run_cmd(["tailscale", "funnel" if publik else "serve", "reset"])

def jalankan_tailscale(port: int, publik: bool) -> bool:
    ts_exe = cari_tailscale_exe()
    if not ts_exe:
        return False
    
    if not cek_tailscale_status():
        return False
    
    reset_serve(publik)
    time.sleep(1)
    
    args = ["tailscale", "funnel" if publik else "serve", "--bg"]
    if not publik:
        args.append(f"http://localhost:{port}")
    else:
        args.append(str(port))
        
    code, out, err = run_cmd(args)
    return code == 0 or "already exists" in err


# 👇 SENSOR POP-UP AUTO-START (Hanya jalan 1x) 👇
def cek_dan_tanya_autostart():
    flag_file = os.path.join(ROOT_DIR, ".autostart_configured")
    if not os.path.exists(flag_file):
        root = tk.Tk()
        root.attributes('-topmost', True) # Pastikan popup muncul di depan
        root.withdraw()
        
        ans = messagebox.askyesno(
            "Auto-Start Windows", 
            "Apakah Anda ingin Sistem Utama FPOS ini otomatis terbuka setiap kali komputer dinyalakan?",
            parent=root
        )
        
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            exe_path = os.path.abspath(sys.argv[0])
            
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            if ans:
                winreg.SetValueEx(key, "EvaStore_FPOS", 0, winreg.REG_SZ, f'"{exe_path}"')
            else:
                try:
                    winreg.DeleteValue(key, "EvaStore_FPOS")
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
            
        # Tandai bahwa sudah pernah ditanya
        with open(flag_file, "w") as f:
            f.write("done")


if __name__ == "__main__":
    import multiprocessing
    import socket
    import webview
    import ctypes

    multiprocessing.freeze_support()

    if sys.stdout is None or sys.stderr is None:
        log_path = os.path.join(ROOT_DIR, "error_log.txt")
        log_file = open(log_path, "w", encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file

    # Jalankan sensor pop-up autostart sebelum server menyala
    cek_dan_tanya_autostart()

    PORT = 8010
    # AMAN secara default: tailscale "serve" (hanya tailnet). Set TAILSCALE_PUBLIC=true
    # di .env HANYA jika cabang berada di luar tailnet dan benar-benar butuh akses publik.
    PUBLIK = settings.TAILSCALE_PUBLIC

    def is_server_running(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0

    def jalankan_server():
        jalankan_tailscale(PORT, PUBLIK)
        # Bind ke localhost saja. Tailscale serve/funnel & WebView lokal mengakses
        # lewat 127.0.0.1, jadi app tidak perlu terekspos di seluruh interface (0.0.0.0).
        #
        # ⚠️ JANGAN tambahkan workers=N / gunicorn fork. SQLite = penulis tunggal,
        # multi-proses justru memperburuk kontensi lock; selain itu lifespan/seed +
        # scheduler backup (thread daemon) akan jalan ganda. Tetap 1 proses: handler
        # `def` (sinkron) sudah dijalankan FastAPI di threadpool (~40 thread) → cukup
        # untuk ≤15 user konkuren tanpa membekukan event loop.
        uvicorn.run(app, host="127.0.0.1", port=PORT)

    def maximize_benar(window):
        time.sleep(0.5) 
        hwnd = ctypes.windll.user32.FindWindowW(None, "Eva Store")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 3)

    if is_server_running(PORT):
        window = webview.create_window(
            title="Eva Store",
            url=f"http://127.0.0.1:{PORT}",
            fullscreen=False, 
            text_select=False,
            confirm_close=True,
        )
        webview.start(maximize_benar, window)
    else:
        server_thread = threading.Thread(target=jalankan_server, daemon=True)
        server_thread.start()

        for _ in range(20):  
            if is_server_running(PORT):
                break
            time.sleep(0.5)

        window = webview.create_window(
            title="Eva Store",
            url=f"http://127.0.0.1:{PORT}",
            fullscreen=False, 
            text_select=False,
            confirm_close=True,
        )
        webview.start(maximize_benar, window)
