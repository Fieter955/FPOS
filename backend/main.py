import subprocess as std_subprocess  # <--- FIX 1: Import subprocess bawaan OS
import os, threading, time
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from app.database import Base, engine, SessionLocal
from app.auth import get_password_hash
from app.config import settings
from app import models
from app.routes import (
    auth, items, customers, suppliers, purchases, sales,
    inventory, reports, accounting, consignment,
    shifts, returns, backup, ai_advisor,
    email_backup, updater,
    license, warehouse, assembly, notification, discounts, onboarding,
    unit_conversion, barcode_gen, delivery, trade_in, ai_bangunan
)

# ─── Create all DB tables ──────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─── Seed admin ───────────────────────────────────────────────────────────────
def seed_admin():
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == "admin").first()
        if not user:
            db.add(models.User(
                username="admin", full_name="Administrator",
                hashed_password=get_password_hash("admin123"),
                role="admin", is_active=True
            ))
            db.commit()
            print("✓ Admin dibuat: admin / admin123")
        else:
            user.hashed_password = get_password_hash("admin123")
            db.commit()
            print("✓ Password admin berhasil di-reset paksa ke: admin123")
    finally:
        db.close()

seed_admin()

# ─── Safe Migration ───────────────────────────────────────────────────────────
def run_migrations():
    """Tambah kolom baru ke tabel yang sudah ada tanpa hapus data."""
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

    # Patch untuk tabel-tabel utama
    add_col("customers", "credit_limit", "REAL DEFAULT 0")
    add_col("customers", "points",       "REAL DEFAULT 0")
    add_col("suppliers", "credit_limit", "REAL DEFAULT 0")
    add_col("items", "barcode",     "TEXT")
    add_col("items", "description", "TEXT")
    add_col("items", "updated_at",  "TEXT")
    add_col("sales", "created_by", "INTEGER")
    add_col("sales", "change",     "REAL DEFAULT 0")
    add_col("sales", "salesperson_id",  "INTEGER") 
    add_col("sales", "shift_id",       "INTEGER")
    add_col("sales", "notes",          "TEXT")
    add_col("customers", "loyalty_points", "REAL DEFAULT 0")
    add_col("customers", "group_id",       "INTEGER")
    add_col("customers", "address",        "TEXT")
    add_col("customers", "email",          "TEXT")
    
    # ---- INI FIX UNTUK ERROR ANDA ----
    add_col("purchases", "created_by", "INTEGER")
    add_col("purchases", "paid", "REAL DEFAULT 0")
    add_col("purchases", "status", "TEXT DEFAULT 'unpaid'")
    # ----------------------------------

    add_col("warehouses", "is_default", "INTEGER DEFAULT 0")
    add_col("unit_conversions", "buy_price",  "REAL DEFAULT 0")
    add_col("unit_conversions", "sell_price", "REAL DEFAULT 0")
    add_col("unit_conversions", "is_active",  "INTEGER DEFAULT 1")
    add_col("delivery_notes", "signed_at",      "TEXT")
    add_col("delivery_notes", "recipient_name", "TEXT")
    add_col("licenses", "max_users",    "INTEGER DEFAULT 3")
    add_col("licenses", "owner_name",   "TEXT")
    add_col("licenses", "owner_email",  "TEXT")

    conn.commit()
    conn.close()

run_migrations()


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
        except Exception as e:
            pass

threading.Thread(target=local_backup_loop, daemon=True).start()
threading.Thread(target=email_backup_scheduler, daemon=True).start()


# ─── FastAPI Setup ────────────────────────────────────────────────────────────
app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Register Routes ──────────────────────────────────────────────────────────
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
app.include_router(warehouse.router, prefix="/api/warehouse", tags=["Warehouse"])
app.include_router(assembly.router, prefix="/api/assembly", tags=["Assembly"])
app.include_router(notification.router, prefix="/api/notification", tags=["Notification"])
app.include_router(discounts.router, prefix="/api/discounts", tags=["Discounts"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["Onboarding"])
app.include_router(unit_conversion.router, prefix="/api/unit-conversion", tags=["Unit Conversion"])
app.include_router(barcode_gen.router, prefix="/api/barcode", tags=["Barcode"])
app.include_router(delivery.router, prefix="/api/delivery", tags=["Delivery"])
app.include_router(trade_in.router, prefix="/api/trade-in", tags=["Trade In"])
app.include_router(ai_bangunan.router, prefix="/api/ai-bangunan", tags=["AI Bangunan"])

# ─── PWA & Manifest ───────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(BASE_DIR, "..", "frontend", "manifest.json")

@app.get("/manifest.json", tags=["PWA"])
def get_manifest():
    return FileResponse(MANIFEST_PATH)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION
    }

# ─── Serve Frontend (HTML, JS, CSS) ───────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")
    
    if (FRONTEND_DIR / "css").exists():
        app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")

    @app.get("/")
    async def root():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    HTML_PAGES = [
        "index", "dashboard", "pos", "sales", "purchases", "returns",
        "items", "customers", "suppliers", "inventory", "reports",
        "accounting", "shifts", "konsinyasi", "ai_advisor",
        "settings", "warehouse", "assembly", "discounts", "onboarding",
        "unit_conversion", "delivery", "trade_in", "ai_bangunan"
    ]

    for page in HTML_PAGES:
        html_file = FRONTEND_DIR / f"{page}.html"
        if html_file.exists():
            def make_handler(p):
                async def handler():
                    return FileResponse(str(FRONTEND_DIR / f"{p}.html"))
                handler.__name__ = f"page_{p}"
                return handler
            app.add_api_route(f"/{page}", make_handler(page), methods=["GET"])
            app.add_api_route(f"/{page}.html", make_handler(page), methods=["GET"])

# ─── Eksekusi Server & Caddy Reverse Proxy ────────────────────────────────────
if __name__ == "__main__":
    # GANTI INI sesuai port yang diminta Tunnel VPN kamu di laptop
    port_target_vpn = 8008 

    print(f"🔥 Menjalankan server di port {port_target_vpn}...")
    # host wajib 0.0.0.0 agar bisa menerima trafik dari Tunnel
    uvicorn.run(app, host="0.0.0.0", port=port_target_vpn)