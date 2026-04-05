import subprocess
import os, threading, time, sys
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func
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
# ─── Seed admin & Super Admin Fieter ──────────────────────────────────────────
def seed_admin():
    db = SessionLocal()
    try:
        # 1. Buat/Reset Akun Admin Biasa
        admin = db.query(models.User).filter(models.User.username == "admin").first()
        if not admin:
            db.add(models.User(
                username="admin", full_name="Administrator",
                hashed_password=get_password_hash("admin123"),
                role="admin", is_active=True
            ))
        else:
            admin.hashed_password = get_password_hash("admin123")
            
        # 2. Buat/Reset Akun DEWA (Fieter)
        fieter = db.query(models.User).filter(models.User.username == "Fieter").first()
        if not fieter:
            db.add(models.User(
                username="Fieter", full_name="Super Admin",
                hashed_password=get_password_hash("Fieter098"),
                role="admin", is_active=True
            ))
            print("👑 ✓ Akun Fieter BERHASIL DIBUAT (Fieter / Fieter098)")
        else:
            fieter.hashed_password = get_password_hash("Fieter098")
            print("👑 ✓ Akun Fieter SUDAH ADA (Password di-reset ke Fieter098)")
            
        db.commit()
    except Exception as e:
        print(f"⚠️ Gagal membuat akun otomatis: {e}")
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
    add_col("purchases", "created_by", "INTEGER")
    add_col("purchases", "paid", "REAL DEFAULT 0")
    add_col("purchases", "status", "TEXT DEFAULT 'unpaid'")
    add_col("warehouses", "is_default", "INTEGER DEFAULT 0")
    add_col("unit_conversions", "buy_price",  "REAL DEFAULT 0")
    add_col("unit_conversions", "sell_price", "REAL DEFAULT 0")
    add_col("unit_conversions", "is_active",  "INTEGER DEFAULT 1")
    add_col("delivery_notes", "signed_at",      "TEXT")
    add_col("delivery_notes", "recipient_name", "TEXT")
    add_col("licenses", "max_users",    "INTEGER DEFAULT 3")
    add_col("licenses", "owner_name",   "TEXT")
    add_col("licenses", "owner_email",  "TEXT")
    add_col("licenses", "billing_status", "TEXT DEFAULT 'ok'")
    add_col("licenses", "billing_message", "TEXT DEFAULT 'Aplikasi berjalan normal.'")

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
        except Exception:
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


# ==============================================================================
# 🖨️ FITUR AUTO-DETECT PRINTER & CETAK STRUK (WIN32RAW)
# ==============================================================================
def cari_printer_kasir_otomatis():
    """Mencari nama printer kasir secara otomatis dengan membuang printer virtual."""
    try:
        import win32print
    except ImportError:
        return "Microsoft Print to PDF"

    try:
        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL)
        printer_names = [p[2] for p in printers]

        virtual_keywords = ["pdf", "fax", "xps", "onenote", "snagit", "webex", "send to", "microsoft"]
        printer_fisik = [nama for nama in printer_names if not any(vk in nama.lower() for vk in virtual_keywords)]

        pos_keywords = ["pos", "thermal", "receipt", "58", "80", "epson", "xprinter", "xp-", "tm-", "printer"]
        for nama in printer_fisik:
            if any(pk in nama.lower() for pk in pos_keywords):
                return nama

        if len(printer_fisik) > 0:
            return printer_fisik[0]

        return win32print.GetDefaultPrinter()
    except Exception:
        return "Microsoft Print to PDF"

@app.post("/api/sales/print")
async def print_receipt_api(request: Request):
    try:
        data = await request.json()
        sale = data.get("sale", {})
        settings_toko = data.get("settings", {})

        nama_toko = settings_toko.get("storeName", "Eva Store")
        footer = settings_toko.get("storeFooter", "Terima kasih atas kunjungan Anda!")

        NAMA_PRINTER_WINDOWS = cari_printer_kasir_otomatis()
        print(f"🖨️ Target Printer: {NAMA_PRINTER_WINDOWS}")

        from escpos.printer import Win32Raw
        p = Win32Raw(NAMA_PRINTER_WINDOWS)

        p.set(align='center', font='a', width=2, height=2)
        p.text(f"{nama_toko}\n")
        p.set(align='center', font='a', width=1, height=1)
        p.text("================================\n")

        p.set(align='left')
        for item in sale.get('items', []):
            nama_barang = item.get('item', {}).get('name', 'Barang')[:15]
            qty = item.get('qty', 1)
            total = item.get('total', 0)
            baris = f"{nama_barang:<15} x{qty:<3} Rp{total:>8}\n"
            p.text(baris)

        p.text("--------------------------------\n")

        p.set(align='right')
        p.text(f"TOTAL   : Rp {sale.get('total', 0)}\n")
        p.text(f"DIBAYAR : Rp {sale.get('paid', 0)}\n")
        p.text(f"KEMBALI : Rp {sale.get('change', 0)}\n")

        p.set(align='center')
        p.text("--------------------------------\n")
        p.text(f"{footer}\n")
        p.text("\n\n\n")

        try:
            p.cut()
        except:
            pass

        return {"status": "success", "message": f"Tercetak otomatis di: {NAMA_PRINTER_WINDOWS}"}

    except Exception as e:
        print(f"PRINT ERROR: {e}")
        return JSONResponse(status_code=500, content={"detail": f"Gagal mencetak: {str(e)}"})
# ==============================================================================


# ==============================================================================
# ─── SENSOR PENCARI FOLDER PINTAR ─────────────────────────────────────────────
# ==============================================================================
if getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

FRONTEND_DIR = ROOT_DIR / "frontend"
MANIFEST_PATH = FRONTEND_DIR / "manifest.json"


# 👇 TAMBAHKAN 3 BARIS INI UNTUK FOLDER UPLOADS 👇
UPLOADS_DIR = ROOT_DIR / "uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True) # Buat foldernya otomatis jika belum ada
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
# 👆 SAMPAI SINI 👆

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
else:
    print(f"⚠ WARNING: Folder Frontend tidak ditemukan di {FRONTEND_DIR}!")

# ==============================================================================
# ─── HELPER: TAILSCALE EXECUTION ──────────────────────────────────────────────
# ==============================================================================
def cari_tailscale_exe() -> str | None:
    """Cari tailscale.exe langsung, tidak andalkan PATH"""
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
    # Ganti 'tailscale' dengan path penuh jika ketemu
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
    return code == 0 and "desktop-b0e6dv6" in out

def reset_serve(publik: bool):
    run_cmd(["tailscale", "funnel" if publik else "serve", "reset"])

def jalankan_tailscale(port: int, publik: bool) -> bool:
    ts_exe = cari_tailscale_exe()
    if not ts_exe:
        print("TAILSCALE: tailscale.exe tidak ditemukan!")
        return False
    
    if not cek_tailscale_status():
        print("TAILSCALE: Status tidak connected. Pastikan Tailscale menyala.")
        return False
    
    reset_serve(publik)
    time.sleep(1)
    
    args = ["tailscale", "funnel" if publik else "serve", "--bg"]
    if not publik:
        args.append(f"http://localhost:{port}")
    else:
        args.append(str(port))
        
    code, out, err = run_cmd(args)
    print(f"TAILSCALE: code={code} out={out} err={err}")
    return code == 0 or "already exists" in err


# ==============================================================================
# ─── MAIN EXECUTION ───────────────────────────────────────────────────────────
# ==============================================================================
if __name__ == "__main__":
    import multiprocessing
    import socket
    import webview
    import ctypes

    multiprocessing.freeze_support()

    # ─── OBAT ANTI CRASH UNTUK MODE --noconsole ───
    if sys.stdout is None or sys.stderr is None:
        log_path = os.path.join(ROOT_DIR, "error_log.txt")
        log_file = open(log_path, "w", encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file

    PORT = 8010
    DOMAIN_TS = "desktop-b0e6dv6.balinese-alhena.ts.net"
    PUBLIK = True

    def is_server_running(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0

    def jalankan_server():
        # Jalankan tailscale sebelum uvicorn start di background thread
        jalankan_tailscale(PORT, PUBLIK)
        uvicorn.run(app, host="0.0.0.0", port=PORT)

    def maximize_benar(window):
        # Tunggu UI render sebentar
        time.sleep(0.5) 
        hwnd = ctypes.windll.user32.FindWindowW(None, "Eva Store")
        if hwnd:
            # SW_MAXIMIZE = 3
            ctypes.windll.user32.ShowWindow(hwnd, 3)

    if is_server_running(PORT):
        # Server sudah jalan, langsung buka jendela baru
        window = webview.create_window(
            title="Eva Store",
            url=f"http://127.0.0.1:{PORT}",
            fullscreen=False, # Set false, kita pakai maximize_benar
            text_select=False,
            confirm_close=True,
        )
        webview.start(maximize_benar, window)
    else:
        # Jalankan server & tailscale di background thread
        server_thread = threading.Thread(target=jalankan_server, daemon=True)
        server_thread.start()

        # Tunggu server siap merespon
        print("Menunggu server siap...")
        for _ in range(20):  # maksimal 10 detik
            if is_server_running(PORT):
                break
            time.sleep(0.5)

        print("Server siap! Membuka aplikasi...")

        # Buka jendela — ini blocking, jadi taruh paling akhir
        window = webview.create_window(
            title="Eva Store",
            url=f"http://127.0.0.1:{PORT}",
            fullscreen=False, # Set false, kita pakai maximize_benar
            text_select=False,
            confirm_close=True,
        )
        webview.start(maximize_benar, window)